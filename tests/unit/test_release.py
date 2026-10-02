"""Release safety tests: certificate continuity and observed route identity."""
import base64
import copy
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts'))
import release_control as release


class Certificates(unittest.TestCase):
    def test_repeat_and_fresh_checkout_preserve_cluster_certificate(self):
        env = {'_name': 'fixture', 'tls_hostname': 'hello.test'}
        with tempfile.TemporaryDirectory() as first, tempfile.TemporaryDirectory() as fresh:
            with patch.object(release, 'ROOT', Path(first)), patch.object(release, 'kubectl', return_value=SimpleNamespace(stdout='')):
                values = release.ensure_tls(env)
                self.assertEqual(values, release.ensure_tls(env))
                key = Path(first) / '.state/tls/fixture/tls.key'
                self.assertEqual(key.stat().st_mode & 0o777, 0o600)
                with self.assertRaises(ValueError):
                    release.ensure_tls(dict(env, tls_hostname='other.test'))
            data = {'data': {field: base64.b64encode(values[value].encode()).decode()
                             for field, value in [('tls.crt', 'certificate'), ('tls.key', 'privateKey'), ('ca.crt', 'caCertificate')]}}
            with patch.object(release, 'ROOT', Path(fresh)), patch.object(release, 'kubectl', return_value=SimpleNamespace(stdout=json.dumps(data))):
                self.assertEqual(values, release.ensure_tls(env))
                self.assertFalse((Path(fresh) / '.state/tls/fixture/ca.key').exists())

    def test_partial_tls_state_is_not_silently_replaced(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / '.state/tls/fixture'
            path.mkdir(parents=True)
            (path / 'tls.key').write_text('partial')
            with patch.object(release, 'ROOT', Path(directory)), patch.object(release, 'kubectl', return_value=SimpleNamespace(stdout='')):
                with self.assertRaisesRegex(ValueError, 'Incomplete'):
                    release.ensure_tls({'_name': 'fixture', 'tls_hostname': 'hello.test'})
            self.assertEqual((path / 'tls.key').read_text(), 'partial')


class Routes(unittest.TestCase):
    def setUp(self):
        self.route = {'spec': {'rules': [{'backendRefs': [
            {'name': 'hello', 'port': 80, 'weight': 90},
            {'name': 'hello-canary', 'port': 80, 'weight': 10}]}]}}

    def test_backend_identity_and_weights_fail_closed(self):
        self.assertEqual(release.route_weights(self.route), {'stable': 90, 'canary': 10})
        for field, bad in [('name', 'other'), ('namespace', 'other'), ('kind', 'Deployment'),
                           ('group', 'other'), ('port', 443), ('weight', -1), ('weight', True)]:
            with self.subTest(field=field):
                value = copy.deepcopy(self.route)
                value['spec']['rules'][0]['backendRefs'][1][field] = bad
                with self.assertRaises(ValueError):
                    release.route_weights(value)

    def test_hostname_and_ip_routes_must_agree(self):
        other = copy.deepcopy(self.route)
        other['spec']['rules'][0]['backendRefs'][0]['weight'] = 100
        other['spec']['rules'][0]['backendRefs'][1]['weight'] = 0
        with patch.object(release, '_observed', side_effect=[self.route, other]):
            with self.assertRaisesRegex(ValueError, 'differ'):
                release.current_weights({})

    def test_duplicate_and_stale_conditions_are_rejected(self):
        condition = {'type': 'Accepted', 'status': 'True', 'observedGeneration': 2}
        release._conditions([condition], 2, ('Accepted',))
        for conditions, generation in [([condition], 3), ([condition, condition], 2), ([], 2)]:
            with self.assertRaises(ValueError):
                release._conditions(conditions, generation, ('Accepted',))

    def test_interrupted_gateway_uses_completed_stable_revision(self):
        replies = [SimpleNamespace(stdout=json.dumps([{'name': 'gateway', 'status': 'pending-upgrade'}])),
                   SimpleNamespace(stdout=json.dumps([{'revision': 1, 'status': 'superseded'},
                                                      {'revision': 2, 'status': 'pending-upgrade'}])),
                   SimpleNamespace(stdout=json.dumps({'traffic': {'stableWeight': 100, 'canaryWeight': 0}})),
                   SimpleNamespace(stdout='')]
        with patch.object(release, 'command', side_effect=replies) as command:
            release.recover_pending({'kubeconfig': '/explicit/fixture'}, 'gateway')
        argv = command.call_args[0][0]
        self.assertEqual(argv[1:4], ['rollback', 'gateway', '1'])

    def test_interrupted_gateway_never_restores_unaccepted_canary_weights(self):
        replies = [SimpleNamespace(stdout=json.dumps([{'name': 'gateway', 'status': 'pending-upgrade'}])),
                   SimpleNamespace(stdout=json.dumps([{'revision': 1, 'status': 'superseded'},
                                                      {'revision': 2, 'status': 'pending-upgrade'}])),
                   SimpleNamespace(stdout=json.dumps({'traffic': {'stableWeight': 90, 'canaryWeight': 10}}))]
        with patch.object(release, 'command', side_effect=replies) as command:
            with self.assertRaisesRegex(ValueError, 'no completed safe revision'):
                release.recover_pending({'kubeconfig': '/explicit/fixture'}, 'gateway')
        self.assertFalse(any('rollback' in call.args[0] for call in command.call_args_list))


if __name__ == '__main__':
    unittest.main()
