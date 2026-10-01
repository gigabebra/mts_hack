"""Reject plausible IPv4 leftovers in otherwise healthy IPv6 snapshots."""
import copy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts'))
from ipv6_checks import validate_snapshot
from ipv6_transport import ipv4_flows, validate as validate_transport
from stacklib import http_url, lock_document


ENV = {'node_name': 'fixture', 'node_ip': '2001:db8:20::10',
       'pod_cidr': 'fd4d:5453:1000:100::/56', 'service_cidr': 'fd4d:5453:1000:200::/112'}
SNAPSHOT = {
    'api_server': 'https://[2001:db8:20::10]:6443',
    'nodes': [{'metadata': {'name': 'fixture'}, 'spec': {'podCIDRs': ['fd4d:5453:1000:100::/64']},
               'status': {'addresses': [{'type': 'InternalIP', 'address': ENV['node_ip']}]}}],
    'pods': [{'spec': {}, 'status': {'phase': 'Running', 'podIPs': [{'ip': 'fd4d:5453:1000:100::1'}]}}],
    'services': [{'spec': {'ipFamilies': ['IPv6'], 'ipFamilyPolicy': 'SingleStack',
                          'clusterIPs': ['fd4d:5453:1000:200::1']}}],
    'endpoint_slices': [{'addressType': 'IPv6', 'endpoints': [{'addresses': ['fd4d:5453:1000:100::1']}]}],
    'calico_pools': [{'spec': {'cidr': ENV['pod_cidr'], 'vxlanMode': 'Always'}}],
}


