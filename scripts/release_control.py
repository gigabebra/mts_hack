"""Shared Helm release operations. Callers hold stacklib.deployment_lock."""
import base64
import datetime as dt
import json
import os
from pathlib import Path
import re
import tempfile
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit

import yaml
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from stacklib import ROOT, VERSIONS, command, http_url, internal_http, kubectl, locked_image, resource, tool


def _observed(env, kind, name):
    return json.loads(kubectl(env, '--request-timeout=5s', 'get', kind, name, '-n', 'demo',
                              '-o', 'json', capture=True, timeout=7).stdout)


def settings(env):
    value = yaml.safe_load((ROOT / 'config/canary.yaml').read_text())
    value['host'] = env.get('tls_hostname', value['host'])
    return value


def assert_target(env):
    config = json.loads(kubectl(env, 'config', 'view', '--minify', '-o', 'json', capture=True).stdout)
    cluster = config['clusters'][0]['cluster']
    import ipaddress
    url = urlsplit(cluster['server'])
    if (url.scheme != 'https' or ipaddress.ip_address(url.hostname) != ipaddress.ip_address(env['node_ip'])
            or cluster.get('proxy-url') or cluster.get('insecure-skip-tls-verify')):
        raise ValueError('Explicit kubeconfig must use the configured IPv6 node and verified TLS')
    nodes = resource(env, 'nodes')['items']
    if len(nodes) != 1 or nodes[0]['metadata']['name'] != env['node_name']:
        raise ValueError('Refusing mutation of an unexpected cluster')
    if not any(c['type'] == 'Ready' and c['status'] == 'True' for c in nodes[0]['status']['conditions']):
        raise ValueError('Configured node is not Ready')


def install(env, release, namespace, chart, values=None, overrides=None, timeout=600):
    if namespace == 'demo' and release in ('app', 'app-canary', 'gateway'):
        recover_pending(env, release)
    argv = [tool('helm'), 'upgrade', '--install', release, chart,
            '--namespace', namespace, '--kubeconfig', env['kubeconfig'],
            '--skip-crds', '--post-renderer', ROOT / 'scripts/post-render.py',
            '--wait', '--atomic', '--timeout', str(timeout) + 's', '--history-max', '5', '--hide-notes']
    if values:
        argv += ['-f', ROOT / values]
    state = ROOT / '.state'
    state.mkdir(exist_ok=True)
    # Helm TLS values contain a private key. Never retain or print the file.
    with tempfile.NamedTemporaryFile(mode='w', prefix='release-', suffix='.yaml', dir=state) as stream:
        os.fchmod(stream.fileno(), 0o600)
        yaml.safe_dump(overrides or {}, stream)
        stream.flush()
        argv += ['-f', stream.name]
        command(argv, timeout=timeout * 2 + 30, capture=True)
    print('Helm ready:', namespace + '/' + release, flush=True)


def recover_pending(env, release):
    """Recover an interrupted Helm write using a completed release revision."""
    rows = json.loads(command([tool('helm'), 'list', '-n', 'demo', '--all', '-o', 'json',
                               '--kubeconfig', env['kubeconfig']], capture=True, timeout=15).stdout)
    current = next((row for row in rows if row['name'] == release), None)
    if current is None or not current['status'].startswith('pending-'):
        return
    history = json.loads(command([tool('helm'), 'history', release, '-n', 'demo', '-o', 'json',
                                  '--kubeconfig', env['kubeconfig']], capture=True, timeout=15).stdout)
    completed = [row for row in reversed(history) if row['status'] in ('deployed', 'superseded')]
    for row in completed:
        if release == 'gateway':
            old = json.loads(command([tool('helm'), 'get', 'values', release, '-n', 'demo',
                                      '--revision', str(row['revision']), '-o', 'json',
                                      '--kubeconfig', env['kubeconfig']], capture=True, timeout=15).stdout) or {}
            if old.get('traffic', {}) != {'stableWeight': 100, 'canaryWeight': 0}:
                continue
        command([tool('helm'), 'rollback', release, str(row['revision']), '-n', 'demo',
                 '--kubeconfig', env['kubeconfig'], '--wait', '--timeout', '120s'], capture=True, timeout=150)
        print('Recovered interrupted Helm release:', release, flush=True)
        return
    # A first, interrupted candidate installation has no accepted revision.
    # Only this dedicated candidate release may be removed and re-created.
    if release == 'app-canary' and not completed:
        command([tool('helm'), 'uninstall', release, '-n', 'demo', '--kubeconfig', env['kubeconfig'],
                 '--wait', '--timeout', '120s'], capture=True, timeout=150)
        return
    raise ValueError('Interrupted ' + release + ' has no completed safe revision; inspect helm history and restore the known accepted release')


