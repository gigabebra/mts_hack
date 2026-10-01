#!/usr/bin/env python3
"""Check real Prometheus samples; never treat missing data as a zero counter."""

import argparse
import datetime as dt
import ipaddress
import json
import math
from pathlib import Path
import sys
import time
import urllib.error
import urllib.parse
import urllib.request


COUNTER = ('traefik_entrypoint_requests_total{namespace="edge",service="traefik-metrics",'
           'endpoint="metrics",entrypoint="web",code="200",method="GET"}')
INFRA = '{__name__=~"up|node_memory_MemAvailable_bytes|kube_node_info",namespace=~"edge|monitoring"}'
REQUIRED = {
    "traefik": ("edge", "traefik-metrics", "metrics"),
    "node_exporter": ("monitoring", "monitoring-prometheus-node-exporter", "http-metrics"),
    "kube_state_metrics": ("monitoring", "monitoring-kube-state-metrics", "http"),
    "prometheus": ("monitoring", "monitoring-prometheus", "http-web"),
}
MAX_AGE = 45.0  # Three configured scrape intervals; not Prometheus's 5m lookback.
MAX_BASELINE_AGE = 3600.0
MAX_BODY = 4 * 1024 * 1024


def check(name, passed, expected, actual):
    return {"name": name, "status": "pass" if passed else "fail",
            "expected": expected, "actual": actual}


def number(value):
    if isinstance(value, bool):
        raise ValueError("boolean is not a numeric sample")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError("non-finite sample/timestamp")
    return result


def identity(metric):
    if not isinstance(metric, dict) or not metric or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in metric.items()
    ):
        raise ValueError("invalid series labels")
    return tuple(sorted(metric.items()))


def fresh(timestamp, now):
    return -5 <= now - timestamp <= MAX_AGE


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class API:
    def __init__(self, base, deadline):
        parsed = urllib.parse.urlsplit(base)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            raise ValueError("prometheus-url must be an HTTP(S) URL")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("prometheus-url must not contain credentials/query/fragment")
        address = ipaddress.ip_address(parsed.hostname)
        if address.version != 6 or address.ipv4_mapped is not None:
            raise ValueError("prometheus-url must use a native IPv6 literal in brackets")
        _ = parsed.port
        self.base = base.rstrip("/")
        self.deadline = deadline
        # Local port forwards must not use an environment HTTP proxy.
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())

    def get(self, endpoint, **parameters):
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("metrics deadline exceeded")
        url = self.base + "/api/v1/" + endpoint
        if parameters:
            url += "?" + urllib.parse.urlencode(parameters)
        with self.opener.open(url, timeout=min(5, remaining)) as response:
            body = response.read(MAX_BODY + 1)
        if len(body) > MAX_BODY:
            raise ValueError("Prometheus response exceeds 4 MiB")
        result = json.loads(body)
        if not isinstance(result, dict) or result.get("status") != "success":
            raise ValueError("Prometheus API returned a non-success response")
        if result.get("warnings"):
            raise ValueError("Prometheus API returned warnings; completeness is uncertain")
        if not isinstance(result.get("data"), dict):
            raise ValueError("Prometheus API data is not an object")
        return result["data"]

    def matrix(self, selector, window, now):
        # A range selector returns stored sample timestamps. Instant-query vector
        # timestamps are evaluation times and cannot establish scrape freshness.
        query = selector + "[" + str(math.ceil(window * 1000)) + "ms]"
        remaining = max(0.001, self.deadline - time.monotonic())
        data = self.get("query", query=query, time=f"{now:.6f}", timeout=f"{max(1, int(remaining * 1000))}ms")
        if data.get("resultType") != "matrix" or not isinstance(data.get("result"), list):
            raise ValueError("expected range-vector matrix from Prometheus")
        series = {}
        for row in data["result"]:
            key = identity(row["metric"])
            if key in series or not isinstance(row.get("values"), list) or not row["values"]:
                raise ValueError("duplicate or empty series")
            samples = []
            for value in row["values"]:
                if not isinstance(value, list) or len(value) != 2:
                    raise ValueError("malformed sample")
                stamp, count = map(number, value)
                if count < 0 or stamp > now + 5:
                    raise ValueError("negative metric or future sample")
                if samples and stamp <= samples[-1][0]:
                    raise ValueError("unordered sample timestamps")
                samples.append([stamp, count])
            series[key] = {"metric": row["metric"], "samples": samples,
                           "timestamp": samples[-1][0], "value": samples[-1][1]}
        return query, series


