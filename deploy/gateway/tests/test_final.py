#!/usr/bin/env python3
"""TLS and routing verifier tests; simulated invalid states must be rejected."""
import copy
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest import mock

from test_render import render_gateway

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts"))
SPEC = importlib.util.spec_from_file_location("final_gateway", ROOT / "scripts/check-gateway.final.py")
FINAL = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(FINAL)


def conditions(*names):
    return [{"type": name, "status": "True", "observedGeneration": 3} for name in names]


def snapshot(split=False):
    docs = render_gateway("--set", "traffic.stableWeight=90,traffic.canaryWeight=10") if split else render_gateway()
    for doc in docs.values():
        doc["metadata"]["generation"] = 3
    gateway = docs[("Gateway", "gateway")]
    gateway["status"] = {"conditions": conditions("Accepted", "Programmed"),
                         "addresses": [{"type": "IPAddress", "value": "2001:db8::10"}],
                         # Live v3.6.2 counts each of the two Gateway refs before
                         # applying its section filter: four routes yield eight.
                         "listeners": [{"name": section, "attachedRoutes": 8,
                                        "conditions": conditions("Accepted", "Programmed", "ResolvedRefs")}
                                       for section in ("web", "websecure")]}
    gc = docs[("GatewayClass", "mts-traefik")]
    gc["status"] = {"conditions": conditions("Accepted")}
    routes = {name: doc for (kind, name), doc in docs.items() if kind == "HTTPRoute"}
    for doc in routes.values():
        doc["status"] = {"parents": [{"controllerName": FINAL.MVP.CONTROLLER, "parentRef": parent,
                                      "conditions": conditions("Accepted", "ResolvedRefs")}
                                     for parent in doc["spec"]["parentRefs"]]}
    return {"gatewayclass": gc, "gateway": gateway, "routes": routes}


