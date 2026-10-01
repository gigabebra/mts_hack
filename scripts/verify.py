#!/usr/bin/env python3
"""Fail-closed MVP acceptance with one correlated HTTP/metrics/logs run."""
import argparse
import contextlib
import datetime as dt
import json
import os
import subprocess
import sys
import time
import urllib.request
import uuid

from stacklib import ROOT, VERSIONS, command, direct_api_env, environment, forward, http_url, internal_http, kubectl, resource, write_json
from network_checks import network_evidence

HTTP = internal_http()


def now():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def check(report, name, expected, operation):
    try:
        actual = operation()
        entry = {'name': name, 'status': 'pass', 'expected': expected, 'actual': actual}
    except Exception as error:
        entry = {'name': name, 'status': 'fail', 'expected': expected, 'actual': str(error)[-5000:]}
    report['checks'].append(entry)
    print(entry['status'].upper(), name, flush=True)
    return entry['status'] == 'pass'


def component(script, path, *args):
    result = subprocess.run([sys.executable, str(ROOT / 'scripts' / script), *map(str, args),
                             '--output', str(path)], capture_output=True, text=True, timeout=240,
                            env=direct_api_env())
    if not path.exists():
        raise RuntimeError(script + ' produced no JSON: ' + result.stderr[-2000:])
    data = json.loads(path.read_text())
    if result.returncode or data.get('status') != 'pass':
        raise RuntimeError(json.dumps(data, ensure_ascii=False)[-4500:])
    return data


def readiness(env):
    result = {}
    nodes = resource(env, 'nodes')['items']
    if len(nodes) != 1 or nodes[0]['metadata']['name'] != env['node_name']:
        raise ValueError('Wrong cluster: expected the configured single node')
    if not any(c['type'] == 'Ready' and c['status'] == 'True' for c in nodes[0]['status']['conditions']):
        raise ValueError('Node is not Ready')
    result['node'] = nodes[0]['status']['nodeInfo']
    required = {'edge': [('deployment', 'traefik')], 'demo': [('deployment', 'hello')],
                'monitoring': [('deployment', 'monitoring-operator'), ('deployment', 'monitoring-kube-state-metrics'),
                               ('daemonset', 'monitoring-prometheus-node-exporter'), ('statefulset', 'prometheus-monitoring-prometheus')],
                'logging': [('deployment', 'loki'), ('daemonset', 'fluentd')]}
    for ns, workloads in required.items():
        for kind, name in workloads:
            kubectl(env, '-n', ns, 'rollout', 'status', f'{kind}/{name}', '--timeout=120s', timeout=140, capture=True)
        pods = resource(env, 'pods', '-n', ns)['items']
        if not pods:
            raise ValueError('No pods in ' + ns)
        for pod in pods:
            if pod.get('status', {}).get('phase') == 'Succeeded':
                continue
            if not any(c['type'] == 'Ready' and c['status'] == 'True' for c in pod.get('status', {}).get('conditions', [])):
                raise ValueError('Unready pod: ' + ns + '/' + pod['metadata']['name'])
        result[ns] = [{'name': p['metadata']['name'], 'phase': p.get('status', {}).get('phase')} for p in pods]
    for ns in ('monitoring', 'logging'):
        pvcs = resource(env, 'pvc', '-n', ns)['items']
        if not pvcs or any(p.get('status', {}).get('phase') != 'Bound' for p in pvcs):
            raise ValueError('Persistent storage is not Bound in ' + ns)
    return result


