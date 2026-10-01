"""Shared local orchestration; never selects an implicit Kubernetes context."""
import contextlib
import fcntl
import hashlib
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


def command(argv, *, capture=False, timeout=900, **kwargs):
    return subprocess.run([str(x) for x in argv], check=True, text=True,
                          capture_output=capture, timeout=timeout, **kwargs)


def tool(name):
    local = ROOT / '.cache/tools' / name
    return str(local) if local.exists() else name


def environment(name):
    path = ROOT / 'config/environments' / (name + '.local.yaml')
    env = yaml.safe_load(path.read_text())
    for key in ('node_name', 'node_ip', 'ssh_host', 'ssh_user', 'kubeconfig'):
        if not env.get(key):
            raise ValueError(f'{path}: missing {key}')
    env.setdefault('node_port', 30080)
    env.setdefault('data_root', '/var/lib/mts-stack')
    env.setdefault('ssh_port', 22)
    env.setdefault('pod_cidr', '10.244.0.0/16')
    env.setdefault('service_cidr', '10.96.0.0/12')
    for key in ('kubeconfig', 'ssh_private_key_file'):
        if env.get(key):
            p = Path(os.path.expanduser(env[key]))
            env[key] = str(p if p.is_absolute() else ROOT / p)
    env['_file'] = str(path)
    return env


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
    if '@sha256:' in ref:
        if ref.split('@')[1] not in IMAGES.values():
            raise ValueError(f'Image digest absent from lock: {ref}')
        return ref
    if ref not in IMAGES:
        raise ValueError(f'Image absent from lock: {ref}')
    return ref.rsplit(':', 1)[0] + '@' + IMAGES[ref]


def lock_document(node):
    if isinstance(node, dict):
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
                                    '-n', namespace, 'port-forward', '--address=127.0.0.1',
                                    'service/' + service, ':' + str(remote_port)],
                                   stdout=stream, stderr=stream, text=True)
        try:
            import re
            until = time.monotonic() + 30
            while time.monotonic() < until:
                stream.flush()
                text = log.read_text()
                match = re.search(r'Forwarding from 127.0.0.1:(\d+)', text)
                if match:
                    yield 'http://127.0.0.1:' + match.group(1)
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
