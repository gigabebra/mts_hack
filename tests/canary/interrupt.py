#!/usr/bin/env python3
"""Prove recovery from real interrupted Helm writes on the explicit test VM."""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts'))
import canary
from release_control import assert_target, candidate_defaults, current_weights, helm_info, install_candidate, set_weights
from stacklib import ROOT, deployment_lock, environment, locked_image, resource, write_json

STALL_IMAGE = 'docker.io/library/busybox:1.37.0'


def terminate_tree(process, grace=15):
    """Terminate only this test's process group before another writer starts."""
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        process.wait(timeout=grace)
    except subprocess.TimeoutExpired:
        pass
    finally:
        # The parent may have exited while a Helm descendant remains alive.
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait(timeout=10)


def certificate_fingerprint(env):
    secret = resource(env, 'secret', 'hello-tls', '-n', 'demo')
    return hashlib.sha256(base64.b64decode(secret['data']['tls.crt'])).hexdigest()


def writer(env, revision):
    """Test-only incomplete writer; the next normal CLI must repair its release."""
    def terminate(_signal, _frame):
        raise SystemExit(143)
    signal.signal(signal.SIGTERM, terminate)
    with deployment_lock(env['_name']):
        assert_target(env)
        set_weights(env, 100, 0)
        values = candidate_defaults(env)
        values.update(image=STALL_IMAGE, releaseRevision=revision)
        # The image is locked, but has no nginx. Helm waits for readiness until
        # this specific writer is interrupted; stable receives all traffic.
        install_candidate(env, values)
    raise RuntimeError('Stalled candidate unexpectedly became Ready')


def pending_candidate(env, process, timeout=35):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError('Writer exited before an actual pending Helm upgrade was observed')
        info = helm_info(env, 'app-canary')
        deployment = resource(env, 'deployment', 'hello-canary', '-n', 'demo')
        if (info['status'] == 'pending-upgrade' and
                deployment['spec']['template']['spec']['containers'][0]['image'] == locked_image(STALL_IMAGE)):
            return {'status': info['status'], 'helm_revision': info['revision'], 'requested_image_observed': True}
        time.sleep(.3)
    raise TimeoutError('No real pending upgrade of the isolated candidate observed')


def cli(env, directory, run_id, *arguments):
    with (directory / (run_id + '.log')).open('w') as log:
        process = subprocess.Popen([sys.executable, str(ROOT / 'scripts/canary.py'), '--env', env['_name'],
                                    '--run-id', run_id, *arguments], stdout=log, stderr=subprocess.STDOUT,
                                   start_new_session=True)
        try:
            exit_code = process.wait(timeout=360)
        finally:
            terminate_tree(process)
    report = json.loads((ROOT / 'artifacts/canary' / run_id / 'report.json').read_text())
    return exit_code, report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--env', required=True)
    parser.add_argument('--writer', help=argparse.SUPPRESS)
    args = parser.parse_args()
    env = environment(args.env)
    if args.writer:
        writer(env, args.writer)
    def interrupted(_signal, _frame):
        raise KeyboardInterrupt()
    previous_handler = signal.signal(signal.SIGTERM, interrupted)
    run_id = 'interrupt-' + uuid.uuid4().hex[:12]
    directory = ROOT / 'artifacts/canary' / run_id
    directory.mkdir(parents=True)
    report = {'schema_version': 1, 'run_id': run_id, **canary.code_identity(),
              'kind': 'live_helm_interruption', 'status': 'fail', 'checks': [], 'started_at': canary.timestamp()}
    target_verified = False
    try:
        assert_target(env)
        target_verified = True
        code, baseline = cli(env, directory, run_id + '-baseline', '--action', 'restore')
        if code or baseline['decision'] != 'restored':
            raise RuntimeError('Interruption test requires a healthy baseline')
        fingerprint = certificate_fingerprint(env)
        for mode in ('same-run', 'next-run'):
            case_id = run_id + '-' + mode
            argv = ([sys.executable, str(ROOT / 'scripts/canary.py'), '--env', args.env, '--run-id', case_id,
                     '--revision', case_id, '--image', STALL_IMAGE] if mode == 'same-run' else
                    [sys.executable, str(Path(__file__).resolve()), '--env', args.env, '--writer', case_id])
            with (directory / (mode + '.log')).open('w') as log:
                process = subprocess.Popen(argv, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
                try:
                    pending = pending_candidate(env, process)
                    process.send_signal(signal.SIGTERM)
                    exit_code = process.wait(timeout=360)
                finally:
                    terminate_tree(process)
            observed = {'name': mode, 'pending_before_signal': pending, 'writer_exit_code': exit_code}
            if mode == 'same-run':
                case = json.loads((ROOT / 'artifacts/canary' / case_id / 'report.json').read_text())
                if exit_code != 2 or case['decision'] != 'interrupted' or case.get('rollback', {}).get('status') != 'pass':
                    raise RuntimeError('Interrupted CLI did not perform its verified recovery')
                observed['interrupted_report'] = str((ROOT / 'artifacts/canary' / case_id / 'report.json').relative_to(ROOT))
            else:
                still_pending = helm_info(env, 'app-canary')['status']
                if exit_code != 143 or still_pending != 'pending-upgrade':
                    raise RuntimeError('Next-run test did not leave a real pending Helm upgrade')
                observed['pending_after_writer_exit'] = still_pending
            restore_id = case_id + '-restore'
            code, restored = cli(env, directory, restore_id, '--action', 'restore')
            if code or restored['decision'] != 'restored' or restored.get('rollback', {}).get('status') != 'pass':
                raise RuntimeError('Normal restore CLI could not recover the interrupted write')
            if helm_info(env, 'app-canary')['status'] != 'deployed' or current_weights(env) != {'stable': 100, 'canary': 0}:
                raise RuntimeError('Candidate/route state was not restored')
            if certificate_fingerprint(env) != fingerprint:
                raise RuntimeError('Interruption recovery changed the TLS certificate')
            observed.update(status='pass', restore_report=str((ROOT / 'artifacts/canary' / restore_id / 'report.json').relative_to(ROOT)),
                            tls_certificate_preserved=True, weights={'stable': 100, 'canary': 0})
            report['checks'].append(observed)
            write_json(directory / 'report.json', report)
            print('PASS', mode, flush=True)
        report['status'] = 'pass'
    except BaseException as error:
        report['error'] = str(error) if isinstance(error, (RuntimeError, TimeoutError)) else canary.safe_error(error)
    finally:
        if target_verified:
            try:
                code, cleanup = cli(env, directory, run_id + '-cleanup', '--action', 'restore')
                report['cleanup'] = {'exit_code': code, 'decision': cleanup['decision'], 'status': cleanup['status']}
                if code or cleanup['status'] != 'pass':
                    report['status'] = 'fail'
            except Exception as error:
                report.update(status='fail', cleanup={'status': 'fail', 'error': canary.safe_error(error)})
        report['finished_at'] = canary.timestamp()
        write_json(directory / 'report.json', report)
        signal.signal(signal.SIGTERM, previous_handler)
    print(report['status'].upper(), directory / 'report.json')
    return 0 if report['status'] == 'pass' else 1


if __name__ == '__main__':
    sys.exit(main())
