#!/usr/bin/env python3
"""Local syntax, Helm render and reviewed image-lock checks without a cluster."""
import os
from pathlib import Path
import py_compile
import subprocess
import sys
import yaml
from stacklib import ROOT, VERSIONS, command, download, lock_document, tool

os.environ['PATH'] = str(ROOT / '.venv/bin') + ':' + str(ROOT / '.cache/tools') + ':' + os.environ['PATH']
os.chdir(ROOT)
for path in list((ROOT / 'scripts').glob('*.py')) + list((ROOT / 'ansible').rglob('*.py')):
    py_compile.compile(str(path), doraise=True)

for folder in ('config', 'ansible'):
    for path in (ROOT / folder).rglob('*.yaml'):
        if '.local.' not in path.name:
            list(yaml.safe_load_all(path.read_text()))

charts = [('storage', 'default', ROOT / 'deploy/storage', []),
          ('app', 'demo', ROOT / 'deploy/app', []),
          ('gateway', 'demo', ROOT / 'deploy/gateway', []),
          ('logging', 'logging', ROOT / 'deploy/logging', []),
          ('monitoring-extras', 'monitoring', ROOT / 'deploy/monitoring/extras', [])]
for key, namespace, values in [('traefik', 'edge', 'deploy/gateway/traefik-values.yaml'),
                               ('monitoring', 'monitoring', 'deploy/monitoring/values.yaml')]:
    archive = download(VERSIONS['charts'][key], key + '.tgz')
    charts.append((key, namespace, archive, ['-f', str(ROOT / values)]))

outdir = ROOT / 'artifacts/render'
outdir.mkdir(parents=True, exist_ok=True)
for release, namespace, chart, values in charts:
    command([tool('helm'), 'lint', chart, *values], capture=False)
    rendered = command([tool('helm'), 'template', release, chart, '-n', namespace,
                        '--kube-version', VERSIONS['platform']['kubernetes'], *values], capture=True).stdout
    documents = [lock_document(x) for x in yaml.safe_load_all(rendered) if x]
    # Secrets rendered by these MVP charts contain no credentials; do not preserve any.
    documents = [x for x in documents if x.get('kind') != 'Secret']
    (outdir / (release + '.yaml')).write_text(yaml.safe_dump_all(documents, sort_keys=False))

command([ROOT / '.venv/bin/ansible-playbook', '-i', 'localhost,', 'ansible/bootstrap.yaml', '--syntax-check'])
command([ROOT / '.venv/bin/ansible-playbook', '-i', 'localhost,', 'ansible/health.yaml', '--syntax-check'])
for test in sorted((ROOT / 'deploy').glob('*/tests/test_*.py')):
    command([sys.executable, test], timeout=180)
print('PASS: Python/Ansible syntax, Helm lint/render, locked images, component negative checks')