def helm_values(env, release):
    return json.loads(command([tool('helm'), 'get', 'values', release, '-n', 'demo',
                               '--kubeconfig', env['kubeconfig'], '-o', 'json'], capture=True, timeout=15).stdout) or {}


def helm_info(env, release):
    data = json.loads(command([tool('helm'), 'status', release, '-n', 'demo',
                               '--kubeconfig', env['kubeconfig'], '-o', 'json'], capture=True, timeout=15).stdout)
    metadata = data.get('chart', {}).get('metadata', {})
    return {'revision': data['version'], 'status': data['info']['status'],
            'chart': metadata.get('name', '') + '-' + metadata.get('version', ''),
            'app_version': metadata.get('appVersion', '')}


def candidate_defaults(env):
    return {'fullnameOverride': 'hello-canary', 'image': VERSIONS['images']['nginx'],
            'releaseTrack': 'canary', 'releaseRevision': 'nginx-1.28.0-base',
            'responseBody': 'Hello World!', 'faultMode': 'none', 'replicas': 1}


def install_candidate(env, values):
    if values.get('fullnameOverride') != 'hello-canary' or values.get('releaseTrack') != 'canary':
        raise ValueError('Candidate must remain isolated from stable resources')
    locked_image(values['image'])
    install(env, 'app-canary', 'demo', ROOT / 'deploy/app', overrides=values, timeout=120)


def route_weights(route):
    rules = route.get('spec', {}).get('rules', [])
    if len(rules) != 1:
        raise ValueError('Expected exactly one weighted rule')
    refs = rules[0].get('backendRefs', [])
    if len(refs) != 2:
        raise ValueError('Expected exactly stable and canary backendRefs')
    result = {}
    for ref in refs:
        role = {'hello': 'stable', 'hello-canary': 'canary'}.get(ref.get('name'))
        if (role is None or role in result or ref.get('kind', 'Service') != 'Service'
                or ref.get('group', '') != '' or ref.get('namespace', 'demo') != 'demo' or ref.get('port') != 80):
            raise ValueError('Unexpected backend identity')
        weight = ref.get('weight', 1)
        if type(weight) is not int or not 0 <= weight <= 100:
            raise ValueError('Invalid backend weight')
        result[role] = weight
    if sum(result.values()) != 100:
        raise ValueError('Expected weights summing to 100')
    return result


def current_weights(env):
    observed = [route_weights(_observed(env, 'httproute', name))
                for name in ('hello', 'hello-host')]
    if observed[0] != observed[1]:
        raise ValueError('IP and hostname route weights differ')
    return observed[0]


def _conditions(conditions, generation, expected):
    for kind in expected:
        entries = [c for c in conditions if c.get('type') == kind]
        if (len(entries) != 1 or entries[0].get('status') != 'True'
                or entries[0].get('observedGeneration') != generation):
            raise ValueError('Missing, failed or stale condition: ' + kind)


