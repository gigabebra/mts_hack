#!/usr/bin/env python3
"""Verify the base stack, TLS and routing without changing the deployment."""
import argparse
import datetime as dt
import sys
import uuid

from stacklib import ROOT, code_identity, command, environment, write_json
from release_control import assert_target, current_weights


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--env', default='ubuntu')
    args = parser.parse_args()
    run_id = 'release-' + uuid.uuid4().hex[:12]
    directory = ROOT / 'artifacts/release' / run_id
    directory.mkdir(parents=True)
    report = {'run_id': run_id, **code_identity(),
              'scope': ['mvp', 'tls-routing'], 'status': 'fail', 'checks': [],
              'started_at': dt.datetime.now(dt.timezone.utc).isoformat()}
    try:
        env = environment(args.env)
        assert_target(env)
        weights = current_weights(env)
        command([sys.executable, ROOT / 'scripts/verify.py', '--env', args.env, '--run-id', run_id], timeout=1200)
        report['checks'].append({'name': 'mvp', 'status': 'pass', 'report': 'artifacts/verify/' + run_id + '/report.json'})
        argv = [sys.executable, ROOT / 'scripts/check-gateway.final.py', '--env', args.env,
                '--output', directory / 'gateway.json']
        if weights == {'stable': 90, 'canary': 10}:
            argv += ['--split']
        elif weights == {'stable': 100, 'canary': 0}:
            argv += ['--requests', '100']
        else:
            raise ValueError('Unsupported observed traffic state')
        command(argv, timeout=420)
        report['checks'].append({'name': 'tls_and_routes', 'status': 'pass', 'weights': weights})
        report['status'] = 'pass'
    except Exception as error:
        report['checks'].append({'name': 'release_verification', 'status': 'fail', 'error': str(error)})
    report['finished_at'] = dt.datetime.now(dt.timezone.utc).isoformat()
    write_json(directory / 'report.json', report)
    print(report['status'].upper(), directory / 'report.json')
    return 0 if report['status'] == 'pass' else 1


if __name__ == '__main__':
    sys.exit(main())
