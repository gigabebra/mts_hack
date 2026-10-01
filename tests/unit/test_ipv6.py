"""Reject plausible IPv4 leftovers in otherwise healthy IPv6 snapshots."""
import copy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts'))
from ipv6_checks import validate_snapshot
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
