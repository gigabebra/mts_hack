#!/usr/bin/env python3
"""Find the shared verification run's Nginx access/error records through Loki."""

import argparse
import datetime
import decimal
import ipaddress
import json
import math
from pathlib import Path
import re
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid


ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z")
SOURCE_PATTERN = re.compile(r"/var/log/containers/hello-[^/]+_demo_nginx-[^/]+\.log\Z")
LABELS = {"app": "hello", "namespace": "demo", "container": "nginx"}
RFC3339 = re.compile(r"(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)(?:\.(\d{1,9}))?(Z|[+-]\d\d:\d\d)\Z")
MAX_RESPONSE = 2 * 1024 * 1024


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


HTTP = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())


class Arguments(argparse.ArgumentParser):
    def error(self, message):
        raise ValueError(message)


def cli(argv):
    parser = Arguments(description=__doc__)
    parser.add_argument("--loki-url", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--request-id")
    parser.add_argument("--error-marker")
    parser.add_argument("--start-time", help="Earliest accepted UNIX seconds, including fractions")
    parser.add_argument("--base-url", help="Create probes only when both IDs are omitted")
    parser.add_argument("--timeout", type=float, default=120)
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    if not math.isfinite(args.timeout) or not 0 < args.timeout <= 1800:
        raise ValueError("--timeout must be finite, positive and at most 1800 seconds")
    for name in ("run_id", "request_id", "error_marker"):
        value = getattr(args, name)
        if value is not None and not ID_PATTERN.fullmatch(value):
            raise ValueError(f"--{name.replace('_', '-')} must match the Request-ID contract")
    if bool(args.request_id) != bool(args.error_marker):
        raise ValueError("--request-id and --error-marker must be supplied together")
    if not args.request_id and not args.base_url:
        raise ValueError("Supply both IDs, or --base-url to generate standalone probes")
    for value in (args.loki_url, args.base_url):
        if value is not None:
            parsed = urllib.parse.urlsplit(value)
            if (parsed.scheme not in ("http", "https") or not parsed.hostname
                    or parsed.username or parsed.password or parsed.query or parsed.fragment):
                raise ValueError("URLs must be HTTP(S) without credentials, query or fragment")
            address = ipaddress.ip_address(parsed.hostname)
            if address.version == 6 and (address.ipv4_mapped is not None or address.scope_id is not None):
                raise ValueError("URLs must use native IPv4 or bracketed IPv6 literals")
            _ = parsed.port
    return args


def seconds_ns(value):
    number = decimal.Decimal(str(value))
    if not number.is_finite() or number < 0:
        raise ValueError("--start-time must be finite nonnegative UNIX seconds")
    return int(number * 1_000_000_000)


def timestamp_ns(value):
    match = RFC3339.fullmatch(value)
    if not match:
        raise ValueError("Not a RFC3339 CRI timestamp")
    whole, fraction, zone = match.groups()
    parsed = datetime.datetime.fromisoformat(whole + zone.replace("Z", "+00:00"))
    return int(parsed.timestamp()) * 1_000_000_000 + int((fraction or "").ljust(9, "0"))


def check(name, passed, expected, actual):
    return {"name": name, "status": "pass" if passed else "fail",
            "expected": expected, "actual": actual}


def budget(deadline):
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("Verification deadline reached")
    return max(0.001, min(10, remaining))


def read_response(request, deadline):
    with HTTP.open(request, timeout=budget(deadline)) as response:
        content = response.read(MAX_RESPONSE + 1)
        if len(content) > MAX_RESPONSE:
            raise ValueError("HTTP response exceeds 2 MiB limit")
        return content


def standalone_probe(base_url, path, request_id, deadline):
    request = urllib.request.Request(base_url.rstrip("/") + path,
                                     headers={"X-Request-ID": request_id})
    try:
        response = HTTP.open(request, timeout=budget(deadline))
    except urllib.error.HTTPError as error:
        response = error
    with response:
        content = response.read(1025)
        return {"status": response.code,
                "request_id": response.headers.get("X-Request-ID"),
                "body": content.decode("utf-8", errors="replace") if len(content) <= 1024 else "<oversize>"}


def log_query(stream, marker):
    labels = dict(LABELS, stream=stream)
    return "{" + ",".join(f"{key}={json.dumps(value)}" for key, value in labels.items()) + "} |= " + json.dumps(marker)


def query_range(url, query, start_ns, end_ns, deadline):
    params = urllib.parse.urlencode({"query": query, "start": start_ns, "end": end_ns,
                                     "limit": 1000, "direction": "forward"})
    request = urllib.request.Request(url.rstrip("/") + "/loki/api/v1/query_range?" + params)
    response = json.loads(read_response(request, deadline))
    if not isinstance(response, dict) or response.get("status") != "success":
        raise ValueError("Loki API returned non-success status")
    data = response.get("data", {})
    if not isinstance(data, dict) or data.get("resultType") != "streams" or not isinstance(data.get("result"), list):
        raise ValueError("Loki API did not return a streams result")
    return data["result"]


def candidate_records(streams, stream, start_ns, end_ns):
    """Ignore unrelated/malformed records and demand CRI source/time evidence."""
    expected_labels = dict(LABELS, stream=stream)
    for result in streams:
        if not isinstance(result, dict) or result.get("stream") != expected_labels:
            continue
        values = result.get("values", [])
        if not isinstance(values, list):
            continue
        for value in values:
            if not isinstance(value, list) or len(value) != 2:
                continue
            raw_timestamp, line = value
            try:
                timestamp = int(raw_timestamp)
                record = json.loads(line)
                if not isinstance(record, dict) or not start_ns <= timestamp <= end_ns:
                    continue
                if not SOURCE_PATTERN.fullmatch(record.get("source_file", "")):
                    continue
                if record.get("cri_flag") != "F" or timestamp_ns(record.get("cri_time", "")) != timestamp:
                    continue
                if not isinstance(record.get("message"), str):
                    continue
            except (TypeError, ValueError, OverflowError):
                continue
            yield {"labels": expected_labels, "timestamp_ns": str(timestamp), "record": record}


def access_match(streams, request_id, start_ns, end_ns):
    expected = {"request_id": request_id, "method": "GET", "uri": "/", "status": 200}
    for match in candidate_records(streams, "stdout", start_ns, end_ns):
        record = match["record"]
        if any(record.get(key) != value for key, value in expected.items()) or type(record.get("status")) is not int:
            continue
        # Verify that parsed fields also came from the preserved original line.
        try:
            original = json.loads(record["message"])
        except (TypeError, ValueError):
            continue
        if not isinstance(original, dict) or any(original.get(key) != value for key, value in expected.items()):
            continue
        return {"labels": match["labels"], "timestamp_ns": match["timestamp_ns"],
                "fields": expected, "cri_time": record["cri_time"], "source_verified": True}
    return None


def error_match(streams, error_marker, start_ns, end_ns):
    uri = "/verify/missing/" + error_marker
    missing = re.compile(r'\bopen\(\) "[^"\n]+" failed \(2: No such file or directory\)')
    request = re.compile(r'\brequest: "GET ' + re.escape(uri) + r' HTTP/1\.[01]"')
    for match in candidate_records(streams, "stderr", start_ns, end_ns):
        record = match["record"]
        message = record["message"]
        if "[error]" in message and missing.search(message) and request.search(message):
            # Raw error lines can contain client addresses; report only the verified facts.
            return {"labels": match["labels"], "timestamp_ns": match["timestamp_ns"],
                    "uri": uri, "missing_file": True, "cri_time": record["cri_time"], "source_verified": True}
    return None


def safe_error(error):
    if isinstance(error, urllib.error.HTTPError):
        return f"Loki HTTP {error.code}"
    if isinstance(error, (urllib.error.URLError, OSError)):
        return type(error).__name__ + ": endpoint unavailable or request timed out"
    return type(error).__name__ + ": " + str(error)[:300]


def verify(args):
    deadline = time.monotonic() + args.timeout
    now_ns = time.time_ns()
    start_ns = seconds_ns(args.start_time) if args.start_time is not None else now_ns - 300_000_000_000
    if start_ns > now_ns:
        raise ValueError("--start-time cannot be in the future")
    generated = not args.request_id
    request_id = args.request_id or "logs-" + uuid.uuid4().hex
    error_marker = args.error_marker or request_id + "-err"
    checks = []
    data = {"run_id": args.run_id, "request_id": request_id, "error_marker": error_marker,
            "start_time": str(decimal.Decimal(start_ns) / 1_000_000_000), "generated_probes": generated}
    if generated:
        start_ns = now_ns - 1_000_000_000 if args.start_time is None else start_ns
        data["start_time"] = str(decimal.Decimal(start_ns) / 1_000_000_000)
        access = standalone_probe(args.base_url, "/", request_id, deadline)
        error = standalone_probe(args.base_url, "/verify/missing/" + error_marker, error_marker, deadline)
        checks.append(check("logs.probe.access", access == {"status": 200, "request_id": request_id, "body": "Hello World!"},
                            {"status": 200, "request_id": request_id, "body": "Hello World!"}, access))
        checks.append(check("logs.probe.error", error["status"] == 404 and error["request_id"] == error_marker,
                            {"status": 404, "request_id": error_marker},
                            {"status": error["status"], "request_id": error["request_id"]}))
    queries = {"access": log_query("stdout", request_id), "error": log_query("stderr", error_marker)}
    data["queries"] = queries
    matches = {"access": None, "error": None}
    last_errors = {}
    attempts = 0
    while time.monotonic() < deadline and not all(matches.values()):
        attempts += 1
        for kind, marker, match_fn in (("access", request_id, access_match), ("error", error_marker, error_match)):
            if matches[kind]:
                continue
            end_ns = time.time_ns()
            try:
                streams = query_range(args.loki_url, queries[kind], start_ns, end_ns, deadline)
                matches[kind] = match_fn(streams, marker, start_ns, end_ns)
                last_errors[kind] = None
            except (urllib.error.URLError, OSError, ValueError, TypeError, TimeoutError) as error:
                last_errors[kind] = safe_error(error)
        if not all(matches.values()):
            time.sleep(min(2, max(0, deadline - time.monotonic())))
    for kind in matches:
        expected = ({"request_id": request_id, "method": "GET", "uri": "/", "status": 200,
                     "labels": dict(LABELS, stream="stdout"), "cri_source_and_timestamp": True}
                    if kind == "access" else
                    {"uri": "/verify/missing/" + error_marker, "missing_file": True,
                     "labels": dict(LABELS, stream="stderr"), "cri_source_and_timestamp": True})
        checks.append(check("logs." + kind, matches[kind] is not None, expected,
                            matches[kind] or {"found": False, "last_error": last_errors.get(kind), "attempts": attempts}))
    data.update({"access_match": matches["access"], "error_match": matches["error"], "poll_attempts": attempts})
    return {"status": "pass" if all(item["status"] == "pass" for item in checks) else "fail", "checks": checks, "data": data}


def save_report(path, report):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, prefix=path.name + ".", delete=False) as target:
        json.dump(report, target, indent=2, ensure_ascii=False)
        target.write("\n")
        temporary = Path(target.name)
    temporary.replace(path)


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    output = None
    # Retain the output path even if argument validation fails.
    for index, arg in enumerate(argv):
        if arg == "--output" and index + 1 < len(argv):
            output = argv[index + 1]
        elif arg.startswith("--output="):
            output = arg.partition("=")[2]
    try:
        args = cli(argv)
        output = args.output
        result = verify(args)
    except Exception as error:
        result = {"status": "fail", "checks": [check("logs.execution", False,
                  "Valid arguments and accessible Loki records", safe_error(error))], "data": {}}
    if output:
        try:
            save_report(output, result)
        except OSError as error:
            print("Cannot write logs report: " + type(error).__name__, file=sys.stderr)
            return 1
    else:
        print(json.dumps(result, ensure_ascii=False), file=sys.stderr)
    return 0 if result["status"] == "pass" else 1


if __name__ == "__main__":
    sys.exit(main())
