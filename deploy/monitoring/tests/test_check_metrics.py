"""HTTP API fixtures, not live Kubernetes acceptance."""

import datetime as dt
import http.server
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.parse


SCRIPT = Path(__file__).resolve().parents[3] / "scripts/check-metrics.py"
SERVICES = [
    ("edge", "traefik-metrics", "metrics"),
    ("monitoring", "monitoring-prometheus-node-exporter", "http-metrics"),
    ("monitoring", "monitoring-kube-state-metrics", "http"),
    ("monitoring", "monitoring-prometheus", "http-web"),
]


def labels(target):
    return dict(zip(("namespace", "service", "endpoint"), target), job=target[1], instance="fixture:9100")


def matrix_row(metric, points):
    return {"metric": metric, "values": [[stamp, str(value)] for stamp, value in points]}


class Fixture(http.server.BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def do_GET(self):
        state = self.server.state
        parsed = urllib.parse.urlsplit(self.path)
        now = time.time()
        mode = state["failure"]
        if mode == "http_503":
            self.send_error(503)
            return
        if parsed.path.endswith("/targets"):
            targets = []
            for target in SERVICES:
                stamp = now - (100 if mode == "stale_target" else 0.1)
                targets.append({"labels": labels(target), "health": "down" if mode == "down" else "up",
                                "lastScrape": dt.datetime.fromtimestamp(stamp, dt.timezone.utc).isoformat(),
                                "lastError": ""})
            if mode == "missing_target":
                targets.pop(1)
            if mode == "reloader_down":
                reloader = dict(targets[-1], labels=dict(targets[-1]["labels"], endpoint="reloader-web"), health="down")
                targets.append(reloader)
            data = {"activeTargets": targets}
        else:
            query = urllib.parse.parse_qs(parsed.query)["query"][0]
            state["queries"].append(query)
            if query.startswith("traefik_entrypoint"):
                metric = dict(labels(SERVICES[0]), __name__="traefik_entrypoint_requests_total",
                              entrypoint="web", code="200", method="GET")
                points = [(state["sample_at"], 100)]
                if state["verify"]:
                    if mode == "reset":
                        points.append((state["sample_at"] + 0.01, 2))
                    if mode != "no_new_scrape":
                        points.append((now - 0.001, 120 if mode != "insufficient" else 119))
                    if mode == "lost_baseline":
                        points.pop(0)
                    if mode == "changed_series":
                        metric["instance"] = "replacement:9100"
                if mode == "stale_sample":
                    points = [(now - 80, 100)]
                if mode == "nan":
                    points = [(now, "NaN")]
                data = {"resultType": "matrix", "result": [matrix_row(metric, points)]}
                if mode == "empty":
                    data["result"] = []
                if mode == "vector":
                    data["resultType"] = "vector"
            else:
                rows = [matrix_row(dict(labels(t), __name__="up"), [(now - 0.1, 0 if mode == "up_zero" else 1)])
                        for t in SERVICES]
                if mode != "missing_infrastructure":
                    rows += [matrix_row(dict(labels(SERVICES[1]), __name__="node_memory_MemAvailable_bytes"),
                                        [(now - 0.1, 1024)]),
                             matrix_row(dict(labels(SERVICES[2]), __name__="kube_node_info", node="fixture"),
                                        [(now - 0.1, 1)])]
                data = {"resultType": "matrix", "result": rows}
        payload = {"status": "success", "data": data}
        if mode == "warning":
            payload["warnings"] = ["incomplete response"]
        body = b"invalid-json" if mode == "invalid_json" else json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class MetricsFixtures(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Fixture)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.baseline = Path(self.temp.name) / "baseline.json"
        self.server.state = {"failure": None, "verify": False, "sample_at": time.time() - 1, "queries": []}

    def invoke(self, mode, *extra):
        output = self.baseline if mode == "snapshot" else Path(self.temp.name) / "result.json"
        command = [sys.executable, str(SCRIPT), "--mode", mode, "--prometheus-url",
                   f"http://127.0.0.1:{self.server.server_port}", "--run-id", "fixture-run",
                   "--timeout", "0.15", "--output", str(output)]
        if mode == "verify":
            command += ["--baseline", str(self.baseline)]
            self.server.state["verify"] = True
        completed = subprocess.run(command + list(extra), capture_output=True, text=True, timeout=3)
        self.assertTrue(output.exists(), completed.stderr)
        report = json.loads(output.read_text())
        self.assertEqual(completed.returncode, 0 if report["status"] == "pass" else 1, completed.stderr)
        return report

    def test_snapshot_then_verify(self):
        before = self.invoke("snapshot")
        self.assertEqual(before["status"], "pass")
        self.assertEqual(before["data"]["series"][0]["timestamp"], self.server.state["sample_at"])
        after = self.invoke("verify")
        self.assertEqual(after["status"], "pass", after)
        self.assertEqual(after["data"]["increase"], 20)
        self.assertTrue(all("[" in q and q.endswith("ms]") for q in self.server.state["queries"]))

    def test_snapshot_rejects_invalid_or_missing_data(self):
        for failure in ("http_503", "down", "reloader_down", "missing_target", "stale_target", "empty", "vector",
                        "stale_sample", "nan", "up_zero", "missing_infrastructure", "warning", "invalid_json"):
            with self.subTest(failure=failure):
                self.server.state["failure"] = failure
                report = self.invoke("snapshot")
                self.assertEqual(report["status"], "fail", failure)

    def test_verify_rejects_counter_false_positives(self):
        for failure, failed_check in (
            ("reset", "counter_no_reset"),
            ("lost_baseline", "counter_baseline_samples"),
            ("changed_series", "counter_series_identity"),
            ("no_new_scrape", "counter_new_scrape"),
            ("insufficient", "counter_increase"),
        ):
            with self.subTest(failure=failure):
                self.server.state["verify"] = False
                self.server.state["failure"] = None
                self.assertEqual(self.invoke("snapshot")["status"], "pass")
                self.server.state["failure"] = failure
                report = self.invoke("verify")
                self.assertEqual(report["status"], "fail", failure)
                self.assertIn(failed_check, [c["name"] for c in report["checks"] if c["status"] == "fail"])

    def test_invalid_baseline_and_inputs(self):
        self.assertEqual(self.invoke("snapshot")["status"], "pass")
        self.assertEqual(self.invoke("verify", "--run-id", "different-run")["status"], "fail")
        self.assertEqual(self.invoke("verify", "--min-increase", "0")["status"], "fail")
        self.assertEqual(self.invoke("verify", "--timeout", "-1")["status"], "fail")
        self.baseline.write_text("{}")
        self.assertEqual(self.invoke("verify")["status"], "fail")


if __name__ == "__main__":
    unittest.main()
