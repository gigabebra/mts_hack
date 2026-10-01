#!/usr/bin/env python3
"""Bounded MVP storage/collector checks on the explicitly selected demo cluster."""
import argparse
import contextlib
import json
from pathlib import Path
import subprocess
import sys
import time
import urllib.parse
import urllib.request
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts'))
from stacklib import ROOT, command, deployment_lock, environment, forward, http_url, kubectl, resource, write_json

HTTP = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def api(url, path, params=None):
    if params:
        path += '?' + urllib.parse.urlencode(params)
    with HTTP.open(url + path, timeout=10) as response:
        return json.load(response)


def poll(operation, timeout=120):
    until = time.monotonic() + timeout
    reason = 'No result'
    while time.monotonic() < until:
        try:
            result = operation()
            if result:
                return result
        except Exception as error:
            reason = str(error)
        time.sleep(2)
    raise TimeoutError(reason)


def http(base, marker):
    with HTTP.open(urllib.request.Request(base + '/', headers={'X-Request-ID': marker}), timeout=10) as response:
        if response.read() != b'Hello World!' or response.headers.get('X-Request-ID') != marker:
            raise ValueError('Unexpected Gateway response')


def logs(url, marker, start):
    result = api(url, '/loki/api/v1/query_range', {'query': '{app="hello",namespace="demo",stream="stdout"} |= ' + json.dumps(marker),
                                                 'start': str(int(start * 1e9)), 'end': str(time.time_ns()), 'limit': '100'})
    if result.get('status') != 'success':
        raise ValueError('Loki query failed')
    return [value for stream in result['data']['result'] for value in stream['values'] if marker in value[1]]


def selected_pods(env, namespace, selector):
    return resource(env, 'pods', '-n', namespace, '-l', selector)['items']


def collector_state(env):
    position_text = kubectl(env, '-n', 'logging', 'exec', 'daemonset/fluentd', '-c', 'fluentd',
                           '--', 'cat', '/var/lib/fluentd/nginx.pos', capture=True).stdout
    positions = {}
    for line in position_text.splitlines():
        path, offset, inode = line.split('\t')
        positions[(path, inode)] = int(offset, 16)
    ruby = 'j=JSON.parse(Net::HTTP.get(URI("http://[::1]:24220/api/plugins.json"))); puts JSON.generate(j["plugins"].find{|p| p["plugin_id"]=="nginx_cri"})'
    metrics = json.loads(kubectl(env, '-n', 'logging', 'exec', 'daemonset/fluentd', '-c', 'fluentd',
                                '--', 'ruby', '-rnet/http', '-rjson', '-e', ruby, capture=True).stdout)
    return positions, metrics['emit_records']


