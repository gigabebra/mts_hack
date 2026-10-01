#!/usr/bin/env python3
"""Run deploy twice and prove stable resources/PVCs plus full verification."""
import argparse
import datetime as dt
import hashlib
import json
import sys
import uuid
from stacklib import ROOT, command, environment, resource, write_json


def snapshot(env):
    records = {}
    for namespace in ('edge', 'demo', 'monitoring', 'logging'):
        for kind in ('deployment', 'statefulset', 'daemonset', 'service', 'pvc', 'secret'):
            for item in resource(env, kind, '-n', namespace)['items']:
                if item['metadata']['name'].startswith('sh.helm.release.'):
                    continue
                key = namespace + '/' + kind + '/' + item['metadata']['name']
                record = {'uid': item['metadata']['uid']}
                if kind == 'pvc':
                    record['volume'] = item['spec'].get('volumeName')
                    record['phase'] = item['status'].get('phase')
                if kind == 'secret':
                    # Compare locally; never emit secret bytes or hashes in artifacts.
                    record['_content'] = hashlib.sha256(json.dumps(item.get('data', {}), sort_keys=True).encode()).hexdigest()
                records[key] = record
    for item in resource(env, 'pv')['items']:
        if item['metadata']['name'] in ('mts-prometheus', 'mts-loki'):
            records['pv/' + item['metadata']['name']] = {'uid': item['metadata']['uid'], 'claim': item['spec'].get('claimRef', {}).get('uid')}
    return records


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--env', default='ubuntu')
    args = parser.parse_args()
    run_id = dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + uuid.uuid4().hex[:8]
    path = ROOT / 'artifacts/idempotency' / run_id / 'report.json'
    report = {'schema_version': 1, 'run_id': run_id, 'status': 'fail', 'checks': [],
              'revision': command(['git', 'rev-parse', 'HEAD'], capture=True).stdout.strip()}
    try:
        env = environment(args.env)
        baseline = snapshot(env)
        if not any('/pvc/' in k for k in baseline):
            raise ValueError('No existing PVCs to compare; deploy first')
        for iteration in (1, 2):
            command([sys.executable, ROOT / 'scripts/deploy.py', '--env', args.env], timeout=2400)
            after = snapshot(env)
            if after != baseline:
                changed = sorted(k for k in set(baseline) | set(after) if baseline.get(k) != after.get(k))
                raise ValueError('Resource identity/content changed: ' + ', '.join(changed))
            command([sys.executable, ROOT / 'scripts/verify.py', '--env', args.env, '--run-id', run_id + '-' + str(iteration)], timeout=1200)
            report['checks'].append({'name': 'deploy_' + str(iteration), 'status': 'pass',
                                     'actual': 'Resource UIDs/PVC bindings/secrets preserved; full verify pass'})
        report['resources'] = {k: {field: value for field, value in rec.items() if not field.startswith('_')} for k, rec in baseline.items()}
        report['status'] = 'pass'
    except Exception as error:
        report['checks'].append({'name': 'idempotency', 'status': 'fail', 'actual': str(error)})
    write_json(path, report)
    print(report['status'].upper(), path)
    return 0 if report['status'] == 'pass' else 1


if __name__ == '__main__':
    sys.exit(main())
