#!/usr/bin/env python3
"""Run canary and recovery scenarios on the explicitly configured test VM."""
import argparse
import datetime as dt
import json
from pathlib import Path
import shlex
import signal
import subprocess
import sys
import threading
import time
import urllib.request
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts'))
from stacklib import ROOT, code_identity, command, deployment_lock, environment, http_url, internal_http, kubectl, resource, tool, write_json
from release_control import assert_target, helm_info, helm_values, install, set_weights
from process_control import terminate_tree


def http(env, revision=None):
    marker = 'demo-' + uuid.uuid4().hex[:16]
    request = urllib.request.Request(http_url(env['node_ip'], env['node_port']) + '/', headers={'X-Request-ID': marker})
    with internal_http().open(request, timeout=5) as response:
        if (response.status != 200 or response.read() != b'Hello World!'
                or response.headers.get('X-Request-ID') != marker or response.headers.get('X-Release') != 'stable'
                or (revision is not None and response.headers.get('X-Revision') != revision)):
            raise ValueError('Unexpected stable response during demo')


def wait_stable_revision(env, revision, previous_uids, report, timeout=60):
    """Wait for old pods to exit, then observe the new revision before smoke."""
    check = {'name': 'stable_revision_convergence', 'revision': revision,
             'status': 'fail', 'observations': []}
    report['checks'].append(check)
    started = time.monotonic()
    selector = 'app.kubernetes.io/name=hello,app.kubernetes.io/instance=app'
    while time.monotonic() - started < timeout:
        observation = {}
        try:
            def get(*args):
                return json.loads(kubectl(env, '--request-timeout=5s', 'get', *args,
                                          '-n', 'demo', '-o', 'json', capture=True, timeout=7).stdout)
            deployment = get('deployment', 'hello')
            pods = get('pods', '-l', selector)['items']
            status = deployment.get('status', {})
            observation['previous_pods_remaining'] = len(previous_uids & {p['metadata']['uid'] for p in pods})
            observation['deployment_ready'] = (
                status.get('observedGeneration') == deployment['metadata']['generation']
                and all(status.get(key) == 2 for key in ('replicas', 'updatedReplicas', 'readyReplicas', 'availableReplicas'))
                and len(pods) == 2 and not any(p['metadata'].get('deletionTimestamp') for p in pods))
            if observation['deployment_ready'] and not observation['previous_pods_remaining']:
                http(env, revision)
                observation['response_matches'] = True
                check.update(status='pass', seconds=round(time.monotonic() - started, 2))
                check['observations'].append(observation)
                return
        except Exception as error:
            observation['error'] = type(error).__name__
        check['observations'].append(observation)
        time.sleep(.5)
    raise TimeoutError('Stable revision did not converge before strict smoke')


