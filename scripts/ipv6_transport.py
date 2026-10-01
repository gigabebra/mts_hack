"""Observe sockets in live network namespaces, including hidden IPv4 loopback."""
import ipaddress
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys


def collect():
    def read(*args):
        return subprocess.check_output(args, text=True, stderr=subprocess.PIPE, timeout=25)
    cri = ['crictl', '--runtime-endpoint', 'unix:///run/containerd/containerd.sock']
    host_namespace = os.readlink('/proc/self/ns/net')
    result = {'host_sockets': read('ss', '-H', '-antup'), 'pods': [], 'calico_usage_reporting': [],
              'ntp_server': read('timedatectl', 'show-timesync', '--property=ServerAddress', '--value').strip()}
    result['host_processes'] = {}
    host_mount = os.readlink('/proc/self/ns/mnt')
    for pid in set(re.findall(r'pid=(\d+)', result['host_sockets'])):
        try:
            result['host_processes'][pid] = {
                'exe': os.readlink(f'/proc/{pid}/exe'),
                'cgroup': Path(f'/proc/{pid}/cgroup').read_text(),
                'host_mount_namespace': os.readlink(f'/proc/{pid}/ns/mnt') == host_mount,
            }
        except (FileNotFoundError, ProcessLookupError, PermissionError):
            # A disappearing process cannot prove an allowed download.
            result['host_processes'][pid] = {}
    containers = json.loads(read(*cri, 'ps', '-o', 'json'))['containers']
    seen = set()
    for container in containers:
        detail = json.loads(read(*cri, 'inspect', container['id']))
        labels = container['labels']
        if labels.get('io.kubernetes.container.name') == 'calico-node':
            result['calico_usage_reporting'].append([
                value for value in detail['info']['runtimeSpec']['process'].get('env', [])
                if value.startswith('FELIX_USAGEREPORTINGENABLED=')])
        pid = detail['info']['pid']
        namespace = os.readlink(f'/proc/{pid}/ns/net')
        if namespace == host_namespace or namespace in seen:
            continue
        seen.add(namespace)
        item = {'namespace': labels['io.kubernetes.pod.namespace'], 'name': labels['io.kubernetes.pod.name'],
                'sockets': read('nsenter', '-t', str(pid), '-n', 'ss', '-H', '-antup')}
        # CRI tries IPv4 first. These two APIs must refuse it even locally.
        # Containers sharing a Pod network namespace share its listeners too.
        pod_name = labels['io.kubernetes.pod.name']
        port = 9090 if pod_name.startswith('prometheus-monitoring-prometheus-') else (
            3100 if labels['io.kubernetes.container.name'] == 'loki' else None)
        if port:
            probe = ('import socket; s=socket.socket(socket.AF_INET,socket.SOCK_STREAM);'
                     's.settimeout(2); print(s.connect_ex(("127.0.0.1",' + str(port) + ')));s.close()')
            item['ipv4_loopback_connect_ex'] = int(read('nsenter', '-t', str(pid), '-n', 'python3', '-c', probe))
            item['ipv6_api_port'] = port
        result['pods'].append(item)
    return result


def address(endpoint):
    host, port = endpoint.rsplit(':', 1)
    if host == '*':
        return None, port
    # ss annotates bound devices even for IPv4 (127.0.0.53%lo).
    return ipaddress.ip_address(host.strip('[]').split('%', 1)[0]), port


def host_exception(local, local_port, peer, peer_port, line, processes):
    owners = re.findall(r'pid=(\d+)', line)
    if not owners:
        return False
    executables = []
    for pid in owners:
        process = processes.get(pid, {})
        cgroup = process.get('cgroup', '')
        if (not process.get('host_mount_namespace') or not cgroup
                or any(marker in cgroup for marker in ('kubepods', 'docker', 'libpod'))):
            return False
        executables.append(process.get('exe'))
    downloaders = {'/usr/local/bin/containerd', '/usr/lib/apt/methods/http', '/usr/lib/apt/methods/https'}
    downloads = all(exe in downloaders for exe in executables)
    if peer.is_global and peer_port in ('80', '443'):
        return downloads
    # The host stub is auxiliary to OS downloads. Pod/hostNetwork clients
    # and arbitrary host Python/curl processes are never exempted by name.
    stubs = {'127.0.0.53', '127.0.0.54'}
    if str(peer) in stubs and peer_port == '53':
        return downloads
    return (peer.is_loopback and str(local) in stubs and local_port == '53'
            and all(exe == '/usr/lib/systemd/systemd-resolved' for exe in executables))


