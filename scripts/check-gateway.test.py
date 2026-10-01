#!/usr/bin/env python3
"""Offline fixtures for checker behavior; these are not Kubernetes acceptance."""

import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock


SPEC = importlib.util.spec_from_file_location("gateway_check", Path(__file__).with_name("check-gateway.py"))
GATEWAY = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(GATEWAY)


def conditions(*names, generation=2):
    return [{"type": name, "status": "True", "observedGeneration": generation, "reason": name}
            for name in names]


def snapshot():
    return {
        "gatewayclass": {
            "metadata": {"name": "mts-traefik", "generation": 2},
            "spec": {"controllerName": GATEWAY.CONTROLLER},
            "status": {"conditions": conditions("Accepted")}},
        "gateway": {
            "metadata": {"name": "gateway", "namespace": "demo", "generation": 2},
            "spec": {"gatewayClassName": "mts-traefik"},
            "status": {"conditions": conditions("Accepted", "Programmed"),
                       "addresses": [{"type": "IPAddress", "value": "192.0.2.10"}],
                       "listeners": [{"name": "web", "attachedRoutes": 1,
                                      "conditions": conditions("Accepted", "Programmed", "ResolvedRefs")}] }},
        "httproute": {
            "metadata": {"name": "hello", "namespace": "demo", "generation": 2},
            "status": {"parents": [{"controllerName": GATEWAY.CONTROLLER,
                                    "parentRef": {"name": "gateway", "sectionName": "web"},
                                    "conditions": conditions("Accepted", "ResolvedRefs")}]}}}


class ConditionTests(unittest.TestCase):
    def assert_failed(self, resources):
        self.assertIn("fail", [c["status"] for c in GATEWAY.conditions_checks(resources)])

    def test_current_true_snapshot(self):
        self.assertTrue(all(c["status"] == "pass" for c in GATEWAY.conditions_checks(snapshot())))

    def test_all_condition_types_reject_false_unknown_missing_and_stale(self):
        base = snapshot()
        groups = [base["gatewayclass"]["status"]["conditions"], base["gateway"]["status"]["conditions"],
                  base["gateway"]["status"]["listeners"][0]["conditions"],
                  base["httproute"]["status"]["parents"][0]["conditions"]]
        for group in groups:
            for entry in group:
                for field, value in (("status", "False"), ("status", "Unknown"), ("status", None),
                                     ("observedGeneration", 1), ("observedGeneration", None)):
                    original = copy.deepcopy(entry)
                    with self.subTest(kind=entry["type"], field=field, value=value):
                        entry[field] = value
                        self.assert_failed(base)
                    entry.clear()
                    entry.update(original)
                original = copy.deepcopy(entry)
                entry.clear()
                self.assert_failed(base)
                entry.update(original)

    def test_reject_wrong_controller_parent_namespace_and_listener(self):
        for field, value in (("name", "other"), ("namespace", "other"), ("sectionName", "other")):
            resources = snapshot()
            resources["httproute"]["status"]["parents"][0]["parentRef"][field] = value
            self.assert_failed(resources)
        resources = snapshot()
        resources["httproute"]["status"]["parents"][0]["controllerName"] = "other"
        self.assert_failed(resources)
        resources = snapshot()
        resources["gatewayclass"]["spec"]["controllerName"] = "other"
        self.assert_failed(resources)

    def test_reject_missing_or_invalid_address_and_no_routes(self):
        for address in ([], [{"type": "IPAddress", "value": "invalid"}],
                        [{"type": "Hostname", "value": "example.invalid"}]):
            resources = snapshot()
            resources["gateway"]["status"]["addresses"] = address
            self.assert_failed(resources)
        resources = snapshot()
        resources["gateway"]["status"]["listeners"][0]["attachedRoutes"] = 0
        self.assert_failed(resources)

    def test_duplicate_condition_is_not_accepted(self):
        resources = snapshot()
        resources["gatewayclass"]["status"]["conditions"] *= 2
        self.assert_failed(resources)


class ReportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.kubeconfig = self.directory / "fixture-kubeconfig"
        self.kubeconfig.write_text("fixture only; never read by real kubectl")
        self.output = self.directory / "report.json"
        self.args = ["--mode", "traffic", "--kubeconfig", str(self.kubeconfig),
                     "--base-url", "http://192.0.2.10:30080", "--run-id", "fixture-run",
                     "--request-id", "fixture-access", "--error-marker", "fixture-error",
                     "--requests", "2", "--timeout", "0.05", "--output", str(self.output)]

    @staticmethod
    def response(base, path, request_id, deadline):
        is_error = path.startswith("/verify/missing/") or path == "/unrouted"
        valid_id = request_id is not None and GATEWAY.ID_PATTERN.fullmatch(request_id)
        return {"method": "GET", "uri": path, "status": 404 if is_error else 200,
                "body": "not found" if is_error else "Hello World!", "bytes": 9 if is_error else 12,
                "request_id": request_id if valid_id else "generated-valid-id",
                "request_id_header_count": 1, "body_exact": not is_error}

    def run_fixture(self, response=None, args=None):
        with mock.patch.object(GATEWAY, "request", side_effect=response or self.response):
            code = GATEWAY.main(args or self.args)
        return code, json.loads(self.output.read_text())

    def test_successful_traffic_report_preserves_shared_markers(self):
        code, report = self.run_fixture()
        self.assertEqual(code, 0)
        self.assertEqual(report["data"]["request_id"], "fixture-access")
        self.assertEqual(report["data"]["request_count"], 6)
        self.assertEqual(report["data"]["error_http"]["uri"], "/verify/missing/fixture-error")

    def test_wrong_body_status_id_and_unrouted_success_are_rejected(self):
        for field, value in (("body_exact", False), ("status", 302), ("request_id", None)):
            def bad(base, path, request_id, deadline):
                result = self.response(base, path, request_id, deadline)
                if path == "/":
                    result[field] = value
                return result
            with self.subTest(field=field):
                code, report = self.run_fixture(bad)
                self.assertEqual(code, 1)
                self.assertEqual(report["status"], "fail")
        def bad_route(base, path, request_id, deadline):
            result = self.response(base, path, request_id, deadline)
            if path == "/unrouted":
                result.update(status=200, body_exact=True)
            return result
        self.assertEqual(self.run_fixture(bad_route)[0], 1)

    def test_invalid_fallback_and_error_body_are_rejected(self):
        def bad(base, path, request_id, deadline):
            result = self.response(base, path, request_id, deadline)
            result["request_id"] = request_id
            return result
        self.assertEqual(self.run_fixture(bad)[0], 1)
        def bad_error(base, path, request_id, deadline):
            result = self.response(base, path, request_id, deadline)
            if path.startswith("/verify/missing/"):
                result["body_exact"] = True
            return result
        self.assertEqual(self.run_fixture(bad_error)[0], 1)

    def test_network_error_still_writes_fail_report(self):
        code, report = self.run_fixture(response=TimeoutError("fixture timeout"))
        self.assertEqual(code, 1)
        self.assertTrue(self.output.is_file())
        self.assertEqual(report["status"], "fail")

    def test_bad_arguments_still_write_fail_report(self):
        for extra in (["--requests", "0"], ["--timeout", "nan"], ["--request-id", "bad id"],
                      ["--kubeconfig", str(self.directory / "missing")], ["--base-url", "http://user:secret@example.invalid"]):
            with self.subTest(extra=extra):
                code, report = self.run_fixture(args=self.args + extra)
                self.assertEqual(code, 1)
                self.assertEqual(report["status"], "fail")

    def test_kubectl_always_receives_explicit_kubeconfig(self):
        resources = snapshot()
        calls = []
        def run(command, **kwargs):
            calls.append(command)
            kind = command[command.index("get") + 1].split(".")[0]
            return mock.Mock(returncode=0, stdout=json.dumps(resources[kind]))
        args = self.args + ["--mode", "conditions", "--timeout", "1"]
        with mock.patch.object(GATEWAY.subprocess, "run", side_effect=run):
            self.assertEqual(GATEWAY.main(args), 0)
        self.assertEqual(len(calls), 3)
        for command in calls:
            self.assertEqual(command[command.index("--kubeconfig") + 1], str(self.kubeconfig.resolve()))

    def test_malformed_api_json_does_not_pass(self):
        with mock.patch.object(GATEWAY.subprocess, "run", return_value=mock.Mock(returncode=0, stdout="{}")):
            self.assertEqual(GATEWAY.main(self.args + ["--mode", "conditions"]), 1)
        self.assertEqual(json.loads(self.output.read_text())["status"], "fail")


if __name__ == "__main__":
    unittest.main(verbosity=2)
