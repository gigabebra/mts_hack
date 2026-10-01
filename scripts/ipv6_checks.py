"""Observe actual cluster address families; no success inferred from values alone."""
import ipaddress
import json
from urllib.parse import urlsplit

from stacklib import ipv6_host, kubectl, resource


def validate_snapshot(env, snapshot):
    pod_network = ipaddress.IPv6Network(env['pod_cidr'])
    service_network = ipaddress.IPv6Network(env['service_cidr'])
    node_ip = ipv6_host(env['node_ip'])
    server = urlsplit(snapshot['api_server'])
    if server.scheme != 'https' or ipv6_host(server.hostname) != node_ip:
        raise ValueError('Kubeconfig must connect to the configured IPv6 node over HTTPS')
    nodes = snapshot['nodes']
    if len(nodes) != 1 or nodes[0]['metadata']['name'] != env['node_name']:
        raise ValueError('IPv6 check received an unexpected node')
    addresses = [a['address'] for a in nodes[0]['status']['addresses'] if a['type'] == 'InternalIP']
    if [ipv6_host(a) for a in addresses] != [node_ip]:
        raise ValueError('Node must advertise exactly the configured IPv6 InternalIP')
    cidrs = nodes[0]['spec'].get('podCIDRs', [])
    if len(cidrs) != 1 or not ipaddress.IPv6Network(cidrs[0]).subnet_of(pod_network):
        raise ValueError('Node Pod CIDR must belong to the IPv6 pool')
    count = {'pods': 0, 'services': 0, 'endpoint_slices': 0, 'endpoints': 0}
    for pod in snapshot['pods']:
        if pod.get('status', {}).get('phase') in ('Succeeded', 'Failed'):
            continue
        ips = pod.get('status', {}).get('podIPs', [])
        if len(ips) != 1:
            raise ValueError('Each running Pod must have exactly one IPv6 address')
        address = ipaddress.IPv6Address(ipv6_host(ips[0]['ip']))
        if pod.get('spec', {}).get('hostNetwork'):
            if str(address) != node_ip:
                raise ValueError('Host-network Pod does not use the IPv6 node address')
        elif address not in pod_network:
            raise ValueError('Pod address is outside the configured IPv6 pool')
        count['pods'] += 1
    for service in snapshot['services']:
        spec = service['spec']
        if spec.get('type') == 'ExternalName':
            raise ValueError('ExternalName Service is outside the verified IPv6-only contract')
        if spec.get('ipFamilies') != ['IPv6'] or spec.get('ipFamilyPolicy') != 'SingleStack':
            raise ValueError('Service must be SingleStack IPv6, including headless Services')
        for address in spec.get('clusterIPs', []):
            if address != 'None' and ipaddress.IPv6Address(ipv6_host(address)) not in service_network:
                raise ValueError('Service address is outside the configured IPv6 range')
        for address in spec.get('externalIPs', []):
            ipv6_host(address)
        count['services'] += 1
    for endpoint_slice in snapshot['endpoint_slices']:
        if endpoint_slice.get('addressType') != 'IPv6':
            raise ValueError('EndpointSlice must contain IPv6 addresses')
        for endpoint in endpoint_slice.get('endpoints', []):
            for address in endpoint['addresses']:
                ipv6_host(address)
                count['endpoints'] += 1
        count['endpoint_slices'] += 1
    pools = snapshot['calico_pools']
    if len(pools) != 1 or ipaddress.IPv6Network(pools[0]['spec']['cidr']) != pod_network:
        raise ValueError('Exactly one IPv6 Calico pool is required; no IPv4 pool allowed')
    if pools[0]['spec'].get('vxlanMode') != 'Always':
        raise ValueError('Expected IPv6 VXLAN pool')
    if not all(count.values()):
        raise ValueError('IPv6 evidence is incomplete: empty workloads, services or endpoints')
    return {'family': 'IPv6', 'api_transport': 'HTTPS over IPv6', 'node_internal_ip_count': 1,
            'calico_pool_count': 1, 'calico_vxlan_mode': 'Always', **count}


def network_evidence(env):
    config = json.loads(kubectl(env, 'config', 'view', '--minify', '-o', 'json', capture=True).stdout)
    if config['clusters'][0]['cluster'].get('proxy-url'):
        raise ValueError('Kubeconfig proxy-url is outside the direct IPv6 contract')
    snapshot = {'api_server': config['clusters'][0]['cluster']['server']}
    for key, kind, args in [('nodes', 'nodes', []), ('pods', 'pods', ['-A']),
                            ('services', 'services', ['-A']),
                            ('endpoint_slices', 'endpointslices.discovery.k8s.io', ['-A']),
                            ('calico_pools', 'ippools.crd.projectcalico.org', [])]:
        snapshot[key] = resource(env, kind, *args)['items']
    return validate_snapshot(env, snapshot)
