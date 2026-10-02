"""Shared local orchestration; never selects an implicit Kubernetes context."""
import contextlib
import fcntl
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import subprocess
import time
import urllib.request

import yaml

ROOT = Path(__file__).resolve().parent.parent
VERSIONS = yaml.safe_load((ROOT / 'config/versions.yaml').read_text())
IMAGES = yaml.safe_load((ROOT / 'config/images.lock.yaml').read_text())['images']


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def internal_http():
    return urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())


def direct_api_env(source=None):
    result = dict(os.environ if source is None else source)
    for key in tuple(result):
        if key.lower() in ('http_proxy', 'https_proxy', 'all_proxy'):
            result.pop(key)
    return result


def command(argv, *, capture=False, timeout=900, **kwargs):
    if '--kubeconfig' in argv:
        # API access is direct IPv6 even if artifact downloads use an HTTP proxy.
        kwargs['env'] = direct_api_env(kwargs.pop('env', None))
    return subprocess.run([str(x) for x in argv], check=True, text=True,
                          capture_output=capture, timeout=timeout, **kwargs)


def tool(name):
    local = ROOT / '.cache/tools' / name
    return str(local) if local.exists() else name


def environment(name):
    path = ROOT / 'config/environments' / (name + '.local.yaml')
    env = yaml.safe_load(path.read_text())
    for key in ('node_name', 'node_ip', 'node_ipv4', 'ssh_host', 'ssh_user', 'kubeconfig'):
        if not env.get(key):
            raise ValueError(f'{path}: missing {key}')
    env.setdefault('node_port', 30080)
    env.setdefault('https_node_port', 30443)
    env.setdefault('tls_hostname', 'hello.test')
    env.setdefault('data_root', '/var/lib/mts-stack')
    env.setdefault('ssh_port', 22)
    env.setdefault('pod_cidr', 'fd4d:5453:1000:100::/56')
    env.setdefault('service_cidr', 'fd4d:5453:1000:200::/112')
    env.setdefault('pod_cidr_ipv4', '10.244.0.0/16')
    env.setdefault('service_cidr_ipv4', '10.96.0.0/16')
    ipv6_host(env['node_ip'])
    if ipaddress.IPv6Address(env['node_ip']).is_loopback:
        raise ValueError('node_ip must be reachable outside the node, not loopback')
    node4 = ipaddress.ip_address(native_host(env['node_ipv4']))
    if node4.version != 4 or node4.is_loopback:
        raise ValueError('node_ipv4 must be a reachable native IPv4 address')
    native_host(env['ssh_host'])
    for key, family in [('pod_cidr', 6), ('service_cidr', 6),
                        ('pod_cidr_ipv4', 4), ('service_cidr_ipv4', 4)]:
        if ipaddress.ip_network(env[key]).version != family:
            raise ValueError(f'{key} must be an IPv{family} network')
    for key in ('kubeconfig', 'ssh_private_key_file'):
        if env.get(key):
            p = Path(os.path.expanduser(env[key]))
            env[key] = str(p if p.is_absolute() else ROOT / p)
    env['_file'] = str(path)
    env['_name'] = name
    return env


def ipv6_host(value):
    address = ipaddress.IPv6Address(value)
    if address.scope_id or address.ipv4_mapped or address.is_unspecified or address.is_multicast or address.is_link_local:
        raise ValueError('Expected an unscoped unicast IPv6 address')
    return str(address)


def http_url(host, port):
    host = native_host(host)
    return f'http://[{host}]:{int(port)}' if ':' in host else f'http://{host}:{int(port)}'


def native_host(value):
    address = ipaddress.ip_address(value)
    if address.version == 6:
        return ipv6_host(value)
    if address.is_unspecified or address.is_multicast or address.is_link_local:
        raise ValueError('Expected a native unicast IP address')
    return str(address)


def kubectl(env, *args, capture=False, timeout=300, **kwargs):
    return command([tool('kubectl'), '--kubeconfig', env['kubeconfig'], *args],
                   capture=capture, timeout=timeout, **kwargs)