def ipv4_flows(text, host=False, processes=None):
    failures = []
    for line in text.splitlines():
        fields = line.split()
        if len(fields) < 6 or fields[1] == 'LISTEN':
            continue
        local, local_port = address(fields[4])
        peer, peer_port = address(fields[5])
        if peer is None or peer.is_unspecified:
            continue
        v4 = peer if peer.version == 4 else peer.ipv4_mapped
        if v4 is None:
            continue
        if host and host_exception(local, local_port, v4, peer_port, line, processes or {}):
            continue
        failures.append({'protocol': fields[0], 'state': fields[1], 'loopback': v4.is_loopback,
                         'processes': re.findall(r'\("([^"\n]+)",pid=', line)})
    return failures


def validate(data):
    ntp = ipaddress.IPv6Address(data['ntp_server'])
    if ntp.ipv4_mapped:
        raise ValueError('NTP selected an IPv4-mapped server')
    if not data['pods']:
        raise ValueError('No Pod network namespaces observed')
    if data.get('calico_usage_reporting') != [['FELIX_USAGEREPORTINGENABLED=false']]:
        raise ValueError('Running Calico must explicitly disable external usage/version reporting')
    failures = {'host': ipv4_flows(data['host_sockets'], host=True, processes=data.get('host_processes', {}))}
    api_ports = set()
    for pod in data['pods']:
        failures[pod['namespace'] + '/' + pod['name']] = ipv4_flows(pod['sockets'])
        if 'ipv4_loopback_connect_ex' in pod:
            if pod['ipv4_loopback_connect_ex'] != 111:
                raise ValueError('Prometheus/Loki must refuse IPv4 loopback (ECONNREFUSED), not just timeout')
            api_ports.add(pod['ipv6_api_port'])
    if api_ports != {3100, 9090}:
        raise ValueError('Missing Prometheus/Loki transport probes')
    nonempty = {name: {'count': len(values), 'states': sorted({v['state'] for v in values}),
                      'processes': sorted({p for v in values for p in v['processes']})}
                for name, values in failures.items() if values}
    if nonempty:
        raise ValueError('Observed IPv4 runtime connections: ' + json.dumps(nonempty))
    return {'pod_network_namespaces': len(data['pods']), 'ipv4_runtime_flows': 0,
            'host_exempt_ipv4_flows': len(ipv4_flows(data['host_sockets'])) - len(failures['host']),
            'prometheus_loki_ipv4_loopback': 'ECONNREFUSED', 'ntp_transport': 'IPv6',
            'calico_usage_reporting': 'disabled in running container',
            'scope': 'Socket snapshot including TIME-WAIT; OS download exceptions require PID, executable, host mount namespace and non-container cgroup; unowned sockets fail; no payload inspection'}


def transport_evidence(env, directory):
    from stacklib import direct_api_env, write_json
    command = ['ssh', '-6', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10',
               '-o', 'ProxyCommand=none', '-o', 'ProxyJump=none', '-p', str(env['ssh_port'])]
    if env.get('ssh_private_key_file'):
        command += ['-i', env['ssh_private_key_file']]
    command += shlex.split(env.get('ssh_common_args', ''))
    command += [env['ssh_user'] + '@' + env['ssh_host'], 'sudo -n python3 - --collect']
    response = subprocess.run(command, input=Path(__file__).read_text(), text=True,
                              capture_output=True, timeout=120, env=direct_api_env(), check=True)
    data = json.loads(response.stdout)
    write_json(directory / 'ipv6-runtime.json', data)
    return validate(data)


if __name__ == '__main__' and sys.argv[1:] == ['--collect']:
    print(json.dumps(collect()))
