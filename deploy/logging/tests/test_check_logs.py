"""Protocol fixtures; these tests do not establish Kubernetes/live acceptance."""

import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts/check-logs.py"
SPEC = importlib.util.spec_from_file_location("check_logs", SCRIPT)
LOGS = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(LOGS)


def record(stream="stdout", marker="fixture", timestamp="2026-10-01T12:00:00.123456789Z"):
    fields = {"time": timestamp, "request_id": marker, "method": "GET", "uri": "/", "status": 200}
    message = json.dumps(fields) if stream == "stdout" else (
        '2026/10/01 12:00:00 [error] 1#1: *1 open() "/app/verify/missing/' + marker
        + '" failed (2: No such file or directory), client: 2001:db8::2, request: "GET /verify/missing/'
        + marker + ' HTTP/1.1", host: "fixture"')
    result = {"cri_time": timestamp, "cri_flag": "F", "message": message,
              "source_file": "/var/log/containers/hello-fixture_demo_nginx-1234.log"}
    if stream == "stdout":
        result.update(fields)
    return [{"stream": dict(LOGS.LABELS, stream=stream),
             "values": [[str(LOGS.timestamp_ns(timestamp)), json.dumps(result)]]}]


class CheckLogs(unittest.TestCase):
    def setUp(self):
        self.start = LOGS.timestamp_ns("2026-10-01T11:59:59Z")
        self.end = LOGS.timestamp_ns("2026-10-01T12:00:01Z")

    def test_exact_fields_and_nanosecond_timestamp(self):
        self.assertTrue(LOGS.access_match(record(), "fixture", self.start, self.end))
        for key, value in (("request_id", "fixture-extra"), ("method", "POST"),
                           ("uri", "/verify"), ("status", "200"), ("status", 404)):
            fixture = record()
            item = json.loads(fixture[0]["values"][0][1])
            item[key] = value
            fixture[0]["values"][0][1] = json.dumps(item)
            with self.subTest(key=key, value=value):
                self.assertIsNone(LOGS.access_match(fixture, "fixture", self.start, self.end))

    def test_rejects_wrong_source_extra_labels_old_time_and_timestamp_rewrite(self):
        for mutation in ("label", "extra", "old", "source", "time", "raw"):
            fixture = record()
            item = json.loads(fixture[0]["values"][0][1])
            if mutation == "label":
                fixture[0]["stream"]["container"] = "fluentd"
            elif mutation == "extra":
                fixture[0]["stream"]["request_id"] = "fixture"
            elif mutation == "old":
                fixture[0]["values"][0][0] = str(self.start - 1)
            elif mutation == "source":
                item["source_file"] = "/var/log/containers/fluentd-fixture_logging_fluentd-123.log"
            elif mutation == "time":
                item["cri_time"] = "2026-10-01T12:00:00.123456788Z"
            else:
                item["message"] = '{"request_id":"unrelated"}'
            fixture[0]["values"][0][1] = json.dumps(item)
            with self.subTest(mutation=mutation):
                self.assertIsNone(LOGS.access_match(fixture, "fixture", self.start, self.end))

    def test_real_missing_file_shape_and_exact_error_marker(self):
        self.assertTrue(LOGS.error_match(record("stderr"), "fixture", self.start, self.end))
        self.assertIsNone(LOGS.error_match(record("stderr", "fixture-other"), "fixture", self.start, self.end))
        fixture = record("stderr")
        fixture[0]["values"][0][1] = fixture[0]["values"][0][1].replace("No such file or directory", "Permission denied")
        self.assertIsNone(LOGS.error_match(fixture, "fixture", self.start, self.end))

    def test_supplied_ids_never_generate_http_probes(self):
        args = LOGS.cli(["--loki-url", "http://[::1]", "--base-url", "http://[2001:db8::10]",
                         "--run-id", "fixture", "--request-id", "fixture", "--error-marker", "fixture",
                         "--start-time", "0", "--timeout", "1", "--output", "unused"])
        with patch.object(LOGS, "standalone_probe", side_effect=AssertionError("unexpected probe")), \
                patch.object(LOGS, "query_range", side_effect=[record(), record("stderr")]), \
                patch.object(LOGS.time, "time_ns", return_value=self.end):
            result = LOGS.verify(args)
        self.assertEqual(result["status"], "pass")
        self.assertFalse(result["data"]["generated_probes"])

    def test_standalone_exactly_two_probes(self):
        args = LOGS.cli(["--loki-url", "http://[::1]", "--base-url", "http://[::1]",
                         "--run-id", "fixture", "--timeout", "0.01", "--output", "unused"])
        def probe(url, path, request_id, deadline):
            return {"status": 200 if path == "/" else 404, "request_id": request_id,
                    "body": "Hello World!" if path == "/" else "missing"}
        with patch.object(LOGS, "standalone_probe", side_effect=probe) as mocked, \
                patch.object(LOGS, "query_range", return_value=[]):
            result = LOGS.verify(args)
        self.assertEqual(mocked.call_count, 2)
        self.assertTrue(result["data"]["generated_probes"])
        self.assertEqual(result["status"], "fail")  # probes alone never prove log delivery

    def test_cli_reports_bad_arguments_and_unavailable_api(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "result.json"
            common = [sys.executable, str(SCRIPT), "--output", str(path), "--run-id", "fixture",
                      "--loki-url", "http://[::1]:1", "--request-id", "fixture", "--error-marker", "fixture"]
            for extra in (["--loki-url", "http://127.0.0.1:3100"],
                          ["--loki-url", "http://[::ffff:192.0.2.10]:3100"], ["--timeout", "nan"], ["--timeout", "0.05"], ["--start-time", "nan"]):
                started = time.monotonic()
                result = subprocess.run(common + extra, capture_output=True, timeout=3)
                self.assertEqual(result.returncode, 1)
                self.assertLess(time.monotonic() - started, 2)
                self.assertEqual(json.loads(path.read_text())["status"], "fail")

    def test_http_protocol_empty_error_and_malformed_results_fail(self):
        for payload in ({"status": "success", "data": {"resultType": "streams", "result": []}},
                        {"status": "error"}, {"status": "success", "data": {"resultType": "vector", "result": []}},
                        {"status": "success", "data": []}):
            with self.subTest(payload=payload), patch.object(LOGS, "read_response", return_value=json.dumps(payload).encode()):
                args = LOGS.cli(["--loki-url", "http://[::1]", "--run-id", "fixture", "--request-id", "fixture",
                                 "--error-marker", "fixture", "--timeout", "0.005", "--output", "unused"])
                result = LOGS.verify(args)
                self.assertEqual(result["status"], "fail")


if __name__ == "__main__":
    unittest.main()