def target_matches(labels, expected):
    return (labels.get("namespace"), labels.get("service"), labels.get("endpoint")) == expected


def inspect_targets(api, now):
    targets = api.get("targets", state="active").get("activeTargets")
    if not isinstance(targets, list):
        raise ValueError("missing activeTargets array")
    checks, saved, selected = [], [], []
    for name, expected in REQUIRED.items():
        matched = [target for target in targets
                   if (target.get("labels", {}).get("namespace"),
                       target.get("labels", {}).get("service")) == expected[:2]]
        main_endpoint_present = any(target_matches(t["labels"], expected) for t in matched)
        evidence = []
        for target in matched:
            stamp = dt.datetime.fromisoformat(target["lastScrape"].replace("Z", "+00:00")).timestamp()
            try:
                scrape = urllib.parse.urlsplit(target.get("scrapeUrl", ""))
                address = ipaddress.ip_address(scrape.hostname or "")
                instance = urllib.parse.urlsplit("http://" + target.get("labels", {}).get("instance", ""))
                instance_address = ipaddress.ip_address(instance.hostname or "")
                native_ipv6 = (scrape.scheme in ("http", "https") and address.version == 6 and address.ipv4_mapped is None and scrape.port is not None
                               and instance_address.version == 6 and instance_address.ipv4_mapped is None
                               and instance.port is not None)
            except ValueError:
                native_ipv6 = False
            item = {"labels": target["labels"], "ipv6": native_ipv6, "health": target.get("health"),
                    "last_scrape": stamp, "has_error": bool(target.get("lastError")),
                    "fresh": fresh(stamp, now)}
            evidence.append(item)
            selected.append(target["labels"])
        passed = main_endpoint_present and bool(evidence) and all(
            item["health"] == "up" and item["fresh"] and not item["has_error"] and item["ipv6"] for item in evidence
        )
        checks.append(check("target_" + name, passed, "present, native IPv6 scrape URL, up, fresh, no scrape error", evidence))
        saved.extend(evidence)
    return checks, saved, selected


def inspect_infrastructure(api, now, selected):
    query, series = api.matrix(INFRA, MAX_AGE + 1, now)
    values = list(series.values())
    up_matches = []
    for labels in selected:
        matches = [s for s in values if s["metric"].get("__name__") == "up"
                   and all(s["metric"].get(k) == v for k, v in labels.items())]
        up_matches.append(bool(matches) and all(s["value"] == 1 and fresh(s["timestamp"], now)
                                                for s in matches))
    checks = [check("target_up_samples", bool(up_matches) and all(up_matches),
                    "fresh up=1 for each required target", up_matches)]
    for name, metric, target in (
        ("node_memory", "node_memory_MemAvailable_bytes", REQUIRED["node_exporter"]),
        ("kubernetes_node", "kube_node_info", REQUIRED["kube_state_metrics"]),
    ):
        matches = [s for s in values if s["metric"].get("__name__") == metric
                   and target_matches(s["metric"], target)]
        passed = bool(matches) and all(fresh(s["timestamp"], now) for s in matches)
        if name == "kubernetes_node":
            passed = passed and all(s["value"] == 1 for s in matches)
        checks.append(check(name, passed, "fresh infrastructure sample", compact(matches)))
    return checks, query, compact(values)


def compact(series):
    return [{"metric": s["metric"], "timestamp": s["timestamp"], "value": s["value"]} for s in series]


def load_baseline(path, run_id, now):
    with Path(path).open() as stream:
        document = json.load(stream)
    data = document["data"]
    if (document.get("status") != "pass" or data.get("mode") != "snapshot"
            or data.get("run_id") != run_id or data.get("query") != COUNTER):
        raise ValueError("baseline must be a successful snapshot of this run and exact query")
    captured = number(data["captured_at"])
    if not 0 <= now - captured <= MAX_BASELINE_AGE:
        raise ValueError("baseline is from the future or older than one hour")
    series = {}
    for row in data["series"]:
        key = identity(row["metric"])
        value, timestamp = number(row["value"]), number(row["timestamp"])
        if key in series or value < 0 or not fresh(timestamp, captured):
            raise ValueError("baseline has duplicate, negative, or stale samples")
        series[key] = {"metric": row["metric"], "value": value, "timestamp": timestamp}
    if not series:
        raise ValueError("baseline contains no series")
    return captured, series


