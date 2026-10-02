"""Stable rollout convergence fixtures; no Kubernetes or VM operations."""
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch


spec = importlib.util.spec_from_file_location(
    'release_demo_convergence', Path(__file__).resolve().parents[1] / 'e2e/release-demo.py')
demo = importlib.util.module_from_spec(spec)
spec.loader.exec_module(demo)


class Clock:
    def __init__(self):
        self.elapsed = 0.0

    def monotonic(self):
        return self.elapsed

    def sleep(self, seconds):
        self.elapsed += seconds


def deployment(**status):
    return {'metadata': {'generation': 4}, 'status': {
        'observedGeneration': 4, 'replicas': 2, 'updatedReplicas': 2,
        'readyReplicas': 2, 'availableReplicas': 2, **status}}


def pod(uid, terminating=False):
    metadata = {'uid': uid}
    if terminating:
        metadata['deletionTimestamp'] = '2026-10-02T00:00:00Z'
    return {'metadata': metadata}


class StableRevisionConvergence(unittest.TestCase):
    def setUp(self):
        self.env = {'fixture': True}
        self.report = {'checks': []}
        self.clock = Clock()
        self.index = -1

    def wait(self, snapshots, http=None, timeout=3):
        def kubectl(env, *args, **kwargs):
            self.assertIs(env, self.env)
            self.assertIn('--request-timeout=5s', args)
            self.assertEqual(kwargs['timeout'], 7)
            if 'deployment' in args:
                self.index += 1
                value = snapshots[min(self.index, len(snapshots) - 1)][0]
            else:
                self.assertIn('pods', args)
                self.assertIn('app.kubernetes.io/name=hello,app.kubernetes.io/instance=app', args)
                value = {'items': snapshots[min(self.index, len(snapshots) - 1)][1]}
            return SimpleNamespace(stdout=json.dumps(value))

        self.http = http if http is not None else Mock()
        with patch.object(demo, 'kubectl', side_effect=kubectl), \
             patch.object(demo, 'http', self.http), \
             patch.object(demo.time, 'monotonic', self.clock.monotonic), \
             patch.object(demo.time, 'sleep', self.clock.sleep):
            demo.wait_stable_revision(self.env, 'stable-new', {'old-1', 'old-2'}, self.report, timeout=timeout)

    def test_old_terminating_uid_blocks_http_until_it_disappears(self):
        snapshots = [
            (deployment(), [pod('new-1'), pod('new-2'), pod('old-1', terminating=True)]),
            (deployment(), [pod('new-1'), pod('new-2')]),
        ]
        def http(env, revision):
            self.assertEqual(self.index, 1, 'HTTP must not run while an old terminating UID remains')
            self.assertIs(env, self.env)
            self.assertEqual(revision, 'stable-new')
        checked_http = Mock(side_effect=http)
        self.wait(snapshots, checked_http)
        checked_http.assert_called_once_with(self.env, 'stable-new')
        check = self.report['checks'][0]
        self.assertEqual(check['status'], 'pass')
        self.assertEqual(check['observations'][0]['previous_pods_remaining'], 1)
        self.assertFalse(check['observations'][0]['deployment_ready'])
        self.assertTrue(check['observations'][1]['response_matches'])

    def test_old_uid_blocks_http_even_when_replica_counts_look_ready(self):
        snapshots = [
            (deployment(), [pod('old-1'), pod('new-2')]),
            (deployment(), [pod('new-1'), pod('new-2')]),
        ]
        checked_http = Mock(side_effect=lambda *_: self.assertEqual(self.index, 1))
        self.wait(snapshots, checked_http)
        checked_http.assert_called_once()
        first = self.report['checks'][0]['observations'][0]
        self.assertTrue(first['deployment_ready'])
        self.assertEqual(first['previous_pods_remaining'], 1)

    def test_stale_observed_generation_blocks_http(self):
        snapshots = [
            (deployment(observedGeneration=3), [pod('new-1'), pod('new-2')]),
            (deployment(), [pod('new-1'), pod('new-2')]),
        ]
        checked_http = Mock(side_effect=lambda *_: self.assertEqual(self.index, 1))
        self.wait(snapshots, checked_http)
        checked_http.assert_called_once_with(self.env, 'stable-new')
        self.assertFalse(self.report['checks'][0]['observations'][0]['deployment_ready'])

    def test_each_replica_count_must_converge_before_http(self):
        for key in ('replicas', 'updatedReplicas', 'readyReplicas', 'availableReplicas'):
            with self.subTest(key=key):
                self.index = -1
                snapshots = [
                    (deployment(**{key: 1}), [pod('new-1'), pod('new-2')]),
                    (deployment(), [pod('new-1'), pod('new-2')]),
                ]
                checked_http = Mock(side_effect=lambda *_: self.assertEqual(self.index, 1))
                self.wait(snapshots, checked_http)
                checked_http.assert_called_once()

    def test_stale_rollout_timeout_is_not_hidden_by_a_healthy_http_stub(self):
        snapshots = [(deployment(observedGeneration=3), [pod('new-1'), pod('new-2')])]
        with self.assertRaisesRegex(TimeoutError, 'did not converge'):
            self.wait(snapshots, timeout=1)
        self.http.assert_not_called()
        self.assertEqual(self.clock.elapsed, 1)
        self.assertEqual(self.report['checks'][0]['status'], 'fail')
        self.assertEqual(len(self.report['checks'][0]['observations']), 2)

    def test_http_mismatch_timeout_remains_failure_after_pod_convergence(self):
        snapshots = [(deployment(), [pod('new-1'), pod('new-2')])]
        checked_http = Mock(side_effect=ValueError('old revision still served'))
        with self.assertRaisesRegex(TimeoutError, 'did not converge'):
            self.wait(snapshots, checked_http, timeout=1)
        self.assertEqual(checked_http.call_count, 2)
        check = self.report['checks'][0]
        self.assertEqual(check['status'], 'fail')
        self.assertTrue(all(o['error'] == 'ValueError' for o in check['observations']))
        self.assertFalse(any(o.get('response_matches') for o in check['observations']))


if __name__ == '__main__':
    unittest.main()
