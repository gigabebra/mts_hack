"""Validate observed dual-stack resources, including per-Service endpoint families."""
import ipaddress
import json
from urllib.parse import urlsplit

from stacklib import ipv6_host, native_host, kubectl, resource, write_json

# kubeadm and Prometheus Operator create these; our Helm Services have no exception.
SYSTEM_SINGLE = {('default', 'kubernetes'), ('kube-system', 'kube-dns'),
                 ('monitoring', 'prometheus-operated')}


def pair(values, label):
    addresses = [ipaddress.ip_address(native_host(x)) for x in values]
    if [a.version for a in addresses] != [6, 4]:
        raise ValueError(label + ' requires native IPv6 then IPv4')
    return addresses


def validate_snapshot(env, snapshot):
    pod_nets = [ipaddress.ip_network(env[k]) for k in ('pod_cidr', 'pod_cidr_ipv4')]
    service_nets = [ipaddress.ip_network(env[k]) for k in ('service_cidr', 'service_cidr_ipv4')]
    node_ips = pair([env['node_ip'], env['node_ipv4']], 'Node config')
    server = urlsplit(snapshot['api_server'])
    if server.scheme != 'https' or ipv6_host(server.hostname) != str(node_ips[0]):
        raise ValueError('Kubeconfig must connect directly to the configured IPv6 node over HTTPS')
    nodes = snapshot['nodes']
    if len(nodes) != 1 or nodes[0]['metadata']['name'] != env['node_name']:
        raise ValueError('Unexpected cluster node')
    actual = [ipaddress.ip_address(native_host(a['address'])) for a in nodes[0]['status']['addresses']
              if a['type'] == 'InternalIP']
    if len(actual) != 2 or set(actual) != set(node_ips):
        raise ValueError('Node must advertise both configured InternalIPs')
    cidrs = [ipaddress.ip_network(c) for c in nodes[0]['spec'].get('podCIDRs', [])]
    if len(cidrs) != 2 or any(c.version != n.version or not c.subnet_of(n) for c, n in zip(cidrs, pod_nets)):
        raise ValueError('Node requires IPv6-primary Pod CIDRs within both pools')
    count = {'pods': 0, 'services': 0, 'endpoint_slices': 0, 'endpoints': 0}
    pod_ips = {}
    pod_metadata = {}
    for pod in snapshot['pods']:
        status = pod.get('status', {})
        if status.get('phase') in ('Succeeded', 'Failed'):
            continue
        label = pod['metadata']['namespace'] + '/' + pod['metadata']['name']
        addresses = pair([x['ip'] for x in status.get('podIPs', [])], 'Pod ' + label)
        if status.get('podIP') != str(addresses[0]):
            raise ValueError('Pod primary IP must match its first IPv6 address: ' + label)
        if pod.get('spec', {}).get('hostNetwork'):
            if addresses != node_ips:
                raise ValueError('Host-network Pod must use both node IPs: ' + label)
        elif any(a not in n for a, n in zip(addresses, pod_nets)):
            raise ValueError('Pod outside configured pools: ' + label)
        pod_ips[pod['metadata']['uid']] = addresses
        pod_metadata[pod['metadata']['uid']] = pod['metadata']
        count['pods'] += 1
    services = {}
    exceptions = []
    for service in snapshot['services']:
        key = (service['metadata']['namespace'], service['metadata']['name'])
        spec = service['spec']
        families = spec.get('ipFamilies')
        single = families == ['IPv6'] and spec.get('ipFamilyPolicy') == 'SingleStack' and key in SYSTEM_SINGLE
        if not single and (families != ['IPv6', 'IPv4'] or spec.get('ipFamilyPolicy') != 'RequireDualStack'):
            raise ValueError('Service requires IPv6-primary RequireDualStack: ' + '/'.join(key))
        if spec.get('type') == 'ExternalName':
            raise ValueError('ExternalName is outside this verified stack')
        addresses = spec.get('clusterIPs', [])
        if not addresses or spec.get('clusterIP') != addresses[0]:
            raise ValueError('Service primary IP must match clusterIPs[0]')
        if addresses != ['None']:
            if len(addresses) != len(families):
                raise ValueError('Service missing an address family')
            for address, network in zip(addresses, service_nets):
                if ipaddress.ip_address(native_host(address)) not in network:
                    raise ValueError('Service IP outside configured family/range')
        for address in spec.get('externalIPs', []):
            native_host(address)
        services[key] = {'families': families, 'selector': spec.get('selector', {}),
                         'ready': {f: set() for f in families}}
        if single:
            exceptions.append('/'.join(key))
        count['services'] += 1
    for endpoint_slice in snapshot['endpoint_slices']:
        meta = endpoint_slice['metadata']
        key = (meta['namespace'], meta.get('labels', {}).get('kubernetes.io/service-name'))
        family = endpoint_slice.get('addressType')
        if key not in services or family not in services[key]['families']:
            raise ValueError('EndpointSlice has unexpected Service/family')
        for endpoint in endpoint_slice.get('endpoints', []):
            uid = endpoint.get('targetRef', {}).get('uid')
            for value in endpoint['addresses']:
                address = ipaddress.ip_address(native_host(value))
                if 'IPv' + str(address.version) != family:
                    raise ValueError('EndpointSlice address does not match its family')
                if key != ('default', 'kubernetes'):
                    if uid not in pod_ips or address not in pod_ips[uid]:
                        raise ValueError('EndpointSlice address is not bound to its actual Pod UID')
                    pod_meta = pod_metadata[uid]
                    ref = endpoint['targetRef']
                    if pod_meta['namespace'] != key[0] or any(
                            pod_meta.get('labels', {}).get(k) != v for k, v in services[key]['selector'].items()):
                        raise ValueError('Endpoint Pod must match the Service namespace and selector')
                    for field, expected in [('kind', 'Pod'), ('name', pod_meta['name']), ('namespace', key[0])]:
                        if field in ref and ref[field] != expected:
                            raise ValueError('Endpoint targetRef does not match the referenced Pod')
                elif address not in node_ips:
                    raise ValueError('API endpoint is outside the configured node')
                if endpoint.get('conditions', {}).get('ready', True):
                    services[key]['ready'][family].add(uid or str(address))
                count['endpoints'] += 1
        count['endpoint_slices'] += 1
    for key, service in services.items():
        ready = service['ready']
        if any(not ready[f] for f in service['families']):
            raise ValueError('Service lacks Ready endpoints in every family: ' + '/'.join(key))
        if service['families'] == ['IPv6', 'IPv4'] and ready['IPv6'] != ready['IPv4']:
            raise ValueError('Service families resolve to different Pod UIDs: ' + '/'.join(key))
    pools = snapshot['calico_pools']
    if len(pools) != 2 or {ipaddress.ip_network(p['spec']['cidr']) for p in pools} != set(pod_nets):
        raise ValueError('Exactly the configured IPv6 and IPv4 Calico pools are required')
    if any(p['spec'].get('vxlanMode') != 'Always' or not p['spec'].get('natOutgoing') or
           p['spec'].get('disabled', False) for p in pools):
        raise ValueError('Both Calico pools require enabled VXLAN Always and outgoing NAT')
    if not all(count.values()):
        raise ValueError('Network evidence is incomplete')
    return {'families': ['IPv6', 'IPv4'], 'primary': 'IPv6', 'api_transport': 'HTTPS over IPv6',
            'node_internal_ip_count': 2, 'calico_pool_count': 2, 'calico_vxlan_mode': 'Always',
            'system_single_stack_services': sorted(exceptions), **count}


def network_evidence(env, directory=None):
    config = json.loads(kubectl(env, 'config', 'view', '--minify', '-o', 'json', capture=True).stdout)
    cluster = config['clusters'][0]['cluster']
    if cluster.get('proxy-url') or cluster.get('insecure-skip-tls-verify'):
        raise ValueError('API requires direct access and normal CA/SAN verification')
    snapshot = {'api_server': cluster['server']}
    for key, kind, args in [('nodes', 'nodes', []), ('pods', 'pods', ['-A']),
                            ('services', 'services', ['-A']),
                            ('endpoint_slices', 'endpointslices.discovery.k8s.io', ['-A']),
                            ('calico_pools', 'ippools.crd.projectcalico.org', [])]:
        snapshot[key] = resource(env, kind, *args)['items']
    if directory is not None:
        write_json(directory / 'network-snapshot.json', snapshot)
    return validate_snapshot(env, snapshot)
