#!/usr/bin/env python3
"""Resolve explicit image tags to registry digests; never run during deployment."""
import argparse
import concurrent.futures
import hashlib
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import yaml

ACCEPT = ', '.join([
    'application/vnd.oci.image.index.v1+json',
    'application/vnd.docker.distribution.manifest.list.v2+json',
    'application/vnd.oci.image.manifest.v1+json',
    'application/vnd.docker.distribution.manifest.v2+json',
])


def resolve(ref):
    if '@sha256:' in ref:
        return ref, ref.split('@', 1)[1]
    name, tag = ref.rsplit(':', 1)
    if tag in ('latest', 'main', 'master'):
        raise ValueError(f'Floating tag: {ref}')
    parts = name.split('/')
    if '.' in parts[0] or ':' in parts[0]:
        host, repo = parts[0], '/'.join(parts[1:])
    else:
        host, repo = 'docker.io', name if '/' in name else 'library/' + name
    if host == 'docker.io':
        host = 'registry-1.docker.io'
    url = f'https://{host}/v2/{repo}/manifests/{tag}'
    headers = {'Accept': ACCEPT}
    try:
        response = urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=60)
    except urllib.error.HTTPError as error:
        if error.code != 401:
            raise
        challenge = dict(re.findall(r'(\w+)="([^"]+)"', error.headers['WWW-Authenticate']))
        realm = challenge.pop('realm')
        token = json.load(urllib.request.urlopen(realm + '?' + urllib.parse.urlencode(challenge), timeout=60))
        headers['Authorization'] = 'Bearer ' + (token.get('token') or token['access_token'])
        response = urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=60)
    body = response.read()
    digest = 'sha256:' + hashlib.sha256(body).hexdigest()
    expected = response.headers.get('Docker-Content-Digest', digest)
    if digest != expected:
        raise ValueError(f'Registry checksum mismatch: {ref}')
    print(ref, digest, flush=True)
    return ref, digest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('references', help='One explicit image tag per line')
    parser.add_argument('--output', default='config/images.lock.yaml')
    args = parser.parse_args()
    refs = sorted(set(Path(args.references).read_text().splitlines()))
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        result = dict(pool.map(resolve, filter(None, refs)))
    Path(args.output).write_text(yaml.safe_dump({'schema_version': 1, 'images': result}, sort_keys=True))


if __name__ == '__main__':
    main()