def replace_pod(env, namespace, selector, workload):
    pods = selected_pods(env, namespace, selector)
    if len(pods) != 1:
        raise ValueError('Expected one pod for recovery target ' + workload)
    old = pods[0]['metadata']['uid']
    kubectl(env, '-n', namespace, 'delete', 'pod', pods[0]['metadata']['name'], '--wait=true', '--timeout=90s')
    kubectl(env, '-n', namespace, 'rollout', 'status', workload, '--timeout=180s')
    def changed():
        current = selected_pods(env, namespace, selector)
        return len(current) == 1 and current[0]['metadata']['uid'] != old and any(
            c['type'] == 'Ready' and c['status'] == 'True' for c in current[0].get('status', {}).get('conditions', []))
    poll(changed, 180)
    return old


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--env', default='ubuntu')
    args = parser.parse_args()
    env = environment(args.env)
    run_id = 'recovery-' + uuid.uuid4().hex[:12]
    directory = ROOT / 'artifacts/recovery' / run_id
    directory.mkdir(parents=True)
    report = {'run_id': run_id, 'revision': command(['git', 'rev-parse', 'HEAD'], capture=True).stdout.strip(),
              'status': 'fail', 'checks': [], 'started_at': time.time()}
    base = http_url(env['node_ip'], env['node_port'])
    try:
        with deployment_lock(args.env):
            nodes = resource(env, 'nodes')['items']
            if len(nodes) != 1 or nodes[0]['metadata']['name'] != env['node_name']:
                raise ValueError('Wrong cluster identity')
            start = time.time()
            marker = run_id + '-persist'
            http(base, marker)
            with forward(env, 'logging', 'loki', 3100, directory) as loki:
                original_logs = poll(lambda: logs(loki, marker, start))
            timestamp = time.time()
            query = 'node_memory_MemAvailable_bytes{namespace="monitoring"}[60s]'
            with forward(env, 'monitoring', 'monitoring-prometheus', 9090, directory) as prometheus:
                before = api(prometheus, '/api/v1/query', {'query': query, 'time': timestamp})
                if before.get('status') != 'success' or not before['data']['result']:
                    raise ValueError('No historical infrastructure samples to preserve')
            replace_pod(env, 'monitoring', 'prometheus=monitoring-prometheus', 'statefulset/prometheus-monitoring-prometheus')
            with forward(env, 'monitoring', 'monitoring-prometheus', 9090, directory) as prometheus:
                after = poll(lambda: api(prometheus, '/api/v1/query', {'query': query, 'time': timestamp}))
            if before['data'] != after['data']:
                raise ValueError('Historical Prometheus samples changed/disappeared after pod replacement')
            report['checks'].append({'name': 'prometheus_history', 'status': 'pass', 'query': query,
                                     'query_time': timestamp, 'series_count': len(before['data']['result'])})

            replace_pod(env, 'logging', 'app.kubernetes.io/name=loki', 'deployment/loki')
            with forward(env, 'logging', 'loki', 3100, directory) as loki:
                preserved = poll(lambda: logs(loki, marker, start))
                if not set(map(tuple, original_logs)).issubset(set(map(tuple, preserved))):
                    raise ValueError('Old Loki records not preserved')
            report['checks'].append({'name': 'loki_history', 'status': 'pass', 'request_id': marker})

            # A short outage proves the bounded persistent buffer, including a collector restart.
            desired = resource(env, 'deployment', 'loki', '-n', 'logging')['spec']['replicas']
            if desired != 1:
                raise ValueError('Loki must start with one replica')
            markers = [run_id + '-buffer-' + str(i) for i in range(5)]
            outage_start = time.time()
            try:
                kubectl(env, '-n', 'logging', 'scale', 'deployment/loki', '--replicas=0')
                poll(lambda: not selected_pods(env, 'logging', 'app.kubernetes.io/name=loki'), 90)
                old_positions, old_emitted = collector_state(env)
                for item in markers:
                    http(base, item)
                # Require successful tail consumption before testing persistent buffer/positions.
                def consumed():
                    positions, emitted = collector_state(env)
                    return (positions, emitted) if emitted >= old_emitted + len(markers) else None
                saved_positions, _ = poll(consumed, 30)
                if not saved_positions or saved_positions == old_positions:
                    raise ValueError('Tail positions did not advance during the Loki outage')
                replace_pod(env, 'logging', 'app.kubernetes.io/name=fluentd', 'daemonset/fluentd')
                time.sleep(6)  # One tail refresh interval; no new application traffic here.
                restored_positions, new_emitted = collector_state(env)
                if restored_positions != saved_positions or new_emitted != 0:
                    raise ValueError('Collector replayed old source records or lost saved tail positions')
                report['checks'].append({'name': 'collector_positions', 'status': 'pass',
                                         'position_files': len(saved_positions), 'records_replayed_after_restart': new_emitted})
            finally:
                kubectl(env, '-n', 'logging', 'scale', 'deployment/loki', '--replicas=1')
                kubectl(env, '-n', 'logging', 'rollout', 'status', 'deployment/loki', '--timeout=180s')
            with forward(env, 'logging', 'loki', 3100, directory) as loki:
                counts = {item: len(poll(lambda marker=item: logs(loki, marker, outage_start), 180)) for item in markers}
            report['checks'].append({'name': 'collector_buffer_restart', 'status': 'pass', 'request_ids': markers,
                                     'matches': counts, 'elapsed_seconds': round(time.time() - outage_start, 2),
                                     'guarantee': 'Only this bounded outage tested; duplicates permitted'})
        command([sys.executable, ROOT / 'scripts/verify.py', '--env', args.env, '--run-id', run_id + '-after'], timeout=1200)
        report['checks'].append({'name': 'after_recovery_verify', 'status': 'pass'})
        report['status'] = 'pass'
    except Exception as error:
        report['checks'].append({'name': 'recovery', 'status': 'fail', 'actual': str(error)})
    report['finished_at'] = time.time()
    write_json(directory / 'report.json', report)
    print(report['status'].upper(), directory / 'report.json')
    return 0 if report['status'] == 'pass' else 1


if __name__ == '__main__':
    sys.exit(main())
