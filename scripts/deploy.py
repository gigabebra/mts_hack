#!/usr/bin/env python3
"""Install the locked MVP in order, using one explicit target and one writer."""
import argparse
import os
import sys
from pathlib import Path
import yaml
from stacklib import (ROOT, VERSIONS, command, deployment_lock, download,
                      environment, kubectl, resource, tool)


def install(env, release, namespace, chart, values=None, overrides=None):
    argv = [tool('helm'), 'upgrade', '--install', release, chart,
            '--namespace', namespace, '--kubeconfig', env['kubeconfig'],
            '--skip-crds', '--post-renderer', ROOT / 'scripts/post-render.py',
            '--wait', '--timeout', '10m', '--history-max', '5']
    if values:
        argv += ['-f', ROOT / values]
    if overrides:
        path = ROOT / '.state' / ('values-' + release + '.yaml')
        path.write_text(yaml.safe_dump(overrides))
        argv += ['-f', path]
    command(argv, timeout=700)


def deploy(env):
    nodes = resource(env, 'nodes')['items']
    if [x['metadata']['name'] for x in nodes] != [env['node_name']]:
        raise ValueError('Refusing deploy: explicit target is not the expected single-node cluster')
    for node in nodes:
        if not any(c['type'] == 'Ready' and c['status'] == 'True' for c in node['status']['conditions']):
            raise ValueError('Node is not Ready; run make bootstrap/health first')
    for name in ('edge', 'demo', 'monitoring', 'logging'):
        manifest = yaml.safe_dump({'apiVersion': 'v1', 'kind': 'Namespace', 'metadata': {'name': name}})
        kubectl(env, 'apply', '-f', '-', capture=False, input=manifest)

    install(env, 'storage', 'default', ROOT / 'deploy/storage',
            overrides={'nodeName': env['node_name'], 'dataRoot': env['data_root']})

    gateway_crds = download(VERSIONS['downloads']['gateway_api'], 'gateway-standard.yaml')
    kubectl(env, 'apply', '--server-side', '--field-manager=mts-crds', '-f', gateway_crds)
    for crd in ('gatewayclasses', 'gateways', 'httproutes', 'referencegrants', 'grpcroutes', 'backendtlspolicies'):
        kubectl(env, 'wait', '--for=condition=Established', 'crd/' + crd + '.gateway.networking.k8s.io', '--timeout=90s')

    monitoring_chart = download(VERSIONS['charts']['monitoring'], 'kube-prometheus-stack.tgz')
    crds = command([tool('helm'), 'show', 'crds', monitoring_chart], capture=True).stdout
    if not crds.strip():
        raise ValueError('Monitoring archive contains no CRDs')
    kubectl(env, 'apply', '--server-side', '--field-manager=mts-crds', '-f', '-', input=crds)
    for crd in ('prometheuses', 'servicemonitors', 'podmonitors', 'prometheusrules'):
        kubectl(env, 'wait', '--for=condition=Established', 'crd/' + crd + '.monitoring.coreos.com', '--timeout=90s')
    install(env, 'monitoring', 'monitoring', monitoring_chart, 'deploy/monitoring/values.yaml',
            overrides={'crds': {'enabled': False}})

    traefik_chart = download(VERSIONS['charts']['traefik'], 'traefik.tgz')
    install(env, 'traefik', 'edge', traefik_chart, 'deploy/gateway/traefik-values.yaml',
            overrides={'providers': {'kubernetesGateway': {'statusAddress': {'ip': env['node_ip']}}},
                       'ports': {'web': {'nodePort': env['node_port']}}})
    install(env, 'monitoring-extras', 'monitoring', ROOT / 'deploy/monitoring/extras')
    install(env, 'logging', 'logging', ROOT / 'deploy/logging',
            overrides={'dataRoot': env['data_root'],
                       'images': {'loki': VERSIONS['images']['loki'], 'fluentd': VERSIONS['images']['fluentd']}})
    install(env, 'app', 'demo', ROOT / 'deploy/app', overrides={'image': VERSIONS['images']['nginx']})
    install(env, 'gateway', 'demo', ROOT / 'deploy/gateway')
    for namespace in ('edge', 'demo', 'monitoring', 'logging'):
        kubectl(env, '-n', namespace, 'wait', '--for=condition=Ready', 'pods', '--all', '--timeout=300s', timeout=330)
    print('Deploy complete. Run make verify ENV=' + env['_name'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--env', default='ubuntu')
    args = parser.parse_args()
    os.environ['PATH'] = str(ROOT / '.venv/bin') + ':' + str(ROOT / '.cache/tools') + ':' + os.environ['PATH']
    env = environment(args.env)
    env['_name'] = args.env
    with deployment_lock(args.env):
        deploy(env)


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print(f'Deploy failed: {error}', file=sys.stderr)
        sys.exit(1)