class IPv6Contract(unittest.TestCase):
    def test_transport_rejects_hidden_ipv4_health_and_mapped_peers(self):
        for connection in (
            'tcp TIME-WAIT 0 0 127.0.0.1:43122 127.0.0.1:8080',
            'tcp ESTAB 0 0 [::ffff:127.0.0.1]:43122 [::ffff:127.0.0.1]:8080',
        ):
            self.assertEqual(len(ipv4_flows(connection)), 1)
        self.assertEqual(ipv4_flows('tcp ESTAB 0 0 [::1]:43122 [::1]:8080'), [])
        self.assertEqual(ipv4_flows('tcp LISTEN 0 10 0.0.0.0:8080 0.0.0.0:*'), [])
        self.assertEqual(ipv4_flows('udp UNCONN 0 0 192.0.2.1:68 0.0.0.0:*', host=True), [])
        self.assertEqual(ipv4_flows('udp UNCONN 0 0 127.0.0.53%lo:53 0.0.0.0:*', host=True), [])
        self.assertEqual(len(ipv4_flows('tcp ESTAB 0 0 192.0.2.1:40000 1.1.1.1:443', host=True)), 1)
        self.assertEqual(len(ipv4_flows('tcp ESTAB 0 0 192.0.2.1:40000 192.168.1.10:443', host=True)), 1)
        self.assertEqual(len(ipv4_flows('tcp ESTAB 0 0 192.0.2.1:40000 198.51.100.1:6443', host=True)), 1)

    def test_public_https_is_not_automatically_a_download(self):
        flow = 'tcp ESTAB 0 0 192.0.2.1:49080 1.1.1.1:443 users:(("calico-node",pid=42,fd=34))'
        container = {'exe': '/usr/bin/calico-node', 'cgroup': '0::/kubepods.slice/pod-fixture',
                     'host_mount_namespace': False}
        self.assertEqual(len(ipv4_flows(flow, host=True, processes={'42': container})), 1)
        downloader = {'exe': '/usr/local/bin/containerd', 'cgroup': '0::/system.slice/containerd.service',
                      'host_mount_namespace': True}
        self.assertEqual(ipv4_flows(flow, host=True, processes={'42': downloader}), [])
        # Neither a forged comm/executable nor a hostNetwork container qualifies.
        for changes in ({'cgroup': '0::/kubepods.slice/pod-fixture'}, {'host_mount_namespace': False},
                        {'exe': '/usr/bin/curl'}, {'exe': '/usr/bin/python3'}, {'cgroup': ''}):
            with self.subTest(changes=changes):
                self.assertEqual(len(ipv4_flows(flow, host=True, processes={'42': downloader | changes})), 1)
        self.assertEqual(len(ipv4_flows(flow, host=True, processes={})), 1)
        self.assertEqual(len(ipv4_flows('tcp TIME-WAIT 0 0 192.0.2.1:49080 1.1.1.1:443', host=True)), 1)

    def test_transport_requires_both_api_loopback_refusals(self):
        data = {'ntp_server': '2001:db8::123', 'host_sockets': '',
                'calico_usage_reporting': [['FELIX_USAGEREPORTINGENABLED=false']], 'pods': [
            {'namespace': 'monitoring', 'name': 'prometheus', 'sockets': '',
             'ipv4_loopback_connect_ex': 111, 'ipv6_api_port': 9090},
            {'namespace': 'logging', 'name': 'loki', 'sockets': '',
             'ipv4_loopback_connect_ex': 111, 'ipv6_api_port': 3100}]}
        self.assertEqual(validate_transport(data)['ipv4_runtime_flows'], 0)
        data['pods'][0]['ipv4_loopback_connect_ex'] = 0
        with self.assertRaises(ValueError):
            validate_transport(data)

    def test_complete_snapshot(self):
        report = validate_snapshot(ENV, copy.deepcopy(SNAPSHOT))
        self.assertEqual(report['family'], 'IPv6')
        self.assertEqual(report['pods'], 1)
        self.assertNotIn(ENV['node_ip'], repr(report))

    def test_rejects_ipv4_residuals_and_incomplete_evidence(self):
        cases = []
        for key in SNAPSHOT:
            data = copy.deepcopy(SNAPSHOT)
            if key == 'api_server':
                data[key] = 'https://192.0.2.10:6443'
            elif key == 'nodes':
                data[key][0]['status']['addresses'].append({'type': 'InternalIP', 'address': '192.0.2.10'})
            elif key == 'pods':
                data[key][0]['status']['podIPs'] = [{'ip': '10.244.0.1'}]
            elif key == 'services':
                data[key][0]['spec']['ipFamilies'] = ['IPv6', 'IPv4']
            elif key == 'endpoint_slices':
                data[key][0]['addressType'] = 'IPv4'
            elif key == 'calico_pools':
                data[key].append({'spec': {'cidr': '10.244.0.0/16'}})
            cases.append((key, data))
        data = copy.deepcopy(SNAPSHOT)
        data['endpoint_slices'] = []
        cases.append(('missing endpoints', data))
        data = copy.deepcopy(SNAPSHOT)
        data['pods'][0]['status']['podIPs'] = [{'ip': '::ffff:192.0.2.1'}]
        cases.append(('mapped IPv4', data))
        for name, data in cases:
            with self.subTest(name=name), self.assertRaises(ValueError):
                validate_snapshot(ENV, data)

    def test_ipv6_urls_have_brackets_and_ipv4_is_rejected(self):
        self.assertEqual(http_url('2001:db8::10', 30080), 'http://[2001:db8::10]:30080')
        self.assertEqual(http_url('::1', 9090), 'http://[::1]:9090')
        for host in ('192.0.2.1', '::ffff:192.0.2.1', '::', 'ff02::1', 'fe80::1', '2001:db8::10%eth0'):
            with self.subTest(host=host), self.assertRaises(ValueError):
                http_url(host, 30080)

    def test_postrenderer_requires_single_ipv6_service_family(self):
        service = {'apiVersion': 'v1', 'kind': 'Service', 'spec': {'ports': [{'port': 80}]}}
        self.assertEqual(lock_document(service)['spec']['ipFamilies'], ['IPv6'])
        service['spec']['ipFamilies'] = ['IPv4']
        with self.assertRaises(ValueError):
            lock_document(service)


if __name__ == '__main__':
    unittest.main()