def app_recovery(env, report):
    with deployment_lock(env['_name']):
        assert_target(env)
        set_weights(env, 100, 0)
        selector = 'app.kubernetes.io/name=hello,app.kubernetes.io/instance=app'
        before = resource(env, 'pods', '-n', 'demo', '-l', selector)['items']
        deployment = resource(env, 'deployment', 'hello', '-n', 'demo')
        if deployment['spec']['replicas'] != 2 or len(before) != 2:
            raise ValueError('App continuity demo requires exactly two stable replicas')
        results, failures = [], []
        stop = threading.Event()

        def traffic():
            while not stop.is_set():
                try:
                    http(env)
                    results.append(time.time())
                except Exception as error:
                    failures.append(type(error).__name__)
                stop.wait(0.05)

        worker = threading.Thread(target=traffic)
        worker.start()
        started = time.monotonic()
        try:
            removed = before[0]['metadata']
            kubectl(env, '-n', 'demo', 'delete', 'pod', removed['name'], '--wait=false', capture=True)
            deadline = time.monotonic() + 120
            while time.monotonic() < deadline:
                after = resource(env, 'pods', '-n', 'demo', '-l', selector)['items']
                ready = [pod for pod in after if not pod['metadata'].get('deletionTimestamp') and
                         any(c['type'] == 'Ready' and c['status'] == 'True' for c in pod.get('status', {}).get('conditions', []))]
                if (len(ready) == 2 and removed['uid'] not in {p['metadata']['uid'] for p in after}
                        and time.monotonic() - started >= 5):
                    break
                time.sleep(0.5)
            else:
                raise TimeoutError('Replacement app pod did not become Ready')
        finally:
            stop.set()
            worker.join(timeout=10)
        if worker.is_alive() or failures or len(results) < 30:
            raise ValueError(f'App replacement traffic failed: success={len(results)}, failures={failures[:5]}')
        report['checks'].append({'name': 'app_pod_replacement', 'status': 'pass', 'requests': len(results),
                                 'failures': len(failures), 'seconds': round(time.monotonic() - started, 2)})

        previous = helm_info(env, 'app')
        values = helm_values(env, 'app')
        original = values.get('releaseRevision', 'nginx-1.28.0-base')
        revision = 'demo-' + uuid.uuid4().hex[:12]
        candidate = dict(values, releaseRevision=revision)
        old_uids = {p['metadata']['uid'] for p in resource(env, 'pods', '-n', 'demo', '-l', selector)['items']}
        try:
            install(env, 'app', 'demo', ROOT / 'deploy/app', overrides=candidate, timeout=120)
            wait_stable_revision(env, revision, old_uids, report)
            for _ in range(20):
                http(env, revision)
            new_uids = {p['metadata']['uid'] for p in resource(env, 'pods', '-n', 'demo', '-l', selector)['items']
                        if not p['metadata'].get('deletionTimestamp')}
            if old_uids & new_uids:
                raise ValueError('Config release did not replace all stable pods')
        finally:
            command([tool('helm'), 'rollback', 'app', str(previous['revision']), '-n', 'demo',
                     '--kubeconfig', env['kubeconfig'], '--wait', '--timeout', '120s'], capture=True, timeout=150)
        wait_stable_revision(env, original, new_uids, report)
        for _ in range(20):
            http(env, original)
        if helm_values(env, 'app') != values:
            raise ValueError('Stable Helm rollback did not restore effective values')
        report['checks'].append({'name': 'config_rollout_and_rollback', 'status': 'pass',
                                 'before_revision': previous['revision'], 'demo_revision': revision,
                                 'restored_response_revision': original})


def node_boot_id(env):
    nodes = json.loads(kubectl(env, '--request-timeout=5s', 'get', 'nodes', '-o', 'json',
                              capture=True, timeout=7).stdout)['items']
    if len(nodes) != 1 or nodes[0]['metadata']['name'] != env['node_name']:
        raise ValueError('Unexpected node while observing reboot')
    return nodes[0]['status']['nodeInfo']['bootID']


def require_external_controller(env):
    before = node_boot_id(env)
    local = Path('/proc/sys/kernel/random/boot_id')
    if local.exists() and local.read_text().strip() == before:
        raise ValueError('Run reboot demo from an external controller; the target VM cannot observe its own reboot')
    return before