def runtime(env):
    pods = resource(env, 'pods', '-n', 'demo', '-l', 'app.kubernetes.io/name=hello')['items']
    if not pods:
        raise ValueError('No application pods')
    result = []
    for pod in pods:
        name = pod['metadata']['name']
        script = 'id -u; cat /proc/1/status; cat /proc/1/mountinfo; if test -e /var/run/secrets/kubernetes.io/serviceaccount/token; then echo TOKEN_PRESENT; else echo TOKEN_ABSENT; fi'
        out = kubectl(env, '-n', 'demo', 'exec', name, '-c', 'nginx', '--', 'sh', '-c', script, capture=True).stdout
        lines = out.splitlines()
        uid = int(lines[0])
        process_uids = next(line for line in lines if line.startswith('Uid:')).split()[1:]
        caps = next(line for line in lines if line.startswith('CapEff:')).split()[1]
        root = next(line for line in lines if len(line.split()) > 6 and line.split()[4] == '/')
        options = root.split()[5].split(',')
        if uid == 0 or any(int(x) == 0 for x in process_uids) or int(caps, 16) != 0 or 'ro' not in options or 'TOKEN_ABSENT' not in lines:
            raise ValueError(f'Runtime invariants failed in {name}: UID={uid}, PID1={process_uids}, caps={caps}, root={options}')
        result.append({'pod': name, 'exec_uid': uid, 'pid1_uids': process_uids, 'cap_effective': caps,
                       'root_mount_options': options, 'serviceaccount_token': 'absent'})
    return result


