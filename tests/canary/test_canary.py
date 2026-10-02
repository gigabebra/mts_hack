"""Deterministic fault fixtures; these are not live acceptance claims."""
import copy
import contextlib
import hashlib
import importlib.util
from pathlib import Path
import tempfile
import sys
import types
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import canary
import canary_metrics as metrics

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
        return {"revision": 1, "status": "deployed"}
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
        self.assertRegex(result["revision"], r"^[0-9a-f]{40,64}$")
        self.assertIsInstance(result["working_tree_dirty"], bool)

    def test_code_identity_is_independent_of_artifact_directory(self):
        with patch.object(canary, "ROOT", Path(self.temp.name)):
            identity = canary.code_identity()
        self.assertRegex(identity["revision"], r"^[0-9a-f]{40,64}$")
        self.assertIsInstance(identity["working_tree_dirty"], bool)

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
