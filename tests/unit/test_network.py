"""Reject incomplete dual-stack resources even when global family counts look healthy."""
import copy
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts'))
from network_checks import network_evidence, validate_snapshot
from stacklib import http_url, lock_document

ENV = {'node_name': 'fixture', 'node_ip': '2001:db8:20::10', 'node_ipv4': '192.0.2.10',
       'pod_cidr': 'fd4d:5453:1000:100::/56', 'service_cidr': 'fd4d:5453:1000:200::/112',
       'pod_cidr_ipv4': '10.244.0.0/16', 'service_cidr_ipv4': '10.96.0.0/16'}
POD_IPS = ['fd4d:5453:1000:100::1', '10.244.0.1']
SNAPSHOT = {
    'api_server': 'https://[2001:db8:20::10]:6443',
    'nodes': [{'metadata': {'name': 'fixture'}, 'spec': {'podCIDRs': ['fd4d:5453:1000:100::/64', '10.244.0.0/24']},
               'status': {'addresses': [{'type': 'InternalIP', 'address': ENV[k]} for k in ('node_ip', 'node_ipv4')]}}],
    'pods': [{'metadata': {'namespace': 'demo', 'name': 'hello', 'uid': 'pod-1', 'labels': {'app': 'hello'}}, 'spec': {},
              'status': {'phase': 'Running', 'podIP': POD_IPS[0], 'podIPs': [{'ip': ip} for ip in POD_IPS]}}],
    'services': [{'metadata': {'namespace': 'demo', 'name': 'hello'},
                  'spec': {'selector': {'app': 'hello'}, 'ipFamilies': ['IPv6', 'IPv4'], 'ipFamilyPolicy': 'RequireDualStack',
                           'clusterIP': 'fd4d:5453:1000:200::1', 'clusterIPs': ['fd4d:5453:1000:200::1', '10.96.0.1']}}],
    'endpoint_slices': [{'metadata': {'namespace': 'demo', 'labels': {'kubernetes.io/service-name': 'hello'}},
                         'addressType': family, 'endpoints': [{'addresses': [ip], 'targetRef': {'uid': 'pod-1'},
                                                             'conditions': {'ready': True}}]}
                        for family, ip in zip(('IPv6', 'IPv4'), POD_IPS)],
    'calico_pools': [{'spec': {'cidr': ENV[k], 'vxlanMode': 'Always', 'natOutgoing': True}}
                    for k in ('pod_cidr', 'pod_cidr_ipv4')],
}