def reboot(env, report):
    with deployment_lock(env['_name']):
        assert_target(env)
        if env['ssh_host'] not in (env['node_ip'], env['node_ipv4']):
            raise ValueError('Reboot SSH host must be the explicitly configured Kubernetes node')
        before = require_external_controller(env)
        argv = ['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10', '-p', str(env['ssh_port'])]
        if env.get('ssh_private_key_file'):
            argv += ['-i', env['ssh_private_key_file']]
        argv += shlex.split(env.get('ssh_common_args', ''))
        argv += [env['ssh_user'] + '@' + env['ssh_host'], 'sudo -n systemctl reboot']
        # SSH can close with 255 once systemd has started the reboot; changed
        # bootID and verified API readiness below are the success criteria.
        result = subprocess.run(argv, capture_output=True, text=True, timeout=20)
        if result.returncode not in (0, 255):
            raise RuntimeError('Explicit-node reboot command failed')
        started = time.monotonic()
        deadline = started + 300
        while time.monotonic() < deadline:
            try:
                kubectl(env, '--request-timeout=5s', 'get', '--raw=/readyz', timeout=7, capture=True)
                after = node_boot_id(env)
                if after != before:
                    break
            except Exception:
                pass
            time.sleep(2)
        else:
            raise TimeoutError('No new bootID with healthy API after controlled reboot')
        report['checks'].append({'name': 'controlled_reboot', 'status': 'pass', 'boot_id_changed': True,
                                 'api_ready_seconds': round(time.monotonic() - started, 2)})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--env', default='ubuntu')
    parser.add_argument('--reboot', action='store_true', help='Also reboot only the explicitly configured test VM')
    parser.add_argument('--section', choices=('all', 'app', 'reboot', 'recovery'), default='all',
                        help='Run an individual scenario; report scope remains explicit')
    args = parser.parse_args()
    def interrupted(_signal, _frame):
        raise KeyboardInterrupt()
    previous_handler = signal.signal(signal.SIGTERM, interrupted)
    run_id = 'demo-' + uuid.uuid4().hex[:12]
    directory = ROOT / 'artifacts/demo' / run_id
    directory.mkdir(parents=True)
    report = {'run_id': run_id, **code_identity(),
              'scope': args.section, 'status': 'fail', 'checks': [], 'started_at': dt.datetime.now(dt.timezone.utc).isoformat()}

    def stage(name, argv, timeout=2400):
        with (directory / (name + '.log')).open('w') as log:
            process = subprocess.Popen([str(arg) for arg in argv], stdout=log, stderr=log,
                                       start_new_session=True)
            try:
                code = process.wait(timeout=timeout)
                if code:
                    raise subprocess.CalledProcessError(code, argv)
            finally:
                # The interruption supervisor owns a separate restore session:
                # allow its 360s wait plus child cleanup before killing it.
                terminate_tree(process, grace=420 if name == 'interrupted_release' else 180)
        report['checks'].append({'name': name, 'status': 'pass'})
        print('PASS', name, flush=True)

    try:
        env = environment(args.env)
        assert_target(env)
        if args.reboot or args.section == 'reboot':
            require_external_controller(env)
        if args.section == 'all':
            stage('initial_verify', [sys.executable, ROOT / 'scripts/verify-release.py', '--env', args.env])
            stage('canary_scenarios', [sys.executable, ROOT / 'tests/canary/verify.py', '--env', args.env], timeout=3600)
            stage('interrupted_release', [sys.executable, ROOT / 'tests/canary/interrupt.py', '--env', args.env], timeout=1800)
        if args.section in ('all', 'app'):
            app_recovery(env, report)
        if args.section in ('all', 'recovery'):
            stage('persistent_recovery', [sys.executable, ROOT / 'tests/e2e/mvp-recovery.py', '--env', args.env])
        if args.reboot or args.section == 'reboot':
            reboot(env, report)
            stage('health_after_reboot', [sys.executable, ROOT / 'scripts/bootstrap.py', '--env', args.env, '--health-only'])
        stage('final_verify', [sys.executable, ROOT / 'scripts/verify-release.py', '--env', args.env])
        report['status'] = 'pass'
    except (Exception, KeyboardInterrupt) as error:
        report['checks'].append({'name': 'demo', 'status': 'fail', 'error': str(error)})
    finally:
        signal.signal(signal.SIGTERM, previous_handler)
    report['finished_at'] = dt.datetime.now(dt.timezone.utc).isoformat()
    write_json(directory / 'report.json', report)
    print(report['status'].upper(), directory / 'report.json')
    return 0 if report['status'] == 'pass' else 1


if __name__ == '__main__':
    sys.exit(main())
