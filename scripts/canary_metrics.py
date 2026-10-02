"""Raw, fresh Traefik backend counters for the canary decision.

The leaf service name comes from Traefik v3.6.2's Gateway provider,
pkg/provider/kubernetes/gateway/httproute.go (namespace-name-http-port).
Never sum a route WRR counter together with its leaf backend counter.
"""
import importlib.util
from pathlib import Path
import time


_spec = importlib.util.spec_from_file_location("_mvp_metrics", Path(__file__).with_name("check-metrics.py"))
_mvp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mvp)

API = _mvp.API
TARGET = 'namespace="edge",service="traefik-metrics",endpoint="metrics"'
BACKEND = "demo-hello-canary-http-80@kubernetesgateway"


class Inconclusive(RuntimeError):
    """The available samples cannot prove the candidate is healthy."""


def public_series(series):
    """Evidence deliberately omits instance/pod/IP and arbitrary labels."""
    keep = {"__name__", "namespace", "service", "endpoint", "exported_service", "code", "method", "protocol"}
    return [{"metric": {k: v for k, v in row["metric"].items() if k in keep},
             "samples": row["samples"]} for row in series.values()]


def selector(service=BACKEND):
    if service != BACKEND:
        raise ValueError("candidate_service must identify the pinned canary leaf service exactly")
    return 'traefik_service_requests_total{' + TARGET + ',exported_service="' + service + '",method="GET"}'


def snapshot(api, *, service=BACKEND, after=0, max_age=45, window=240, now=None):
    now = time.time() if now is None else now
    try:
        targets = api.get("targets", state="active").get("activeTargets", [])
        chosen = [t for t in targets if _mvp.target_matches(t.get("labels", {}),
                                                          ("edge", "traefik-metrics", "metrics"))]
        if len(chosen) != 1:
            raise Inconclusive("expected exactly one Traefik scrape target")
        target = chosen[0]
        identity = _mvp.target_endpoint_identity(target)
        if identity is None or sum(_mvp.target_endpoint_identity(t) == identity for t in targets) != 1:
            raise Inconclusive("Traefik has missing identity or duplicate scrapes")
        if target.get("health") != "up" or target.get("lastError"):
            raise Inconclusive("Traefik scrape target is unhealthy")
        query, counters = api.matrix(selector(service), window, now)
        _, process = api.matrix('process_start_time_seconds{' + TARGET + '}', window, now)
        _, up = api.matrix('up{' + TARGET + '}', window, now)
        if not counters or len(process) != 1 or len(up) != 1:
            raise Inconclusive("missing backend counter, process identity, or up samples")
        rows = list(counters.values()) + list(process.values()) + list(up.values())
        labels = target.get("labels", {})
        for row in rows:
            if not -5 <= now - row["timestamp"] <= max_age or row["timestamp"] <= after:
                raise Inconclusive("stale samples or no scrape after the required boundary")
            for key in ("namespace", "service", "endpoint", "job", "instance", "pod"):
                if labels.get(key) != row["metric"].get(key):
                    raise Inconclusive("sample belongs to a different scrape target")
        for row in counters.values():
            if (row["metric"].get("exported_service") != service or row["metric"].get("method") != "GET"
                    or not row["metric"].get("code", "").isdigit()):
                raise Inconclusive("counter does not identify the requested backend")
        if next(iter(up.values()))["value"] != 1:
            raise Inconclusive("Traefik up sample is not one")
        return {"captured_at": now, "query": query, "series": counters, "process": process, "up": up}
    except Inconclusive:
        raise
    except Exception as error:
        # Network errors may contain kubeconfig paths or real addresses.
        raise Inconclusive("Prometheus query failed (" + type(error).__name__ + ")") from error


def public_snapshot(value):
    return {"captured_at": value["captured_at"], "query": value["query"],
            "series": public_series(value["series"]), "process": public_series(value["process"]),
            "up": public_series(value["up"])}


def compare(baseline, current, *, minimum=100, max_error_rate=0.01):
    """Count new code series only when the original exporter did not restart."""
    before, after = baseline["series"], current["series"]
    for name in ("process", "up"):
        old, new = baseline[name], current[name]
        if set(old) != set(new):
            raise Inconclusive("scrape identity changed during observation")
        for key, row in new.items():
            if row["timestamp"] <= old[key]["timestamp"]:
                raise Inconclusive("no new infrastructure scrape")
            points = [p for p in row["samples"] if p[0] >= old[key]["timestamp"]]
            if not points or any(p[1] != old[key]["value"] for p in points):
                raise Inconclusive("exporter restarted or scrape was unavailable during observation")
    if not set(before).issubset(after):
        raise Inconclusive("baseline counter series disappeared")
    # A newly appearing status code starts from zero only for the same process
    # and the same complete backend/target identity (excluding the code label).
    family = {tuple((k, v) for k, v in key if k != "code") for key in before}
    boundary = max(row["timestamp"] for row in before.values())
    total, errors, deltas = 0.0, 0.0, []
    for key, row in after.items():
        if tuple((k, v) for k, v in key if k != "code") not in family:
            raise Inconclusive("unexpected backend counter identity")
        old = before.get(key)
        start = old["timestamp"] if old else boundary
        points = [p for p in row["samples"] if p[0] >= start]
        if not points or row["timestamp"] <= start:
            raise Inconclusive("no new backend scrape")
        if old and (points[0][0] != old["timestamp"] or points[0][1] != old["value"]):
            raise Inconclusive("raw baseline sample is missing from observation")
        if not old and points[0][0] <= boundary:
            raise Inconclusive("new code series has an ambiguous baseline")
        if any(b[1] < a[1] for a, b in zip(points, points[1:])):
            raise Inconclusive("backend counter reset during observation")
        delta = row["value"] - (old["value"] if old else 0)
        if delta < 0:
            raise Inconclusive("backend counter decreased")
        code = row["metric"]["code"]
        total += delta
        errors += delta if 500 <= int(code) < 600 else 0
        deltas.append({"code": code, "increase": delta})
    evidence = {"requests": total, "errors_5xx": errors,
                "error_rate": errors / total if total else None,
                "min_requests": minimum, "max_error_rate": max_error_rate, "deltas": deltas}
    if total < minimum:
        error = Inconclusive("insufficient canary metric sample: " + str(total) + " < " + str(minimum))
        error.evidence = evidence
        raise error
    evidence["status"] = "pass" if evidence["error_rate"] <= max_error_rate else "fail"
    return evidence