def wait_routes(env, timeout=90):
    deadline = time.monotonic() + timeout
    last = 'not observed'
    while time.monotonic() < deadline:
        try:
            gateway = _observed(env, 'gateway', 'gateway')
            generation = gateway['metadata']['generation']
            _conditions(gateway.get('status', {}).get('conditions', []), generation, ('Accepted', 'Programmed'))
            for name in ('web', 'websecure'):
                listeners = [x for x in gateway.get('status', {}).get('listeners', []) if x.get('name') == name]
                if len(listeners) != 1:
                    raise ValueError('Missing expected Gateway listener')
                _conditions(listeners[0].get('conditions', []), generation, ('Accepted', 'Programmed', 'ResolvedRefs'))
            for name in ('hello', 'hello-host', 'hello-stable', 'hello-canary'):
                route = _observed(env, 'httproute', name)
                for listener in ('web', 'websecure'):
                    parents = [p for p in route.get('status', {}).get('parents', [])
                               if p.get('controllerName') == 'traefik.io/gateway-controller'
                               and p.get('parentRef', {}).get('name') == 'gateway'
                               and p.get('parentRef', {}).get('namespace', 'demo') == 'demo'
                               and p.get('parentRef', {}).get('sectionName') == listener]
                    if len(parents) != 1:
                        raise ValueError('Missing route parent: ' + name + '/' + listener)
                    _conditions(parents[0].get('conditions', []), route['metadata']['generation'], ('Accepted', 'ResolvedRefs'))
            return current_weights(env)
        except Exception as error:
            last = str(error)
            time.sleep(1)
    raise TimeoutError('Gateway configuration did not converge: ' + last)


def set_weights(env, stable, canary):
    if (stable, canary) not in ((100, 0), (90, 10)):
        raise ValueError('Only confirmed stable100/0 and demo90/10 states are supported')
    values = helm_values(env, 'gateway')
    values['traffic'] = {'stableWeight': stable, 'canaryWeight': canary}
    install(env, 'gateway', 'demo', ROOT / 'deploy/gateway', overrides=values, timeout=120)
    actual = wait_routes(env)
    if actual != {'stable': stable, 'canary': canary}:
        raise ValueError('Observed weights differ from requested release')
    wait_traffic_split(env, stable, canary)
    return actual


def wait_traffic_split(env, stable, canary, timeout=30):
    """Observe the marker emitted by the same route config as backend weights."""
    expected = f'{stable}/{canary}'
    started = time.monotonic()
    deadline = started + timeout
    evidence = {'expected': expected, 'status': 'fail', 'attempts': 0, 'observations': []}
    env['_last_routing_convergence'] = evidence
    http = internal_http()
    while time.monotonic() < deadline:
        evidence['attempts'] += 1
        matching = True
        for route, host in (('hello', None), ('hello-host', env.get('tls_hostname', 'hello.test'))):
            headers = {'Connection': 'close', 'X-Request-ID': 'routing-convergence'}
            if host:
                headers['Host'] = host
            request = urllib.request.Request(http_url(env['node_ip'], env['node_port']) + '/', headers=headers)
            observation = {'route': route}
            try:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError('Routing convergence deadline')
                try:
                    response = http.open(request, timeout=min(3, remaining))
                except urllib.error.HTTPError as error:
                    response = error
                with response:
                    splits = response.headers.get_all('X-Traffic-Split', [])
                    routes = response.headers.get_all('X-Gateway-Route', [])
                    response.read(4097)
                    observation.update(status=response.status,
                                       split=splits[0] if len(splits) == 1 and splits[0] in ('100/0', '90/10') else 'invalid',
                                       route_matches=routes == [route])
                    matching = matching and splits == [expected] and routes == [route]
            except Exception as error:
                observation['error'] = type(error).__name__
                matching = False
            evidence['observations'].append(observation)
        if matching:
            evidence.update(status='pass', duration_seconds=round(time.monotonic() - started, 3))
            return evidence
        time.sleep(min(.2, max(0, deadline - time.monotonic())))
    evidence['duration_seconds'] = round(time.monotonic() - started, 3)
    raise TimeoutError('Traefik has not served the requested traffic split marker on both routes')


def _private_write(path, content):
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
        os.fchmod(stream.fileno(), 0o600)
        stream.write(content)
        temp = Path(stream.name)
    temp.replace(path)


