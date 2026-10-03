#!/usr/bin/env python3
"""Create a one-host inventory from explicit local environment and run Ansible."""
import argparse
import os
from pathlib import Path
import yaml
from stacklib import ROOT, command, deployment_lock, environment

def bootstrap(env, *, health_only=False):
    """Prepare the target and fetch its kubeconfig; caller holds deployment_lock."""
    state = ROOT / '.state' / env['_name']
    state.mkdir(parents=True, exist_ok=True, mode=0o700)
    host = {'ansible_host': env['ssh_host'], 'ansible_user': env['ssh_user'],
            'ansible_port': env['ssh_port'], 'ansible_python_interpreter': '/usr/bin/python3'}
    if env.get('ssh_private_key_file'):
        host['ansible_ssh_private_key_file'] = env['ssh_private_key_file']
    if env.get('ssh_common_args'):
        host['ansible_ssh_common_args'] = env['ssh_common_args']
    inventory = state / 'inventory.yaml'
    inventory.write_text(yaml.safe_dump({'all': {'children': {'mvp': {'hosts': {env['node_name']: host}}}}}))
    inventory.chmod(0o600)
    effective = state / 'environment.yaml'
    effective.write_text(yaml.safe_dump({k: v for k, v in env.items() if not k.startswith('_')}))
    effective.chmod(0o600)
    process_env = os.environ.copy()
    for key, name in [('ANSIBLE_LOCAL_TEMP', 'tmp'), ('ANSIBLE_SSH_CONTROL_PATH_DIR', 'cp')]:
        directory = ROOT / '.state/ansible' / name
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        process_env[key] = str(directory)
    ansible = ROOT / '.venv/bin/ansible-playbook'
    command([ansible, '-i', inventory, ROOT / ('ansible/health.yaml' if health_only else 'ansible/bootstrap.yaml'),
             '-e', '@' + str(effective)], timeout=2400, cwd=ROOT, env=process_env)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--env', default='ubuntu')
    parser.add_argument('--health-only', action='store_true')
    args = parser.parse_args(argv)
    env = environment(args.env)
    with deployment_lock(args.env):
        bootstrap(env, health_only=args.health_only)


if __name__ == '__main__':
    main()
