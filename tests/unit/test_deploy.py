"""Fresh-host orchestration and fail-closed bootstrap; no VM fixtures."""
import contextlib
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts'))
import bootstrap
import deploy


class FullDeployment(unittest.TestCase):
    def test_bootstrap_creates_kubeconfig_before_stack_under_one_lock(self):
        with tempfile.TemporaryDirectory() as directory:
            kubeconfig = Path(directory) / 'new.kubeconfig'
            env = {'_name': 'fresh', 'kubeconfig': str(kubeconfig)}
            locked = False
            operations = []

            @contextlib.contextmanager
            def lock(name):
                nonlocal locked
                self.assertEqual(name, 'fresh')
                self.assertFalse(locked, 'bootstrap must not acquire the deployment lock recursively')
                locked = True
                try:
                    yield
                finally:
                    locked = False

            def prepare(target):
                self.assertTrue(locked)
                self.assertIs(target, env)
                self.assertFalse(kubeconfig.exists())
                kubeconfig.write_text('fixture explicit kubeconfig')
                operations.append('bootstrap')

            def install(target):
                self.assertTrue(locked)
                self.assertIs(target, env)
                self.assertTrue(kubeconfig.is_file())
                operations.append('stack')

            with patch.object(deploy, 'environment', return_value=env), \
                 patch.object(deploy, 'deployment_lock', side_effect=lock), \
                 patch.object(deploy, 'bootstrap', side_effect=prepare), \
                 patch.object(deploy, 'deploy', side_effect=install):
                deploy.main(['--env', 'fresh'])
            self.assertEqual(operations, ['bootstrap', 'stack'])
            self.assertFalse(locked)

    def test_failed_bootstrap_never_uses_an_old_kubeconfig_to_deploy(self):
        env = {'_name': 'fresh', 'kubeconfig': '/explicit/previous.kubeconfig'}
        with patch.object(deploy, 'environment', return_value=env), \
             patch.object(deploy, 'deployment_lock', return_value=contextlib.nullcontext()), \
             patch.object(deploy, 'bootstrap', side_effect=RuntimeError('foreign cluster identity')), \
             patch.object(deploy, 'deploy') as install:
            with self.assertRaisesRegex(RuntimeError, 'foreign cluster identity'):
                deploy.main(['--env', 'fresh'])
        install.assert_not_called()

    def test_bootstrap_keeps_explicit_target_and_private_local_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            env = {'_name': 'fresh', 'node_name': 'fixture-node', 'ssh_host': '2001:db8::10',
                   'ssh_user': 'ubuntu', 'ssh_port': 22, 'kubeconfig': str(root / 'admin.kubeconfig'),
                   'ssh_private_key_file': '/explicit/test-key'}
            with patch.object(bootstrap, 'ROOT', root), patch.object(bootstrap, 'command') as command:
                bootstrap.bootstrap(env)
            argv = command.call_args.args[0]
            self.assertIn(root / 'ansible/bootstrap.yaml', argv)
            inventory = root / '.state/fresh/inventory.yaml'
            effective = root / '.state/fresh/environment.yaml'
            host = yaml.safe_load(inventory.read_text())['all']['children']['mvp']['hosts']['fixture-node']
            self.assertEqual(host['ansible_host'], env['ssh_host'])
            self.assertEqual(host['ansible_ssh_private_key_file'], env['ssh_private_key_file'])
            self.assertEqual(yaml.safe_load(effective.read_text())['kubeconfig'], env['kubeconfig'])
            for path in (inventory, effective):
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            child_env = command.call_args.kwargs['env']
            self.assertEqual(child_env['ANSIBLE_LOCAL_TEMP'], str(root / '.state/ansible/tmp'))
            self.assertEqual(child_env['ANSIBLE_SSH_CONTROL_PATH_DIR'], str(root / '.state/ansible/cp'))


if __name__ == '__main__':
    unittest.main()