def validate_certificate(certificate, private_key, ca, hostname):
    cert = x509.load_pem_x509_certificate(certificate)
    authority = x509.load_pem_x509_certificate(ca)
    key = serialization.load_pem_private_key(private_key, password=None)
    now = dt.datetime.now(dt.timezone.utc)
    for item in (cert, authority):
        if not item.not_valid_before_utc <= now < item.not_valid_after_utc:
            raise ValueError('TLS certificate or CA is expired/not yet valid; explicit renewal required')
    if not authority.extensions.get_extension_for_class(x509.BasicConstraints).value.ca:
        raise ValueError('TLS issuer is not a CA')
    if cert.issuer != authority.subject:
        raise ValueError('Unexpected TLS issuer')
    authority.public_key().verify(cert.signature, cert.tbs_certificate_bytes, padding.PKCS1v15(), cert.signature_hash_algorithm)
    if hostname not in cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value.get_values_for_type(x509.DNSName):
        raise ValueError('TLS certificate does not cover the configured hostname')
    if ExtendedKeyUsageOID.SERVER_AUTH not in cert.extensions.get_extension_for_class(x509.ExtendedKeyUsage).value:
        raise ValueError('TLS certificate lacks server authentication purpose')
    def public(value):
        return value.public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    if public(key.public_key()) != public(cert.public_key()):
        raise ValueError('TLS private key does not match certificate')


def ensure_tls(env):
    name = env['_name']
    hostname = env['tls_hostname']
    if not re.fullmatch(r'[A-Za-z0-9_-]+', name) or not re.fullmatch(r'[a-z0-9][a-z0-9.-]*\.test', hostname):
        raise ValueError('Invalid environment name or .test TLS hostname')
    directory = ROOT / '.state/tls' / name
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    directory.chmod(0o700)
    existing = kubectl(env, '-n', 'demo', 'get', 'secret', 'hello-tls', '--ignore-not-found', '-o', 'json', capture=True).stdout
    files = {'tls.crt': directory / 'tls.crt', 'tls.key': directory / 'tls.key', 'ca.crt': directory / 'ca.crt'}
    if existing.strip():
        data = json.loads(existing)['data']
        material = {k: base64.b64decode(data[k], validate=True) for k in files}
    elif all(path.exists() for path in files.values()):
        material = {k: path.read_bytes() for k, path in files.items()}
    elif any(path.exists() for path in files.values()):
        raise ValueError('Incomplete local TLS state; refusing silent certificate rotation')
    else:
        now = dt.datetime.now(dt.timezone.utc)
        ca_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'MTS Demo CA')])
        ca = (x509.CertificateBuilder().subject_name(subject).issuer_name(subject)
              .public_key(ca_key.public_key()).serial_number(x509.random_serial_number())
              .not_valid_before(now - dt.timedelta(minutes=5)).not_valid_after(now + dt.timedelta(days=3650))
              .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
              .add_extension(x509.KeyUsage(False, False, False, False, False, True, True, None, None), critical=True)
              .sign(ca_key, hashes.SHA256()))
        cert = (x509.CertificateBuilder().subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, hostname)]))
                .issuer_name(subject).public_key(key.public_key()).serial_number(x509.random_serial_number())
                .not_valid_before(now - dt.timedelta(minutes=5)).not_valid_after(now + dt.timedelta(days=365))
                .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
                .add_extension(x509.SubjectAlternativeName([x509.DNSName(hostname)]), critical=False)
                .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
                .sign(ca_key, hashes.SHA256()))
        def pem_key(value):
            return value.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
        material = {'tls.crt': cert.public_bytes(serialization.Encoding.PEM),
                    'tls.key': pem_key(key), 'ca.crt': ca.public_bytes(serialization.Encoding.PEM)}
        _private_write(directory / 'ca.key', pem_key(ca_key))
    validate_certificate(material['tls.crt'], material['tls.key'], material['ca.crt'], hostname)
    for field, path in files.items():
        _private_write(path, material[field])
    return {'enabled': True, 'hostname': hostname, 'secretName': 'hello-tls',
            'certificate': material['tls.crt'].decode(), 'privateKey': material['tls.key'].decode(),
            'caCertificate': material['ca.crt'].decode()}
