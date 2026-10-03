"""Deterministic fault fixtures; these are not live acceptance claims."""
import copy
import contextlib
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import sys
import types
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import canary
import canary_metrics as metrics

REAL_RUNTIME = canary.Runtime

_harness_spec = importlib.util.spec_from_file_location("canary_harness", Path(__file__).with_name("verify.py"))
harness = importlib.util.module_from_spec(_harness_spec)
_harness_spec.loader.exec_module(harness)


LABELS = {"namespace": "edge", "service": "traefik-metrics", "endpoint": "metrics", "job": "traefik-metrics",
          "instance": "[2001:db8::1]:9100", "pod": "traefik-fixture"}


def row(name, points, **labels):
    metric = dict(LABELS, __name__=name, **labels)
    return tuple(sorted(metric.items())), {"metric": metric, "samples": points,
                                          "timestamp": points[-1][0], "value": points[-1][1]}


def snapshot(stamp=1000, count=10, before=None, errors=0):
    points = [[stamp, count]] if before is None else [[1000, 10], [stamp, count]]
    counters = dict([row("traefik_service_requests_total", points, exported_service=metrics.BACKEND, method="GET", code="200")])
    if errors:
        counters.update([row("traefik_service_requests_total", [[stamp, errors]], exported_service=metrics.BACKEND, method="GET", code="500")])
    return {"captured_at": stamp + .01, "query": metrics.selector(), "series": counters,
            "process": dict([row("process_start_time_seconds", [[1000, 50], [stamp, 50]] if before else [[stamp, 50]])]),
            "up": dict([row("up", [[1000, 1], [stamp, 1]] if before else [[stamp, 1]])])}


class Clock:
    value = 0
    def monotonic(self):
        return self.value
    def time(self):
        return 1000 + self.value
    def sleep(self, duration):
        self.value += duration


class FakeOps:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.settings = canary.validate_settings({})
        self.current = {"stable": 90, "canary": 10}  # interrupted predecessor
        self.app = {"image": "locked-fixture", "releaseTrack": "stable", "releaseRevision": "stable-v1",
                    "responseBody": "Hello World!", "faultMode": "none", "fullnameOverride": "hello", "replicas": 2}
        self.env = {}
        self.stable_deployment = {
            "metadata": {"generation": 3},
            "spec": {"replicas": 2, "template": {"spec": {"containers": [{"image": self.app["image"]}]}}},
            "status": {"observedGeneration": 3, "replicas": 2, "updatedReplicas": 2,
                       "readyReplicas": 2, "availableReplicas": 2}}
        self.stable_helm_status = "deployed"
        self.candidate = self.defaults()
        self.calls, self.requests, self.snapshots = [], 0, 0
        self.metrics_down, self.rollback_failure, self.identity_wrong = False, False, False
        self.started_metrics, self.stopped_metrics = False, False

    def assert_target(self):
        self.calls.append("assert_target")
    def values(self, release):
        return copy.deepcopy(self.app if release == "app" else self.candidate if release == "app-canary" else
                             {"traffic": {"stableWeight": self.current["stable"], "canaryWeight": self.current["canary"]}})
    def info(self, release):
        return {"revision": 1, "status": self.stable_helm_status if release == "app" else "deployed"}
    def defaults(self):
        return {"image": "locked-fixture", "releaseTrack": "canary", "releaseRevision": "accepted-base",
                "responseBody": "Hello World!", "faultMode": "none", "fullnameOverride": "hello-canary", "replicas": 1}
    def weights(self):
        return dict(self.current)
    def set_weights(self, stable, candidate):
        self.calls.append(("weights", stable, candidate))
        if self.rollback_failure and stable == 100 and ("weights", 90, 10) in self.calls:
            raise RuntimeError("fixture rollback command failed")
        self.current = {"stable": stable, "canary": candidate}
        return dict(self.current)
    def install(self, values):
        self.calls.append(("install", values["releaseRevision"], values["faultMode"]))
        self.candidate = copy.deepcopy(values)
    def ready(self, values):
        return {"ready_replicas": 1, "revision": values["releaseRevision"]}
    def stable_ready(self, values):
        self.calls.append(("stable_ready",))
        # Exercise the production stable gate with a Kubernetes API fixture.
        with patch.object(canary, "resource", return_value=self.stable_deployment) as resource, \
             patch.object(canary, "locked_image", side_effect=lambda image: image):
            result = REAL_RUNTIME.stable_ready(self, values)
        resource.assert_called_once_with(self.env, "deployment", "hello", "-n", "demo")
        return result
    def request(self, path, request_id):
        self.requests += 1
        role = "canary" if path == "/backend/canary" or path == "/" and self.current["canary"] and self.requests % 10 == 0 else "stable"
        value = self.candidate if role == "canary" else self.app
        body = b"Wrong body!" if value["faultMode"] == "wrong-body" else value["responseBody"].encode()
        return {"status": 500 if value["faultMode"] == "http500" else 200, "body_sha256": hashlib.sha256(body).hexdigest(),
                "body_length": len(body), "release": role, "revision": "wrong-v2" if self.identity_wrong and role == "canary" else value["releaseRevision"],
                "request_id_matches": True}
    def start_metrics(self, deadline):
        self.started_metrics = True
    def stop_metrics(self):
        self.stopped_metrics = True
    def snapshot(self, after, deadline):
        if self.metrics_down:
            raise metrics.Inconclusive("Prometheus query failed (ConnectionRefusedError)")
        self.snapshots += 1
        if self.snapshots == 1:
            return snapshot()
        return snapshot(1061, 250, before=True)