def compare(current, baseline, captured, now, minimum):
    checks = [check("counter_series_identity", set(current) == set(baseline),
                    "same complete series set before and after", {"before": len(baseline), "after": len(current)})]
    if set(current) != set(baseline):
        return checks, {"increase": None}
    resets, anchors, advances = [], [], []
    for key, before in baseline.items():
        after = current[key]
        samples = [p for p in after["samples"] if p[0] >= before["timestamp"]]
        anchors.append(bool(samples) and samples[0] == [before["timestamp"], before["value"]])
        resets.append(any(right[1] < left[1] for left, right in zip(samples, samples[1:])))
        advances.append(after["timestamp"] > max(captured, before["timestamp"]) and fresh(after["timestamp"], now))
    before_total = sum(s["value"] for s in baseline.values())
    after_total = sum(s["value"] for s in current.values())
    increase = after_total - before_total
    checks.extend([
        check("counter_baseline_samples", all(anchors), "baseline raw samples still present", anchors),
        check("counter_no_reset", not any(resets), "no observed decrease in any series", resets),
        check("counter_new_scrape", all(advances), "fresh raw sample after baseline capture", advances),
        check("counter_increase", increase >= minimum, {"minimum": minimum}, increase),
    ])
    return checks, {"before": before_total, "after": after_total, "increase": increase,
                    "min_increase": minimum, "window_start": min(s["timestamp"] for s in baseline.values()),
                    "window_end": now}


def run(args):
    started = time.time()
    data = {"mode": args.mode, "run_id": args.run_id, "query": COUNTER,
            "started_at": started, "max_sample_age_seconds": MAX_AGE}
    report = {"status": "fail", "checks": [], "data": data}
    try:
        if not math.isfinite(args.timeout) or args.timeout <= 0:
            raise ValueError("timeout must be positive and finite")
        if not math.isfinite(args.min_increase) or args.min_increase <= 0:
            raise ValueError("min-increase must be positive and finite")
        if not args.run_id:
            raise ValueError("run-id must not be empty")
        deadline = time.monotonic() + args.timeout
        api = API(args.prometheus_url, deadline)
        baseline, captured = None, None
        if args.mode == "verify":
            if not args.baseline:
                raise ValueError("verify requires --baseline")
            captured, baseline = load_baseline(args.baseline, args.run_id, started)
        attempts = 0
        while True:
            if attempts and time.monotonic() >= deadline:
                report["checks"].append(check("deadline", False, "checks pass within timeout", args.timeout))
                break
            attempts += 1
            now = time.time()
            try:
                checks, targets, selected = inspect_targets(api, now)
                infra_checks, infra_query, infrastructure = inspect_infrastructure(api, now, selected)
                checks.extend(infra_checks)
                window = MAX_AGE + 1 if baseline is None else now - min(s["timestamp"] for s in baseline.values()) + 1
                raw_query, series = api.matrix(COUNTER, window, now)
                checks.append(check("counter_present", bool(series), "nonempty counter series", len(series)))
                checks.append(check("counter_fresh", bool(series) and all(fresh(s["timestamp"], now) for s in series.values()),
                                    "all raw samples at most 45s old", compact(series.values())))
                data.update({"captured_at": now, "targets": targets, "series": compact(series.values()),
                             "raw_query": raw_query, "infrastructure_query": infra_query,
                             "infrastructure": infrastructure, "attempts": attempts})
                if baseline is not None:
                    delta_checks, delta = compare(series, baseline, captured, now, args.min_increase)
                    checks.extend(delta_checks)
                    data.update(delta)
                report["checks"] = checks
                if all(item["status"] == "pass" for item in checks):
                    report["status"] = "pass"
                    break
                # An observed reset cannot be healed by waiting for more traffic.
                if any(c["name"] == "counter_no_reset" and c["status"] == "fail" for c in checks):
                    break
            except Exception as exc:
                # Malformed/unexpected API payloads also produce a failure report.
                report["checks"] = [check("prometheus_api", False, "valid, complete API responses",
                                          type(exc).__name__ + ": " + str(exc)[:250])]
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                report["checks"].append(check("deadline", False, "checks pass within timeout", args.timeout))
                break
            time.sleep(min(1, remaining))
    except Exception as exc:
        report["checks"] = [check("inputs", False, "valid CLI and baseline", str(exc)[:250])]
    data["finished_at"] = time.time()
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("snapshot", "verify"), required=True)
    parser.add_argument("--prometheus-url", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--baseline")
    parser.add_argument("--min-increase", type=float, default=20)
    parser.add_argument("--timeout", type=float, default=90)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    report = run(args)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
    print(json.dumps({"status": report["status"], "output": str(output)}))
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":
    sys.exit(main())
