"""IPv4-only installation contracts; no IPv6 address, route or loopback required."""
import copy
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import jinja2
import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT / 'ansible/filter_plugins'))
from network_config import network_profile
from network_checks import validate_snapshot
from stacklib import environment, lock_document
from mvp import network_ips, servers, image, calico
from test_network import SNAPSHOT, ENV


class IPv4Environment(unittest.TestCase):
    def load(self, updates=None):
        data = yaml.safe_load((ROOT / 'config/environments/ubuntu-ipv4.example.yaml').read_text())
        data.update(updates or {})
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'config/environments').mkdir(parents=True)
            (root / 'config/environments/fixture.local.yaml').write_text(yaml.safe_dump(data))
            with patch('stacklib.ROOT', root):
                return environment('fixture')

    def test_no_ipv6_fields_are_needed(self):
        env = self.load()
        self.assertEqual(env['node_ip'], env['node_ipv4'])
        self.assertNotIn('pod_cidr', env)
        self.assertNotIn('service_cidr', env)
        self.assertEqual(network_profile(env['network_mode'])['loopback'], '127.0.0.1')

    def test_invalid_mode_family_and_servers_are_rejected(self):
        for updates in ({'network_mode': 'auto'}, {'node_ip': '2001:db8::10'},
                        {'node_ipv4': '192.0.2.11'}, {'node_ip': '127.0.0.1'},
                        {'dns_servers': ['2606:4700:4700::1111']},
                        {'ntp_servers': ['2606:4700:f1::123']}, {'pod_cidr_ipv4': 'fd00::/64'}):
            with self.subTest(updates=updates), self.assertRaises(ValueError):
                self.load(updates)

    def test_only_one_real_ipv4_address_and_ipv4_servers_allowed(self):
        self.assertEqual(network_ips(['10.244.0.2'], 'ipv4'), {'ipv4': '10.244.0.2'})
        for values in ([], ['2001:db8::10'], ['10.244.0.2', '10.244.0.3'], ['127.0.0.1']):
            with self.subTest(values=values), self.assertRaises(ValueError):
                network_ips(values, 'ipv4')
        with self.assertRaises(ValueError):
            servers(['2001:db8::10'], 'ipv4')

    def test_preflight_does_not_query_ipv6_on_ipv4_vm(self):
        spec = importlib.util.spec_from_file_location('ipv4_preflight', ROOT / 'ansible/files/preflight.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        calls = []
        def output(argv, **kwargs):
            calls.append(argv)
            self.assertIn('-4', argv)
            if 'route' in argv:
                return json.dumps([{'dst': 'default', 'dev': 'eth0'}])
            return json.dumps([{'ifname': 'eth0', 'operstate': 'UP', 'addr_info': [
                {'family': 'inet', 'local': '192.0.2.10', 'scope': 'global'}]}])
        from types import SimpleNamespace
        argv = ['preflight', '--network-mode', 'ipv4', '', '', '192.0.2.10',
                '10.244.0.0/16', '10.96.0.0/16', '192.0.2.10', 'new']
        with patch.object(module.sys, 'argv', argv), patch.object(module.subprocess, 'check_output', side_effect=output), \
                patch.object(module.shutil, 'disk_usage', return_value=SimpleNamespace(total=60*1024**3, free=50*1024**3)), \
                patch('builtins.print'):
            module.main()
        self.assertEqual(len(calls), 2)


class IPv4Snapshot(unittest.TestCase):
    def setUp(self):
        self.env = {**ENV, 'network_mode': 'ipv4', 'node_ip': ENV['node_ipv4']}
        data = copy.deepcopy(SNAPSHOT)
        data['api_server'] = 'https://192.0.2.10:6443'
        data['nodes'][0]['status']['addresses'] = data['nodes'][0]['status']['addresses'][1:]
        data['nodes'][0]['spec']['podCIDRs'] = data['nodes'][0]['spec']['podCIDRs'][1:]
        pod = data['pods'][0]['status']
        pod['podIPs'] = pod['podIPs'][1:]
        pod['podIP'] = pod['podIPs'][0]['ip']
        service = data['services'][0]['spec']
        service.update(ipFamilies=['IPv4'], ipFamilyPolicy='SingleStack',
                       clusterIPs=service['clusterIPs'][1:], clusterIP=service['clusterIPs'][1])
        data['endpoint_slices'] = data['endpoint_slices'][1:]
        data['calico_pools'] = data['calico_pools'][1:]
        self.data = data

    def test_complete_ipv4_evidence(self):
        result = validate_snapshot(self.env, self.data)
        self.assertEqual(result['families'], ['IPv4'])
        self.assertEqual(result['calico_pool_count'], 1)
        self.assertEqual(result['api_transport'], 'HTTPS over IPv4')

    def test_ipv6_leaks_missing_endpoints_and_wrong_identity_fail(self):
        mutations = [lambda d: d.update(api_server='https://[2001:db8:20::10]:6443'),
                     lambda d: d['nodes'][0]['status']['addresses'].append({'type': 'InternalIP', 'address': ENV['node_ip']}),
                     lambda d: d['pods'][0]['status']['podIPs'].append({'ip': 'fd00::1'}),
                     lambda d: d['services'][0]['spec'].update(ipFamilyPolicy='RequireDualStack'),
                     lambda d: d['endpoint_slices'].clear(),
                     lambda d: d['endpoint_slices'][0]['endpoints'][0]['targetRef'].update(uid='foreign'),
                     lambda d: d['calico_pools'].append(copy.deepcopy(SNAPSHOT['calico_pools'][0]))]
        for index, mutate in enumerate(mutations):
            data = copy.deepcopy(self.data)
            mutate(data)
            with self.subTest(index=index), self.assertRaises(ValueError):
                validate_snapshot(self.env, data)


class IPv4Render(unittest.TestCase):
    def setUp(self):
        self.jinja = jinja2.Environment(loader=jinja2.FileSystemLoader(ROOT / 'ansible/templates'),
                                       undefined=jinja2.StrictUndefined, trim_blocks=True)
        self.jinja.filters.update(to_json=json.dumps, mvp_image=image)
        self.values = yaml.safe_load((ROOT / 'config/versions.yaml').read_text())
        self.values.update(yaml.safe_load((ROOT / 'config/environments/ubuntu-ipv4.example.yaml').read_text()))
        self.values.update(node_ipv4=self.values['node_ip'], network=network_profile('ipv4'),
                           remote_state='/etc/mts-stack', health_uuid={'stdout': 'fixture'},
                           health_server_ipv4='10.244.0.20', health_service_ipv4='10.96.0.20',
                           health_kubernetes_ipv4='10.96.0.1',
                           image_lock=yaml.safe_load((ROOT / 'config/images.lock.yaml').read_text()))

    def render(self, name):
        return list(yaml.safe_load_all(self.jinja.get_template(name + '.j2').render(**self.values)))

    def test_control_plane_and_calico_pool_only_ipv4(self):
        docs = {doc['kind']: doc for doc in self.render('kubeadm.yaml')}
        self.assertEqual(docs['ClusterConfiguration']['networking']['podSubnet'], '10.244.0.0/16')
        self.assertEqual(docs['ClusterConfiguration']['networking']['serviceSubnet'], '10.96.0.0/16')
        self.assertEqual(docs['KubeletConfiguration']['address'], '0.0.0.0')
        self.assertEqual(docs['KubeProxyConfiguration']['nodePortAddresses'], ['192.0.2.10/32'])
        self.assertNotIn('::', json.dumps(docs))
        pools = self.render('calico-pool.yaml')
        self.assertEqual(len(pools), 1)
        self.assertEqual(pools[0]['spec']['cidr'], '10.244.0.0/16')

    def test_health_checks_ipv4_dns_service_and_direct_pod(self):
        pod, service = self.render('health.yaml')
        self.assertEqual(service['spec']['ipFamilies'], ['IPv4'])
        self.assertEqual(service['spec']['ipFamilyPolicy'], 'SingleStack')
        self.assertNotIn('[$POD_IP]', pod['spec']['containers'][0]['command'][-1])
        script = self.render('health-client.yaml')[0]['spec']['template']['spec']['containers'][0]['command'][-1]
        self.assertNotIn('type=AAAA', script)
        for value in ('type=A kubernetes', 'http://10.96.0.20:8080/', 'http://10.244.0.20:8080/',
                      'MTS_IPV4_A_SERVICE_AND_DIRECT_POD_OK'):
            self.assertIn(value, script)

    def test_os_needs_no_ipv6_sysctl_or_socket(self):
        tasks = yaml.safe_load((ROOT / 'ansible/tasks/os.yaml').read_text())
        content = {task['name']: task['ansible.builtin.copy'].get('content', '')
                   for task in tasks if 'ansible.builtin.copy' in task}
        for name in ('Configure SSH for both address families', 'Make the systemd SSH listener accept IPv6 and IPv4',
                     'Persist Kubernetes networking sysctls'):
            text = self.jinja.from_string(content[name]).render(**self.values)
            self.assertNotIn('::', text)
            self.assertNotIn('net.ipv6', text)
            self.assertNotIn('ip6tables', text)
        self.assertIn('AddressFamily inet', self.jinja.from_string(content['Configure SSH for both address families']).render(**self.values))

    def test_postrenderer_rejects_dual_stack_in_ipv4_mode(self):
        doc = {'apiVersion': 'v1', 'kind': 'Service', 'spec': {}}
        self.assertEqual(lock_document(doc, 'ipv4')['spec'], {'ipFamilies': ['IPv4'], 'ipFamilyPolicy': 'SingleStack'})
        doc['spec']['ipFamilies'] = ['IPv6', 'IPv4']
        with self.assertRaises(ValueError):
            lock_document(doc, 'ipv4')

    def test_calico_disables_ipv6_allocation_and_felix(self):
        source = [
            {'kind': 'ConfigMap', 'metadata': {'name': 'calico-config'}, 'data': {
                'cni_network_config': json.dumps({'plugins': [{'type': 'calico', 'ipam': {'type': 'calico-ipam'}}]})}},
            {'kind': 'DaemonSet', 'metadata': {'name': 'calico-node'}, 'spec': {'template': {'spec': {'containers': [{
                'name': 'calico-node', 'image': 'calico/node:v1', 'env': [{'name': 'FELIX_IPV6SUPPORT', 'value': 'true'}],
                'livenessProbe': {'exec': {'command': ['/bin/calico-node', '-felix-live']}},
                'readinessProbe': {'exec': {'command': ['/bin/calico-node', '-felix-ready']}}
            }]}}}}]
        docs = calico(source, {'calico/node:v1': 'sha256:' + 'a'*64}, '', '192.0.2.10', '10.244.0.0/16', '192.0.2.10', 'ipv4')
        ipam = json.loads(docs[0]['data']['cni_network_config'])['plugins'][0]['ipam']
        self.assertEqual(ipam['assign_ipv6'], 'false')
        env = {v['name']: v['value'] for v in docs[1]['spec']['template']['spec']['containers'][0]['env']}
        self.assertEqual((env['IP'], env['IP6'], env['FELIX_IPV6SUPPORT']), ('192.0.2.10', 'none', 'false'))


if __name__ == '__main__':
    unittest.main()