def resource(env, *args):
    return json.loads(kubectl(env, 'get', *args, '-o', 'json', capture=True).stdout)


def download(spec, name):
    path = ROOT / '.cache/downloads' / name
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and hashlib.sha256(path.read_bytes()).hexdigest() == spec['sha256']:
        return path
    temp = path.with_suffix(path.suffix + '.part')
    with urllib.request.urlopen(spec['url'], timeout=90) as response, temp.open('wb') as stream:
        while chunk := response.read(1024 * 1024):
            stream.write(chunk)
    if hashlib.sha256(temp.read_bytes()).hexdigest() != spec['sha256']:
        temp.unlink()
        raise ValueError(f'SHA256 mismatch: {name}')
    temp.replace(path)
    return path


def locked_image(ref):
    if '@' in ref:
        accepted = {candidate for tagged, digest in IMAGES.items()
                    for candidate in (tagged + '@' + digest,
                                      tagged.rsplit(':', 1)[0] + '@' + digest)}
        if ref not in accepted:
            raise ValueError(f'Image repository/tag and digest absent from lock: {ref}')
        return ref
    if ref not in IMAGES:
        raise ValueError(f'Image absent from lock: {ref}')
    return ref.rsplit(':', 1)[0] + '@' + IMAGES[ref]


def lock_document(node):
    if isinstance(node, dict):
        if node.get('name') == 'traefik' and 'image' in node:
            node['args'] = [arg.replace('.address=:', '.address=[::]:')
                            if arg.lower().startswith('--entrypoints.') else arg
                            for arg in node.get('args', [])]
        if node.get('name') == 'kube-prometheus-stack' and 'image' in node:
            node['args'] = list(dict.fromkeys(
                arg.replace('--web.listen-address=:', '--web.listen-address=[::]:')
                for arg in node.get('args', [])))
        if node.get('name') == 'node-exporter' and 'image' in node:
            for item in node.get('env', []):
                if item.get('name') == 'HOST_IP':
                    item['value'] = '::'
        if node.get('kind') == 'Service' and node.get('apiVersion') == 'v1':
            spec = node.setdefault('spec', {})
            if spec.get('type') != 'ExternalName':
                if spec.get('ipFamilies', ['IPv6', 'IPv4']) != ['IPv6', 'IPv4']:
                    raise ValueError('Services require IPv6-primary dual-stack')
                spec['ipFamilies'] = ['IPv6', 'IPv4']
                spec['ipFamilyPolicy'] = 'RequireDualStack'
        for key, value in node.items():
            if key == 'image' and isinstance(value, str):
                node[key] = locked_image(value)
            else:
                lock_document(value)
    elif isinstance(node, list):
        for item in node:
            lock_document(item)
    return node


@contextlib.contextmanager
def deployment_lock(env_name):
    path = ROOT / '.state' / ('deploy-' + env_name + '.lock')
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield


def write_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + '\n')


@contextlib.contextmanager
def forward(env, namespace, service, remote_port, directory):
    # kubectl chooses an unused loopback port atomically.
    log = Path(directory) / f'port-forward-{namespace}.log'
    with log.open('w+') as stream:
        process = subprocess.Popen([tool('kubectl'), '--kubeconfig', env['kubeconfig'],
                                    '-n', namespace, 'port-forward', '--address=::1',
                                    'service/' + service, ':' + str(remote_port)],
                                   stdout=stream, stderr=stream, text=True, env=direct_api_env())
        try:
            import re
            until = time.monotonic() + 30
            while time.monotonic() < until:
                stream.flush()
                text = log.read_text()
                match = re.search(r'Forwarding from \[::1\]:(\d+)', text)
                if match:
                    yield http_url('::1', match.group(1))
                    return
                if process.poll() is not None:
                    raise RuntimeError('port-forward failed: ' + text[-2000:])
                time.sleep(0.2)
            raise TimeoutError('port-forward did not start: ' + log.read_text()[-2000:])
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