class CurrentRouteChecks(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.baseline = snapshot()

    def is_pass(self, data, split=False):
        return all(item["status"] == "pass" for item in FINAL.route_checks(data, "hello.test", split, "2001:db8::10"))

    def test_current_and_exact_specs_pass_for_100_0_and_90_10(self):
        self.assertTrue(self.is_pass(self.baseline))
        self.assertTrue(self.is_pass(snapshot(True), True))
        self.assertFalse(self.is_pass(self.baseline, True))

    def test_pinned_controller_count_matches_live_pattern_exactly(self):
        self.assertTrue(self.is_pass(self.baseline))
        for section in (0, 1):
            for count in (0, 4, 7, 9):
                data = copy.deepcopy(self.baseline)
                data["gateway"]["status"]["listeners"][section]["attachedRoutes"] = count
                self.assertFalse(self.is_pass(data), (section, count))
        with mock.patch.dict(FINAL.VERSIONS["images"], {"traefik": "docker.io/library/traefik:v3.7.0"}):
            self.assertFalse(self.is_pass(self.baseline))

    def test_old_https_listener_hostname_and_generic_route_response_fail(self):
        data = copy.deepcopy(self.baseline)
        data["gateway"]["spec"]["listeners"][1]["hostname"] = "hello.test"
        self.assertFalse(self.is_pass(data))
        # The live failure had valid TLS, body and release headers; only the
        # route marker exposed the lost hostname specificity. Keep it required.
        response = {"curl_exit": 0, "status": 200, "body_exact": True, "release": "stable",
                    "revision": "stable-1", "request_id": "fixture", "route": "hello"}
        self.assertFalse(FINAL.response_ok(response, "fixture", expected_route="hello-host"))
        response["route"] = "hello-host"
        self.assertTrue(FINAL.response_ok(response, "fixture", expected_route="hello-host"))

    def test_eight_attachments_cannot_hide_wrong_routes_or_status_parents(self):
        for mutate in (
                lambda data: data["routes"].pop("hello-canary"),
                lambda data: data["routes"].update({"unexpected": copy.deepcopy(data["routes"]["hello"])}),
                lambda data: data["routes"]["hello"]["spec"]["parentRefs"].pop(),
                lambda data: data["routes"]["hello"]["status"]["parents"].pop(),
                lambda data: data["routes"]["hello"]["status"]["parents"].append(
                    copy.deepcopy(data["routes"]["hello"]["status"]["parents"][0])),
                lambda data: data["routes"]["hello"]["status"]["parents"].append(
                    {"controllerName": "another-controller", "parentRef": {"name": "another-gateway"}})):
            data = copy.deepcopy(self.baseline)
            mutate(data)
            self.assertFalse(self.is_pass(data))

    def test_reject_stale_unknown_missing_and_duplicate_conditions_everywhere(self):
        original = copy.deepcopy(self.baseline)
        groups = [original["gateway"]["status"]["conditions"]]
        groups += [item["conditions"] for item in original["gateway"]["status"]["listeners"]]
        groups += [item["conditions"] for route in original["routes"].values() for item in route["status"]["parents"]]
        for group in groups:
            for item in group:
                saved = copy.deepcopy(item)
                for patch in ({"status": "Unknown"}, {"status": "False"}, {"observedGeneration": 2}):
                    item.update(patch)
                    self.assertFalse(self.is_pass(original))
                    item.clear()
                    item.update(saved)
                item.clear()
                self.assertFalse(self.is_pass(original))
                item.update(saved)
            group.append(copy.deepcopy(group[0]))
            self.assertFalse(self.is_pass(original))
            group.pop()

    def test_wrong_weights_backend_namespace_and_extra_rule_are_rejected(self):
        for name in ("hello", "hello-host", "hello-stable", "hello-canary"):
            for field, value in (("weight", 42), ("name", "unknown"), ("namespace", "other"), ("port", 81)):
                data = copy.deepcopy(self.baseline)
                data["routes"][name]["spec"]["rules"][0]["backendRefs"][0][field] = value
                self.assertFalse(self.is_pass(data), (name, field))
            data = copy.deepcopy(self.baseline)
            data["routes"][name]["spec"]["rules"].append(copy.deepcopy(data["routes"][name]["spec"]["rules"][0]))
            self.assertFalse(self.is_pass(data))

    def test_wrong_listener_certificate_parent_and_path_are_rejected(self):
        for modify in (
                lambda data: data["gateway"]["spec"]["listeners"][1]["tls"]["certificateRefs"][0].update(name="other"),
                lambda data: data["routes"]["hello-host"]["spec"].update(hostnames=["other.test"]),
                lambda data: data["routes"]["hello-canary"]["spec"]["rules"][0]["filters"].clear(),
                lambda data: data["routes"]["hello-canary"]["spec"]["rules"][0]["matches"][0]["path"].update(type="PathPrefix"),
                lambda data: data["routes"]["hello-host"]["status"]["parents"][1]["parentRef"].update(namespace="other"),
                lambda data: data["routes"].pop("hello-canary")):
            data = copy.deepcopy(self.baseline)
            modify(data)
            self.assertFalse(self.is_pass(data))

    def test_wait_keeps_failed_condition_details_at_deadline(self):
        failure = [{"name": "wrong-weight", "status": "fail"}]
        with mock.patch.object(FINAL, "snapshot", return_value=self.baseline) as read, \
                mock.patch.object(FINAL, "route_checks", return_value=failure), \
                mock.patch.object(FINAL.time, "monotonic", side_effect=[0, 0, 0, 0, 61]), \
                mock.patch.object(FINAL.time, "sleep"):
            self.assertEqual(FINAL.wait_routes({"tls_hostname": "hello.test", "node_ip": "2001:db8::10"}, 100, False), failure)
        read.assert_called_once()


class HttpChecks(unittest.TestCase):
    def response(self):
        return {"curl_exit": 0, "status": 200, "body_exact": True, "release": "canary",
                "revision": "candidate-1", "request_id": "fixture", "route": "hello"}

    def test_bad_body_status_release_request_id_and_revision_cannot_count(self):
        self.assertTrue(FINAL.response_ok(self.response(), "fixture"))
        for field, value in (("curl_exit", 60), ("status", 500), ("body_exact", False),
                             ("release", "unknown"), ("revision", "bad\nrevision"), ("request_id", "wrong")):
            response = self.response()
            response[field] = value
            self.assertFalse(FINAL.response_ok(response, "fixture"))
        self.assertFalse(FINAL.response_ok(self.response(), "fixture", expected_release="stable"))
        self.assertFalse(FINAL.response_ok(self.response(), "fixture", expected_route="hello-host"))
        self.assertFalse(FINAL.response_ok(self.response(), "fixture", revisions={"canary": "old"}))

    def test_current_spec_does_not_accept_stale_or_missing_served_split(self):
        response = self.response()
        for expected in ("100/0", "90/10"):
            for observed in (None, "", "100/0" if expected == "90/10" else "90/10", "invalid"):
                response["traffic_split"] = observed
                self.assertFalse(FINAL.response_ok(response, "fixture", expected_route="hello", expected_split=expected))
            response["traffic_split"] = expected
            self.assertTrue(FINAL.response_ok(response, "fixture", expected_route="hello", expected_split=expected))

    def test_split_statistical_band_missing_responses_and_no_canary_baseline(self):
        for count in (50, 87, 100, 113, 150):
            self.assertEqual(FINAL.distribution_check({"stable": 1000 - count, "canary": count}, 1000, True)["status"], "pass")
        for counts, total in (({"stable": 1000}, 1000), ({"stable": 849, "canary": 151}, 1000),
                              ({"stable": 900, "canary": 99}, 1000), ({"stable": 90, "canary": 10}, 100)):
            self.assertEqual(FINAL.distribution_check(counts, total, True)["status"], "fail")
        self.assertEqual(FINAL.distribution_check({"stable": 1000}, 1000, False)["status"], "pass")
        self.assertEqual(FINAL.distribution_check({"stable": 999, "canary": 1}, 1000, False)["status"], "fail")

    def test_curl_enforces_ca_host_direct_family_and_no_retry(self):
        env = {"node_ip": "2001:db8::10", "node_ipv4": "192.0.2.10", "node_port": 30080,
               "https_node_port": 30443, "tls_hostname": "hello.test"}

        def run(argv, **kwargs):
            self.assertEqual(argv[argv.index("--retry") + 1], "0")
            self.assertEqual(argv[argv.index("--cacert") + 1], "/fixture/ca.crt")
            self.assertEqual(argv[argv.index("--noproxy") + 1], "*")
            self.assertNotIn("--insecure", argv)
            self.assertNotIn("--location", argv)
            self.assertIn("hello.test:30443:[2001:db8::10]", argv)
            self.assertIn("--ipv6", argv)
            self.assertEqual(argv[-1], "https://hello.test:30443/backend/canary")
            Path(argv[argv.index("--dump-header") + 1]).write_bytes(
                b"HTTP/1.1 200 OK\r\nX-Release: canary\r\nX-Revision: candidate-1\r\nX-Request-ID: fixture\r\n\r\n")
            Path(argv[argv.index("--output") + 1]).write_bytes(b"Hello World!")
            return mock.Mock(returncode=0, stdout=b"200")

        with mock.patch.object(FINAL.subprocess, "run", side_effect=run):
            response = FINAL.curl_probe(env, 6, "/backend/canary", "fixture", time.monotonic() + 5,
                                        tls=True, ca="/fixture/ca.crt")
        self.assertTrue(FINAL.response_ok(response, "fixture", expected_release="canary"))
        with mock.patch.object(FINAL.subprocess, "run", return_value=mock.Mock(returncode=60)):
            response = FINAL.curl_probe(env, 4, "/", "fixture", time.monotonic() + 5, tls=True, ca="/fixture/ca.crt")
        self.assertFalse(FINAL.response_ok(response, "fixture"))

    def test_environment_snapshot_uses_only_explicit_kubeconfig(self):
        env = {"kubeconfig": "/fixture/explicit-kubeconfig"}
        calls = []

        def run(argv, **kwargs):
            calls.append(argv)
            return mock.Mock(stdout=json.dumps({"items": []}))

        with mock.patch("stacklib.command", side_effect=run):
            FINAL.snapshot(env, time.monotonic() + 5)
        for argv in calls:
            self.assertEqual(argv[argv.index("--kubeconfig") + 1], env["kubeconfig"])

    def test_invalid_inputs_write_failure_report_without_api(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "report.json"
            for extra in (("--requests", "99"), ("--split", "--requests", "999"), ("--timeout", "nan"),
                          ("--env", "../other"), ("--run-id", "bad id")):
                with mock.patch.object(FINAL, "environment") as env:
                    self.assertEqual(FINAL.main(["--output", str(output), *extra]), 1)
                    env.assert_not_called()
                self.assertEqual(json.loads(output.read_text())["status"], "fail")


if __name__ == "__main__":
    unittest.main(verbosity=2)