class DualStackContract(unittest.TestCase):
    def test_insecure_or_proxied_api_is_rejected_before_resource_reads(self):
        for option in ({'insecure-skip-tls-verify': True}, {'proxy-url': 'http://127.0.0.1:9'}):
            cluster = {'server': SNAPSHOT['api_server'], **option}
            with patch('network_checks.kubectl') as command, patch('network_checks.resource') as read:
                command.return_value.stdout = json.dumps({'clusters': [{'cluster': cluster}]})
                with self.assertRaises(ValueError):
                    network_evidence(ENV)
                read.assert_not_called()

    def test_complete_snapshot_and_sanitized_summary(self):
        result = validate_snapshot(ENV, copy.deepcopy(SNAPSHOT))
        self.assertEqual(result['families'], ['IPv6', 'IPv4'])
        self.assertEqual(result['pods'], 1)
        self.assertNotIn(ENV['node_ip'], repr(result))

    def test_rejects_missing_or_incorrect_family(self):
        mutations = [
            lambda d: d.update(api_server='https://192.0.2.10:6443'),
            lambda d: d['nodes'][0]['status']['addresses'].pop(),
            lambda d: d['nodes'][0]['spec']['podCIDRs'].reverse(),
            lambda d: d['pods'][0]['status']['podIPs'].pop(),
            lambda d: d['pods'][0]['status'].update(podIP=POD_IPS[1]),
            lambda d: d['pods'][0]['status']['podIPs'][0].update(ip='::ffff:192.0.2.1'),
            lambda d: d['services'][0]['spec']['ipFamilies'].reverse(),
            lambda d: d['services'][0]['spec']['clusterIPs'].pop(),
            lambda d: d['services'][0]['spec'].update(ipFamilies=['IPv6'], ipFamilyPolicy='SingleStack'),
            lambda d: d['endpoint_slices'].pop(),
            lambda d: d['endpoint_slices'][1]['endpoints'][0]['targetRef'].update(uid='other-pod'),
            lambda d: d['endpoint_slices'][1]['endpoints'][0]['targetRef'].update(name='wrong-pod'),
            lambda d: d['endpoint_slices'][1]['endpoints'][0]['targetRef'].update(kind='Node'),
            lambda d: d['pods'][0]['metadata'].update(namespace='other'),
            lambda d: d['pods'][0]['metadata'].update(labels={'app': 'other'}),
            lambda d: d['endpoint_slices'][1]['endpoints'][0]['conditions'].update(ready=False),
            lambda d: d['endpoint_slices'][1]['endpoints'][0].update(addresses=['10.244.0.2']),
            lambda d: d['calico_pools'].pop(),
            lambda d: d['calico_pools'][0]['spec'].update(natOutgoing=False),
        ]
        for i, mutate in enumerate(mutations):
            data = copy.deepcopy(SNAPSHOT)
            mutate(data)
            with self.subTest(case=i), self.assertRaises(ValueError):
                validate_snapshot(ENV, data)

    def test_exact_system_single_stack_allowlist(self):
        data = copy.deepcopy(SNAPSHOT)
        service = data['services'][0]
        service['metadata'].update(namespace='kube-system', name='kube-dns')
        data['pods'][0]['metadata']['namespace'] = 'kube-system'
        service['spec'].update(ipFamilies=['IPv6'], ipFamilyPolicy='SingleStack', clusterIPs=[service['spec']['clusterIP']])
        data['endpoint_slices'] = data['endpoint_slices'][:1]
        data['endpoint_slices'][0]['metadata'].update(namespace='kube-system', labels={'kubernetes.io/service-name': 'kube-dns'})
        self.assertEqual(validate_snapshot(ENV, data)['system_single_stack_services'], ['kube-system/kube-dns'])
        service['metadata']['name'] = 'arbitrary'
        with self.assertRaises(ValueError):
            validate_snapshot(ENV, data)

    def test_per_service_families_not_global_presence(self):
        data = copy.deepcopy(SNAPSHOT)
        other = copy.deepcopy(data['services'][0])
        other['metadata']['name'] = 'missing-v4'
        data['services'].append(other)
        endpoint = copy.deepcopy(data['endpoint_slices'][0])
        endpoint['metadata']['labels']['kubernetes.io/service-name'] = 'missing-v4'
        data['endpoint_slices'].append(endpoint)
        with self.assertRaises(ValueError):
            validate_snapshot(ENV, data)

    def test_urls_accept_both_native_families(self):
        self.assertEqual(http_url('2001:db8::10', 30080), 'http://[2001:db8::10]:30080')
        self.assertEqual(http_url('192.0.2.10', 30080), 'http://192.0.2.10:30080')
        self.assertEqual(http_url('127.0.0.1', 9090), 'http://127.0.0.1:9090')
        for host in ('::ffff:192.0.2.1', '::', '0.0.0.0', 'ff02::1', 'fe80::1', '2001:db8::10%eth0'):
            with self.subTest(host=host), self.assertRaises(ValueError):
                http_url(host, 30080)

    def test_postrenderer_requires_ipv6_primary_dual_stack(self):
        service = {'apiVersion': 'v1', 'kind': 'Service', 'spec': {'ports': [{'port': 80}]}}
        self.assertEqual(lock_document(service)['spec']['ipFamilies'], ['IPv6', 'IPv4'])
        self.assertEqual(service['spec']['ipFamilyPolicy'], 'RequireDualStack')
        for families in (['IPv4'], ['IPv6'], ['IPv4', 'IPv6']):
            service['spec']['ipFamilies'] = families
            with self.assertRaises(ValueError):
                lock_document(service)


if __name__ == '__main__':
    unittest.main()