class EngineFixtures(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.ops = FakeOps(self.temp.name)

    def run_release(self, **kwargs):
        clock = Clock()
        with patch.object(canary.time, "monotonic", clock.monotonic), patch.object(canary.time, "time", clock.time), patch.object(canary.time, "sleep", clock.sleep):
            result = canary.run_locked(self.ops, run_id="fixture", revision="candidate-v2", **kwargs)
        self.assertTrue((self.ops.directory / "report.json").is_file())
        return result

    def test_healthy_uses_actual_state_and_accepts(self):
        result = self.run_release()
        self.assertEqual(result["decision"], "accepted")
        self.assertEqual(result["weights_before"], {"stable": 90, "canary": 10})
        self.assertEqual(self.ops.calls[1], ("weights", 100, 0))
        self.assertEqual(self.ops.weights(), {"stable": 90, "canary": 10})
        self.assertGreaterEqual(result["http_observation"]["releases"]["canary"], 100)
        self.assertTrue(self.ops.stopped_metrics)
        for key, value in canary.code_identity().items():
            self.assertEqual(result[key], value)
        for key in ("stable_ready", "stable_ready_before_split", "stable_ready_after"):
            self.assertEqual(result[key]["ready_replicas"], 2)
            self.assertEqual(result[key]["helm"]["status"], "deployed")
        self.assertEqual(self.ops.calls.count(("stable_ready",)), 3)

    def test_incomplete_stable_helm_release_rejected_before_candidate_install(self):
        for status in ("pending-install", "pending-upgrade", "pending-rollback", "failed", "unknown", None):
            with self.subTest(status=status):
                self.ops = FakeOps(self.temp.name)
                self.ops.stable_helm_status = status
                result = self.run_release()
                self.assertEqual(result["decision"], "rejected")
                self.assertEqual(result["status"], "fail")
                self.assertIn("stable Helm release is not deployed", result["reason"])
                self.assertNotIn("candidate_requested", result)
                self.assertNotIn(("weights", 90, 10), self.ops.calls)
                self.assertFalse(self.ops.started_metrics)
                self.assertEqual(result["rollback"]["status"], "pass")
                self.assertEqual(self.ops.weights(), {"stable": 100, "canary": 0})

    def test_degraded_stable_rejected_despite_successful_http(self):
        self.ops.stable_deployment["status"].update(readyReplicas=1, availableReplicas=1)
        result = self.run_release()
        self.assertEqual(result["stable_smoke"]["passed"], 5)
        self.assertEqual(result["decision"], "rejected")
        self.assertIn("stable rollout is not fully ready", result["reason"])
        self.assertNotIn("candidate_requested", result)
        self.assertNotIn(("weights", 90, 10), self.ops.calls)
        self.assertEqual(result["rollback"]["status"], "pass")

    def test_stable_degrades_during_candidate_preparation_before_split(self):
        snapshot = self.ops.snapshot
        def degrade(after, deadline):
            self.ops.stable_deployment["status"]["availableReplicas"] = 1
            return snapshot(after, deadline)
        self.ops.snapshot = degrade
        result = self.run_release()
        self.assertIn("candidate_smoke", result)
        self.assertIn("stable_ready", result)
        self.assertEqual(result["decision"], "rejected")
        self.assertNotIn(("weights", 90, 10), self.ops.calls)
        self.assertEqual(result["rollback"]["status"], "pass")

    def test_stable_loses_readiness_or_completed_helm_status_after_split(self):
        for fault in ("replicas", "helm"):
            with self.subTest(fault=fault):
                self.ops = FakeOps(self.temp.name)
                def degrade(ops, *_):
                    if fault == "replicas":
                        ops.stable_deployment["status"]["readyReplicas"] = 1
                    else:
                        ops.stable_helm_status = "pending-upgrade"
                result = self.run_release(after_split=degrade)
                self.assertEqual(result["http_observation"]["status"], "pass")
                self.assertEqual(result["metrics_decision"]["status"], "pass")
                self.assertEqual(result["decision"], "rejected")
                self.assertEqual(result["status"], "fail")
                self.assertEqual(result["rollback"]["status"], "pass")
                self.assertEqual(self.ops.weights(), {"stable": 100, "canary": 0})

    def test_restore_remains_available_with_degraded_pending_stable(self):
        self.ops.stable_helm_status = "pending-upgrade"
        self.ops.stable_deployment["status"]["readyReplicas"] = 1
        result = self.run_release(action="restore")
        self.assertEqual(result["decision"], "restored")
        self.assertEqual(self.ops.weights(), {"stable": 100, "canary": 0})
        self.assertNotIn(("stable_ready",), self.ops.calls)

    def test_code_identity_is_independent_of_artifact_directory(self):
        expected = canary.code_identity()
        with patch.object(canary, "ROOT", Path(self.temp.name)):
            identity = canary.code_identity()
        self.assertEqual(identity, expected)

    def test_git_metadata_is_optional_for_release_restore_and_rejection(self):
        for kwargs, decision in [({}, "accepted"), ({"action": "restore"}, "restored"),
                                 ({"fault_mode": "wrong-body"}, "rejected")]:
            with self.subTest(decision=decision):
                self.ops = FakeOps(self.temp.name)
                with patch.object(canary, "code_identity", return_value={"revision": None, "working_tree_dirty": None}):
                    result = self.run_release(**kwargs)
                self.assertEqual(result["decision"], decision)
                self.assertEqual(result["status"], "fail" if decision == "rejected" else "pass")
                self.assertIsNone(result["revision"])
                self.assertIsNone(result["working_tree_dirty"])
                self.assertEqual(self.ops.weights(), {"stable": 90, "canary": 10} if decision == "accepted"
                                 else {"stable": 100, "canary": 0})

    def test_bad_body_is_rejected_before_traffic(self):
        result = self.run_release(fault_mode="wrong-body")
        self.assertEqual(result["decision"], "rejected")
        self.assertNotIn(("weights", 90, 10), self.ops.calls)
        self.assertEqual(result["rollback"]["status"], "pass")
        self.assertEqual(result["status"], "fail")

    def test_wrong_revision_does_not_pass_smoke(self):
        self.ops.identity_wrong = True
        result = self.run_release()
        self.assertNotIn(("weights", 90, 10), self.ops.calls)
        self.assertNotEqual(result["status"], "pass")

    def test_http_fault_after_split_rejects_and_restores_config(self):
        for mode in ("http500", "wrong-body"):
            with self.subTest(mode=mode):
                self.ops = FakeOps(self.temp.name)
                def fault(ops, candidate, report):
                    self.assertEqual(ops.weights(), {"stable": 90, "canary": 10})
                    candidate["faultMode"] = mode
                    ops.install(candidate)
                result = self.run_release(after_split=fault)
                self.assertEqual(result["decision"], "rejected")
                self.assertEqual(result["status"], "fail")
                self.assertEqual(result["rollback"]["status"], "pass")
                self.assertEqual(result["rollback"]["stable_http"]["passed"], 25)
                self.assertEqual(self.ops.candidate["faultMode"], "none")
                self.assertEqual(self.ops.candidate["releaseRevision"], "accepted-base")
                self.assertEqual(self.ops.weights(), {"stable": 100, "canary": 0})

    def test_missing_metrics_is_inconclusive_and_nonzero_even_after_recovery(self):
        def fault(ops, candidate, report):
            ops.metrics_down = True
        result = self.run_release(after_split=fault)
        self.assertEqual(result["decision"], "inconclusive")
        self.assertEqual(result["status"], "fail")
        self.assertEqual(result["rollback"]["status"], "pass")

    def test_insufficient_exact_revision_http_sample_is_inconclusive(self):
        result = self.run_release(after_split=lambda *_: 20)
        self.assertEqual(result["decision"], "inconclusive")
        self.assertEqual(result["rollback"]["status"], "pass")

    def test_rollback_failure_is_distinct(self):
        def fault(ops, candidate, report):
            ops.metrics_down, ops.rollback_failure = True, True
        result = self.run_release(after_split=fault)
        self.assertEqual(result["decision"], "rollback_failed")
        self.assertEqual(result["original_decision"], "inconclusive")
        self.assertIn("recovery_command", result["rollback"])

    def test_interrupt_after_split_rolls_back(self):
        def interrupt(*_):
            raise KeyboardInterrupt()
        result = self.run_release(after_split=interrupt)
        self.assertEqual(result["decision"], "interrupted")
        self.assertEqual(result["rollback"]["status"], "pass")
        self.assertEqual(result["status"], "fail")

    def test_restore_action_ends_stable_only(self):
        result = self.run_release(action="restore")
        self.assertEqual(result["decision"], "restored")
        self.assertEqual(self.ops.weights(), {"stable": 100, "canary": 0})
        self.assertFalse(self.ops.started_metrics)

    def test_restore_repairs_inconsistent_interrupted_route_weights(self):
        actual_weights = self.ops.weights
        calls = 0
        def initial_mismatch():
            nonlocal calls
            calls += 1
            if calls == 1:
                raise ValueError("IP and hostname route weights differ")
            return actual_weights()
        self.ops.weights = initial_mismatch
        result = self.run_release(action="restore")
        self.assertEqual(result["weights_before"]["status"], "unavailable")
        self.assertEqual(result["decision"], "restored")
        self.assertEqual(result["rollback"]["weights_after"], {"stable": 100, "canary": 0})

    def test_restore_does_not_depend_on_missing_candidate_history(self):
        original_values, original_info = self.ops.values, self.ops.info
        def values(release):
            if release == "app-canary":
                raise RuntimeError("candidate not yet installed")
            return original_values(release)
        def info(release):
            if release == "app-canary" and not any(call[0] == "install" for call in self.ops.calls):
                raise RuntimeError("candidate not yet installed")
            return original_info(release)
        self.ops.values, self.ops.info = values, info
        result = self.run_release(action="restore")
        self.assertEqual(result["previous"]["app-canary"]["values_status"], "unavailable")
        self.assertEqual(result["previous"]["app-canary"]["helm_status"], "unavailable")
        self.assertEqual(result["decision"], "restored")
        self.assertEqual(result["rollback"]["weights_after"], {"stable": 100, "canary": 0})

    def test_secret_fields_not_saved_in_values(self):
        public = canary.public_values(dict(self.ops.candidate, secret="private", responseBody="private body"))
        self.assertNotIn("secret", public)
        self.assertNotIn("responseBody", public)
        self.assertNotIn("private", str(public))

    def test_cli_returns_nonzero_after_successful_rollback(self):
        with patch.object(sys, "argv", ["canary.py", "--env", "fixture", "--run-id", "fixture-cli", "--fault-mode", "wrong-body"]), \
             patch.object(canary, "ROOT", Path(self.temp.name)), \
             patch.object(canary, "environment", return_value={}), \
             patch.object(canary.importlib, "import_module", return_value=types.SimpleNamespace(settings=lambda _: {})), \
             patch.object(canary, "Runtime", return_value=self.ops), \
             patch.object(canary, "deployment_lock", return_value=contextlib.nullcontext()):
            self.assertEqual(canary.main(), 2)
        self.assertEqual(self.ops.weights(), {"stable": 100, "canary": 0})
        result = json.loads((self.ops.directory / "report.json").read_text())
        self.assertIn("dedicated canary smoke failed", result["reason"])

    def test_cli_returns_nonzero_when_stable_is_pending(self):
        self.ops.stable_helm_status = "pending-upgrade"
        with patch.object(sys, "argv", ["canary.py", "--env", "fixture", "--run-id", "fixture-pending"]), \
             patch.object(canary, "ROOT", Path(self.temp.name)), \
             patch.object(canary, "environment", return_value={}), \
             patch.object(canary.importlib, "import_module", return_value=types.SimpleNamespace(settings=lambda _: {})), \
             patch.object(canary, "Runtime", return_value=self.ops), \
             patch.object(canary, "deployment_lock", return_value=contextlib.nullcontext()):
            self.assertEqual(canary.main(), 2)
        self.assertNotIn(("weights", 90, 10), self.ops.calls)
        self.assertEqual(self.ops.weights(), {"stable": 100, "canary": 0})
        result = json.loads((self.ops.directory / "report.json").read_text())
        self.assertIn("stable Helm release is not deployed", result["reason"])

    def test_missing_metrics_before_split_also_restores_candidate(self):
        self.ops.metrics_down = True
        result = self.run_release()
        self.assertEqual(result["decision"], "inconclusive")
        self.assertNotIn(("weights", 90, 10), self.ops.calls)
        self.assertEqual(result["rollback"]["status"], "pass")

    def test_signal_timeout_at_release_step_runs_recovery(self):
        import signal
        def expire(*_):
            signal.getsignal(signal.SIGALRM)(signal.SIGALRM, None)
        result = self.run_release(after_split=expire)
        self.assertEqual(result["decision"], "rejected")
        self.assertIn("deadline", result["reason"])
        self.assertEqual(result["rollback"]["status"], "pass")

    def test_acceptance_does_not_mistake_failed_injection_for_observed_fault(self):
        def failed_injection(*_):
            raise RuntimeError("Helm fault injection failed")
        result = self.run_release(after_split=failed_injection)
        self.assertEqual(result["decision"], "rejected")
        self.assertEqual(result["rollback"]["status"], "pass")
        for scenario in ("http500", "wrong-body"):
            self.assertEqual(harness.acceptance(scenario, result)["status"], "fail")

    def test_acceptance_requires_real_fault_from_exact_requested_canary(self):
        result = self.run_release(after_split=harness.injector("http500"))
        self.assertEqual(harness.acceptance("http500", result)["status"], "pass")
        result["http_observation"]["invalid_examples"][0]["revision"] = "unrelated"
        result["http_observation"]["invalid_examples"] = result["http_observation"]["invalid_examples"][:1]
        self.assertEqual(harness.acceptance("http500", result)["status"], "fail")

    def test_acceptance_missing_metrics_cannot_be_unrelated_inconclusive(self):
        result = self.run_release(after_split=lambda *_: 20)
        result["test_fault"] = {"scenario": "metrics-unavailable", "status": "injected"}
        self.assertEqual(harness.acceptance("metrics-unavailable", result)["status"], "fail")


class StableReadinessFixtures(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.ops = FakeOps(self.temp.name)
        self.expected = copy.deepcopy(self.ops.app)

    def test_current_generation_requires_all_updated_ready_available_replicas(self):
        self.assertEqual(self.ops.stable_ready(self.expected)["generation"], 3)
        healthy = copy.deepcopy(self.ops.stable_deployment)
        for field in ("observedGeneration", "replicas", "updatedReplicas", "readyReplicas", "availableReplicas"):
            for value in (None, 0, 1, 4):
                with self.subTest(field=field, value=value):
                    self.ops.stable_deployment = copy.deepcopy(healthy)
                    self.ops.stable_deployment["status"][field] = value
                    with self.assertRaisesRegex(canary.Rejected, "stable rollout is not fully ready"):
                        self.ops.stable_ready(self.expected)

    def test_changed_scale_or_deleting_deployment_is_not_ready(self):
        self.ops.stable_deployment["spec"]["replicas"] = 1
        with self.assertRaisesRegex(canary.Rejected, "not fully ready"):
            self.ops.stable_ready(self.expected)
        self.ops.stable_deployment["spec"]["replicas"] = 2
        self.ops.stable_deployment["metadata"]["deletionTimestamp"] = "2026-10-03T00:00:00Z"
        with self.assertRaisesRegex(canary.Rejected, "not fully ready"):
            self.ops.stable_ready(self.expected)

    def test_wrong_image_and_changed_release_cannot_count_as_ready_stable(self):
        self.ops.stable_deployment["spec"]["template"]["spec"]["containers"][0]["image"] = "other-image"
        with self.assertRaisesRegex(canary.Rejected, "expected locked image"):
            self.ops.stable_ready(self.expected)
        self.ops.stable_deployment["spec"]["template"]["spec"]["containers"][0]["image"] = self.expected["image"]
        self.ops.app["releaseRevision"] = "different-stable"
        with self.assertRaisesRegex(canary.Rejected, "values changed"):
            self.ops.stable_ready(self.expected)

    def test_missing_or_zero_replicas_cannot_pass_as_a_completed_rollout(self):
        for count in (0, -1, None, True):
            with self.subTest(count=count), self.assertRaisesRegex(canary.Rejected, "positive replica count"):
                self.ops.stable_ready(dict(self.expected, replicas=count))


class MetricsFixtures(unittest.TestCase):
    def test_healthy_raw_counter_delta(self):
        result = metrics.compare(snapshot(), snapshot(1061, 210, before=True))
        self.assertEqual(result["requests"], 200)
        self.assertEqual(result["status"], "pass")

    def test_new_error_series_is_not_ignored(self):
        result = metrics.compare(snapshot(), snapshot(1061, 210, before=True, errors=20))
        self.assertEqual(result["requests"], 220)
        self.assertEqual(result["errors_5xx"], 20)
        self.assertEqual(result["status"], "fail")

    def test_counter_reset_in_middle_of_window_is_inconclusive(self):
        current = snapshot(1061, 210, before=True)
        next(iter(current["series"].values()))["samples"].insert(1, [1030, 1])
        with self.assertRaisesRegex(metrics.Inconclusive, "reset"):
            metrics.compare(snapshot(), current)

    def test_exporter_restart_is_inconclusive_even_if_counter_grew(self):
        current = snapshot(1061, 210, before=True)
        next(iter(current["process"].values()))["samples"][-1][1] = 1040
        with self.assertRaisesRegex(metrics.Inconclusive, "restarted"):
            metrics.compare(snapshot(), current)

    def test_insufficient_data_is_inconclusive(self):
        with self.assertRaisesRegex(metrics.Inconclusive, "insufficient"):
            metrics.compare(snapshot(), snapshot(1061, 109, before=True))

    def test_missing_baseline_anchor_is_inconclusive(self):
        current = snapshot(1061, 210, before=True)
        next(iter(current["series"].values()))["samples"].pop(0)
        with self.assertRaisesRegex(metrics.Inconclusive, "baseline sample"):
            metrics.compare(snapshot(), current)

    def test_other_service_identity_is_inconclusive(self):
        current = snapshot(1061, 210, before=True)
        key, value = next(iter(current["series"].items()))
        del current["series"][key]
        value["metric"]["exported_service"] = "demo-hello-http-80@kubernetesgateway"
        current["series"][tuple(sorted(value["metric"].items()))] = value
        with self.assertRaises(metrics.Inconclusive):
            metrics.compare(snapshot(), current)

    def test_weak_thresholds_rejected(self):
        for settings in ({"min_canary_requests": 99}, {"window_seconds": 30}, {"max_error_rate": .02}):
            with self.subTest(settings=settings), self.assertRaises(ValueError):
                canary.validate_settings(settings)

    def test_configured_test_hostname_is_accepted(self):
        for host in ("hello.test", "canary.demo.test", "release-2.test", "a.test"):
            with self.subTest(host=host):
                self.assertEqual(canary.validate_settings({"host": host})["host"], host)

    def test_unsafe_hostname_is_rejected(self):
        for host in (None, "localhost", "hello.example", "hello.test:30080", "Hello.test", ".test",
                     "bad..test", "-bad.test", "bad-.test", "bad_name.test", "hello.test\r\nX-Test: bad",
                     "x" * 64 + ".test", ".".join(["x" * 63] * 4) + ".test"):
            with self.subTest(host=host), self.assertRaises(ValueError):
                canary.validate_settings({"host": host})

    def test_configurable_hostname_does_not_loosen_backend_identity(self):
        with self.assertRaises(ValueError):
            canary.validate_settings({"host": "canary.demo.test", "candidate_service": "another-service"})


class DataPlaneConvergenceFixtures(unittest.TestCase):
    def setUp(self):
        self.ops = canary.Runtime.__new__(canary.Runtime)
        self.values = {"releaseRevision": "candidate-v2", "responseBody": "Hello World!"}
        self.healthy = {"status": 200, "release": "canary", "revision": "candidate-v2",
                        "request_id_matches": True, "body_length": 12,
                        "body_sha256": hashlib.sha256(b"Hello World!").hexdigest()}

    def converge(self):
        clock = Clock()
        with patch.object(canary.time, "monotonic", clock.monotonic), patch.object(canary.time, "sleep", clock.sleep):
            return self.ops.converge_identity(self.values)

    def test_transient_unavailable_and_old_revision_converge_then_strict_smoke_passes(self):
        samples = [{"status": 0, "release": "unavailable", "error": "TimeoutError"},
                   dict(self.healthy, revision="candidate-v1"), self.healthy]
        self.ops.request = lambda *_, **kw: samples.pop(0) if samples else self.healthy
        result = self.converge()
        self.assertEqual(result["status"], "pass")
        self.assertEqual(result["attempts"], 3)
        self.assertEqual(result["duration_seconds"], 1)
        self.assertEqual(result["observations"][0]["error"], "TimeoutError")
        self.assertEqual(canary.smoke(self.ops, self.values, "canary", "fixture")["passed"], 5)

    def test_faulty_new_revision_converges_immediately_but_fails_strict_smoke(self):
        for response in (dict(self.healthy, status=500),
                         dict(self.healthy, body_length=11, body_sha256=hashlib.sha256(b"Wrong body!").hexdigest())):
            with self.subTest(status=response["status"]):
                self.ops.request = lambda *_, **kw: response
                result = self.converge()
                self.assertEqual(result["attempts"], 1)
                self.assertEqual(result["duration_seconds"], 0)
                with self.assertRaises(canary.Rejected):
                    canary.smoke(self.ops, self.values, "canary", "fixture")

    def test_persistent_old_revision_rejected_after_bounded_timeout(self):
        self.ops.request = lambda *_, **kw: dict(self.healthy, revision="candidate-v1")
        with self.assertRaises(canary.Rejected) as failure:
            self.converge()
        self.assertEqual(failure.exception.evidence["duration_seconds"], 30)
        self.assertEqual(failure.exception.evidence["status"], "fail")
        self.assertEqual(failure.exception.evidence["attempts"], 60)

    def test_operation_deadline_shortens_convergence(self):
        self.ops.operation_deadline = 2
        self.ops.request = lambda *_, **kw: {"status": 0, "release": "unavailable", "error": "TimeoutError"}
        with self.assertRaises(canary.Rejected) as failure:
            self.converge()
        self.assertEqual(failure.exception.evidence["duration_seconds"], 2)
        self.assertEqual(failure.exception.evidence["attempts"], 4)


class SnapshotFixtures(unittest.TestCase):
    def setUp(self):
        self.data = snapshot(1000, 10)
        self.targets = [{"labels": LABELS, "health": "up", "lastError": "", "scrapeUrl": "http://[2001:db8::1]:9100/metrics"}]
        self.api = types.SimpleNamespace(get=lambda *_, **kw: {"activeTargets": self.targets}, matrix=self.matrix)

    def matrix(self, selector, window, now):
        key = "process" if selector.startswith("process_") else "up" if selector.startswith("up{") else "series"
        return selector, self.data[key]

    def test_raw_samples_must_follow_completed_smoke(self):
        with self.assertRaisesRegex(metrics.Inconclusive, "boundary"):
            metrics.snapshot(self.api, after=1001, now=1002)
        self.assertTrue(metrics.snapshot(self.api, after=999, now=1002))

    def test_stale_samples_are_not_current_evaluation_time(self):
        with self.assertRaisesRegex(metrics.Inconclusive, "stale"):
            metrics.snapshot(self.api, now=1100)

    def test_missing_series_is_not_zero_errors(self):
        self.data["series"] = {}
        with self.assertRaisesRegex(metrics.Inconclusive, "missing"):
            metrics.snapshot(self.api, now=1002)

    def test_unhealthy_target_is_inconclusive(self):
        self.targets[0]["health"] = "down"
        with self.assertRaisesRegex(metrics.Inconclusive, "unhealthy"):
            metrics.snapshot(self.api, now=1002)

    def test_duplicate_monitor_rejected_even_under_different_service(self):
        duplicate = copy.deepcopy(self.targets[0])
        duplicate["labels"]["service"] = "alternate-monitor"
        self.targets.append(duplicate)
        with self.assertRaisesRegex(metrics.Inconclusive, "duplicate"):
            metrics.snapshot(self.api, now=1002)

    def test_wrong_target_in_counter_is_rejected(self):
        next(iter(self.data["series"].values()))["metric"]["pod"] = "other-exporter"
        with self.assertRaisesRegex(metrics.Inconclusive, "different scrape target"):
            metrics.snapshot(self.api, now=1002)


if __name__ == "__main__":
    unittest.main()