def diagnostics(env, directory):
    # No secrets, configmaps, kubeconfig, environment dump, or unbounded pod logs.
    for ns in ('edge', 'demo', 'monitoring', 'logging'):
        try:
            out = kubectl(env, '-n', ns, 'get', 'events', '--sort-by=.lastTimestamp', capture=True, timeout=15).stdout
            (directory / ('events-' + ns + '.txt')).write_text(out[-12000:])
        except Exception:
            pass


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--env', default='ubuntu')
    parser.add_argument('--profile', default='mvp')
    parser.add_argument('--run-id')
    args = parser.parse_args()
    run_id = args.run_id or dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + uuid.uuid4().hex[:8]
    if not all(c.isalnum() or c in '._-' for c in run_id) or not 1 <= len(run_id) <= 48:
        parser.error('run-id must be 1..48 ASCII letters/digits/._-')
    directory = ROOT / 'artifacts/verify' / run_id
    directory.mkdir(parents=True, exist_ok=False)
    report = {'schema_version': 1, 'run_id': run_id, 'request_id': run_id, 'profile': args.profile,
              'revision': command(['git', 'rev-parse', 'HEAD'], capture=True).stdout.strip(),
              'working_tree_dirty': bool(command(['git', 'status', '--porcelain'], capture=True).stdout),
              'started_at': now(), 'versions': VERSIONS, 'checks': [], 'status': 'fail'}
    env = None
    try:
        if args.profile != 'mvp':
            raise ValueError('Unsupported profile: only mvp is implemented; final checks cannot be skipped')
        env = environment(args.env)
        report['environment'] = {'name': args.env, 'node_name': env['node_name']}
        base = http_url(env['node_ip'], env['node_port'])
        check(report, 'readiness', 'Expected node, workloads Ready, PVC Bound', lambda: readiness(env))
        check(report, 'dual_stack_network', 'IPv6-primary API and dual-stack Node, Pods, Services, EndpointSlices and Calico pools',
              lambda: network_evidence(env, directory))
        common = ['--kubeconfig', env['kubeconfig'], '--base-url', base, '--run-id', run_id,
                  '--expected-address', env['node_ip'],
                  '--request-id', run_id, '--error-marker', run_id + '-err', '--timeout', '90']
        common4 = ['--kubeconfig', env['kubeconfig'], '--base-url', http_url(env['node_ipv4'], env['node_port']),
                   '--run-id', run_id, '--request-id', run_id + '-v4', '--error-marker', run_id + '-v4-err', '--timeout', '90']
        check(report, 'gateway_conditions', 'Current Accepted/Programmed/ResolvedRefs',
              lambda: component('check-gateway.py', directory / 'gateway-conditions.json', '--mode', 'conditions', *common))
        with contextlib.ExitStack() as stack:
            prometheus = stack.enter_context(forward(env, 'monitoring', 'monitoring-prometheus', 9090, directory))
            loki = stack.enter_context(forward(env, 'logging', 'loki', 3100, directory))
            def warmup():
                req = urllib.request.Request(base + '/', headers={'X-Request-ID': run_id + '-warmup'})
                with HTTP.open(req, timeout=10) as response:
                    if response.status != 200 or response.read() != b'Hello World!':
                        raise ValueError('Warm-up did not reach the expected app')
                return 'HTTP 200; exact body'
            check(report, 'metrics_warmup', 'Create a real counter series before baseline', warmup)
            baseline = directory / 'metrics-baseline.json'
            snap = check(report, 'metrics_baseline', 'Healthy targets and fresh counter',
                         lambda: component('check-metrics.py', baseline, '--mode', 'snapshot',
                                           '--prometheus-url', prometheus, '--run-id', run_id, '--timeout', '90'))
            start = time.time()
            traffic = directory / 'gateway-traffic.json'
            traffic_ok = check(report, 'http', 'Exact response, Request-ID, traffic, true missing-file 404, unmatched route',
                               lambda: component('check-gateway.py', traffic, '--mode', 'traffic', '--requests', '20', *common))
            traffic4 = directory / 'gateway-traffic-ipv4.json'
            traffic4_ok = check(report, 'http_ipv4', 'Same application behavior over native IPv4',
                                lambda: component('check-gateway.py', traffic4, '--mode', 'traffic', '--requests', '20', *common4))
            if snap and traffic_ok and traffic4_ok:
                count = sum(json.loads(p.read_text())['data']['request_count'] for p in (traffic, traffic4))
                check(report, 'metrics_delta', 'Fresh scoped counter increases by generated requests without reset',
                      lambda: component('check-metrics.py', directory / 'metrics-verify.json', '--mode', 'verify',
                                        '--prometheus-url', prometheus, '--run-id', run_id, '--baseline', baseline,
                                        '--min-increase', count, '--timeout', '120'))
            else:
                report['checks'].append({'name': 'metrics_delta', 'status': 'fail', 'expected': 'Valid baseline and traffic',
                                         'actual': 'Dependency failed; cannot prove counter increment'})
            check(report, 'logs', 'Same Nginx Request-ID access record and independent stderr error in Loki',
                  lambda: component('check-logs.py', directory / 'logs.json', '--loki-url', loki,
                                    '--run-id', run_id, '--request-id', run_id, '--error-marker', run_id + '-err',
                                    '--start-time', start, '--timeout', '120'))
            check(report, 'logs_ipv4', 'IPv4 HTTP Request-ID and independent stderr marker in Loki',
                  lambda: component('check-logs.py', directory / 'logs-ipv4.json', '--loki-url', loki,
                                    '--run-id', run_id, '--request-id', run_id + '-v4', '--error-marker', run_id + '-v4-err',
                                    '--start-time', start, '--timeout', '120'))
        check(report, 'runtime_security', 'Non-root PID1, zero capabilities, root mount ro, API token absent', lambda: runtime(env))
    except Exception as error:
        report['checks'].append({'name': 'orchestration', 'status': 'fail', 'expected': 'All required checks execute', 'actual': str(error)})
    finally:
        required = {'readiness', 'dual_stack_network', 'gateway_conditions', 'metrics_warmup', 'metrics_baseline',
                    'http', 'http_ipv4', 'metrics_delta', 'logs', 'logs_ipv4', 'runtime_security'}
        missing = required - {c['name'] for c in report['checks']}
        for name in sorted(missing):
            report['checks'].append({'name': name, 'status': 'fail', 'expected': 'Required MVP check', 'actual': 'Not executed because prerequisite failed'})
        report['status'] = 'pass' if all(c['status'] == 'pass' for c in report['checks']) else 'fail'
        report['finished_at'] = now()
        write_json(directory / 'report.json', report)
        if report['status'] != 'pass' and env:
            diagnostics(env, directory)
        print(f"{report['status'].upper()}: {directory / 'report.json'}", flush=True)
    return 0 if report['status'] == 'pass' else 1


if __name__ == '__main__':
    sys.exit(main())
