#!/usr/bin/env python3
"""Bounded canary release with HTTP/metrics gate and verified Helm recovery."""
import argparse
from collections import Counter
import contextlib
import copy
import datetime as dt
import hashlib
import importlib
import re
import signal
import subprocess
import time
import urllib.error
import urllib.request
import uuid

import canary_metrics as metrics
from stacklib import (ROOT, deployment_lock, environment, forward, http_url, internal_http,
                      locked_image, resource, write_json)


def timestamp():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def safe_error(error):
    if isinstance(error, (Rejected, metrics.Inconclusive)):
        return str(error)
    if isinstance(error, subprocess.CalledProcessError):
        return "external command failed with exit " + str(error.returncode)
    return type(error).__name__ + ": operation did not complete"


class Rejected(RuntimeError):
    def __init__(self, message, evidence=None):
        super().__init__(message)
        self.evidence = evidence


def normalized_weights(value):
    if not isinstance(value, dict):
        raise Rejected("actual route weights are malformed")
    result = {"stable": value.get("stable", value.get("stableWeight")),
              "canary": value.get("canary", value.get("canaryWeight"))}
    if any(type(v) is not int or v < 0 for v in result.values()) or sum(result.values()) != 100:
        raise Rejected("actual route weights are not a valid percentage pair")
    return result


def public_values(value):
    keep = ("fullnameOverride", "image", "imagePullPolicy", "replicas", "releaseTrack", "releaseRevision", "faultMode")
    result = {key: value[key] for key in keep if key in value}
    if "responseBody" in value:
        result["responseBody_sha256"] = hashlib.sha256(value["responseBody"].encode()).hexdigest()
    traffic = value.get("traffic", {})
    if traffic:
        result["traffic"] = {key: traffic[key] for key in ("stableWeight", "canaryWeight") if key in traffic}
    return result


def validate_settings(settings):
    result = {"window_seconds": 60, "min_canary_requests": 100, "max_error_rate": 0.01,
              "max_sample_age_seconds": 45, "request_rate": 40, "scrape_wait_seconds": 45,
              "operation_timeout_seconds": 420, "host": "hello.test", "candidate_service": metrics.BACKEND}
    result.update(settings)
    for key in ("window_seconds", "min_canary_requests", "request_rate", "scrape_wait_seconds",
                "max_sample_age_seconds", "operation_timeout_seconds"):
        if type(result[key]) not in (float, int) or not 0 < result[key] < 10000:
            raise ValueError("invalid canary setting: " + key)
    if not 60 <= result["window_seconds"] <= 120 or result["min_canary_requests"] < 100:
        raise ValueError("canary requires a 60..120 second window and at least 100 backend requests")
    if type(result["max_error_rate"]) not in (float, int) or not 0 <= result["max_error_rate"] <= .01:
        raise ValueError("canary max_error_rate cannot exceed one percent")
    if result["host"] != "hello.test" or result["candidate_service"] != metrics.BACKEND:
        raise ValueError("canary target must match the contracted host and backend")
    return result


class Runtime:
    """All mutations use release_control's sole Helm ownership; caller holds lock."""
    def __init__(self, env, directory, settings):
        self.env, self.directory, self.settings = env, directory, settings
        self.control = importlib.import_module("release_control")
        self.http = internal_http()
        self._forward = contextlib.ExitStack()
        self.api = None

    def assert_target(self):
        return self.control.assert_target(self.env)

    def values(self, release):
        return self.control.helm_values(self.env, release)

    def info(self, release):
        data = self.control.helm_info(self.env, release)
        return {key: data[key] for key in ("revision", "status", "chart", "app_version") if key in data}

    def defaults(self):
        return self.control.candidate_defaults(self.env)

    def weights(self):
        return normalized_weights(self.control.current_weights(self.env))

    def set_weights(self, stable, canary):
        self.control.set_weights(self.env, stable, canary)
        self.control.wait_routes(self.env)
        actual = self.weights()
        if actual != {"stable": stable, "canary": canary}:
            raise Rejected("observed route weights differ from the requested split")
        return actual

    def install(self, values):
        return self.control.install_candidate(self.env, values)

    def ready(self, values):
        deployment = resource(self.env, "deployment", "hello-canary", "-n", "demo")
        status, spec = deployment.get("status", {}), deployment["spec"]
        replicas = values.get("replicas", 1)
        if (status.get("observedGeneration", 0) < deployment["metadata"]["generation"]
                or spec.get("replicas") != replicas or status.get("updatedReplicas", 0) != replicas
                or status.get("readyReplicas", 0) != replicas or status.get("availableReplicas", 0) != replicas):
            raise Rejected("requested candidate rollout is not fully ready")
        containers = spec["template"]["spec"]["containers"]
        if len(containers) != 1 or containers[0].get("image") != locked_image(values["image"]):
            raise Rejected("candidate Deployment does not use the requested locked image")
        actual = self.values("app-canary")
        for key in ("image", "releaseRevision", "releaseTrack", "faultMode", "responseBody", "fullnameOverride"):
            if actual.get(key) != values.get(key):
                raise Rejected("candidate release values differ from the requested revision")
        return {"ready_replicas": replicas, "generation": deployment["metadata"]["generation"],
                "image": locked_image(values["image"]), "revision": values["releaseRevision"]}

    def request(self, path, request_id):
        if len(request_id) > 64:
            request_id = request_id[:45] + "-" + hashlib.sha256(request_id.encode()).hexdigest()[:16]
        request = urllib.request.Request(http_url(self.env["node_ip"], self.env["node_port"]) + path,
                                         headers={"Host": self.settings["host"], "X-Request-ID": request_id,
                                                  "Connection": "close"})
        try:
            try:
                response = self.http.open(request, timeout=3)
            except urllib.error.HTTPError as error:
                response = error
            with response:
                body = response.read(4097)
                roles, revisions = response.headers.get_all("X-Release", []), response.headers.get_all("X-Revision", [])
                role = roles[0] if len(roles) == 1 else "invalid"
                revision = revisions[0] if len(revisions) == 1 else "invalid"
                return {"status": response.status, "body_sha256": hashlib.sha256(body).hexdigest(),
                        "body_length": len(body), "release": role if role in ("stable", "canary") else "invalid",
                        "revision": revision if re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", revision) else "invalid",
                        "request_id_matches": response.headers.get_all("X-Request-ID", []) == [request_id]}
        except Exception as error:
            return {"status": 0, "release": "unavailable", "error": type(error).__name__}

    def start_metrics(self, deadline):
        url = self._forward.enter_context(forward(self.env, "monitoring", "monitoring-prometheus", 9090,
                                                 self.directory))
        self.api = metrics.API(url, deadline)

    def stop_metrics(self):
        self._forward.close()

    def snapshot(self, after, deadline):
        until = min(deadline, time.monotonic() + self.settings["scrape_wait_seconds"])
        last = metrics.Inconclusive("no fresh metrics sample")
        while time.monotonic() < until:
            try:
                return metrics.snapshot(self.api, service=self.settings["candidate_service"], after=after,
                                        max_age=self.settings["max_sample_age_seconds"])
            except metrics.Inconclusive as error:
                last = error
                time.sleep(min(1, max(0, until - time.monotonic())))
        raise last


def expected_response(result, values, role):
    body = values.get("responseBody", "Hello World!").encode()
    return (result.get("status") == 200 and result.get("body_length") == len(body)
            and result.get("body_sha256") == hashlib.sha256(body).hexdigest()
            and result.get("release") == role and result.get("revision") == values["releaseRevision"]
            and result.get("request_id_matches") is True)


def smoke(ops, values, role, run_id, count=5, path=None):
    path = path or "/backend/" + role
    samples = [ops.request(path, run_id + "-" + str(index)) for index in range(count)]
    result = {"path": path, "requests": count, "passed": sum(expected_response(s, values, role) for s in samples),
              "samples": samples}
    if result["passed"] != count:
        raise Rejected("dedicated " + role + " smoke failed to serve the exact requested release", result)
    return result


def restore(ops, stable, report, run_id):
    started = time.monotonic()
    rollback = {"status": "fail", "started_at": timestamp()}
    report["rollback"] = rollback
    try:
        rollback["weights"] = ops.set_weights(100, 0)
        rollback["stable_http"] = smoke(ops, stable, "stable", run_id + "-rollback", 25, "/")
        healthy = ops.defaults()
        ops.install(healthy)
        rollback["candidate_ready"] = ops.ready(healthy)
        rollback["candidate_http"] = smoke(ops, healthy, "canary", run_id + "-restored")
        rollback["restored_values"] = public_values(healthy)
        rollback["candidate_helm"] = ops.info("app-canary")
        rollback["weights_after"] = ops.weights()
        if rollback["weights_after"] != {"stable": 100, "canary": 0}:
            raise Rejected("weights changed during recovery")
        rollback["status"] = "pass"
    except BaseException as error:
        rollback["reason"] = safe_error(error)
        with contextlib.suppress(Exception):
            rollback["weights_after"] = ops.weights()
        rollback["recovery_command"] = "python3 scripts/canary.py --env <environment> --action restore"
    finally:
        rollback["finished_at"] = timestamp()
        rollback["duration_seconds"] = round(time.monotonic() - started, 3)
    return rollback["status"] == "pass"


def traffic(ops, stable, candidate, report, run_id, deadline, request_budget=None):
    started, wall = time.monotonic(), time.time()
    until = min(deadline, started + ops.settings["window_seconds"])
    counts, bad, total, first_bad = Counter(), [], 0, None
    rate = ops.settings["request_rate"]
    while time.monotonic() < until:
        if request_budget is not None and total >= request_budget:
            time.sleep(min(.25, max(0, until - time.monotonic())))
            continue
        sample = ops.request("/", run_id + "-load-" + str(total))
        total += 1
        role = sample.get("release", "unavailable")
        counts[role] += 1
        valid = role in ("stable", "canary") and expected_response(sample, stable if role == "stable" else candidate, role)
        if not valid:
            first_bad = first_bad or timestamp()
            if len(bad) < 12:
                bad.append(sample)
        due = started + total / rate
        if due > time.monotonic():
            time.sleep(min(due - time.monotonic(), max(0, until - time.monotonic())))
    result = {"started_at_unix": wall, "finished_at_unix": time.time(), "duration_seconds": time.monotonic() - started,
              "target_requests_per_second": rate, "requests": total, "releases": dict(counts),
              "invalid_examples": bad, "first_bad_at": first_bad, "status": "fail" if bad else "pass"}
    report["http_observation"] = result
    if result["duration_seconds"] + .05 < ops.settings["window_seconds"]:
        raise Rejected("operation deadline interrupted the observation window")
    return result


def run_locked(ops, *, run_id, revision, image=None, fault_mode="none", action="deploy", after_split=None):
    """Shared engine; tests inject post-split faults here under the same lock.

    after_split exists only as an in-process test seam. The production CLI has
    no way to inject missing metrics, reduce the sample threshold, or skip gates.
    """
    report = {"schema_version": 1, "run_id": run_id, "started_at": timestamp(), "status": "fail",
              "decision": "rejected", "action": action, "thresholds": dict(ops.settings), "events": []}
    path = ops.directory / "report.json"
    started = time.monotonic()
    deadline = started + ops.settings["operation_timeout_seconds"]
    stable, touched, accepted = None, False, False
    old_alarm = signal.getsignal(signal.SIGALRM)
    def operation_expired(_signal, _frame):
        raise Rejected("whole canary operation exceeded its deadline")
    signal.signal(signal.SIGALRM, operation_expired)
    previous_timer = signal.setitimer(signal.ITIMER_REAL, ops.settings["operation_timeout_seconds"])

    def event(name):
        report["events"].append({"name": name, "at": timestamp()})
        write_json(path, report)

    try:
        ops.assert_target()
        stable = ops.values("app")
        if not stable.get("releaseRevision"):
            raise Rejected("stable release has no explicit revision identity")
        report["previous"] = {release: {"values": public_values(ops.values(release)), "helm": ops.info(release)}
                              for release in ("app", "app-canary", "gateway")}
        report["weights_before"] = ops.weights()
        # A killed predecessor may have left 90/10 or a rejected candidate.
        # Always read actual resources and restore stable before a new rollout.
        touched = True
        report["initial_weights"] = ops.set_weights(100, 0)
        event("stable_only_confirmed")
        report["stable_smoke"] = smoke(ops, stable, "stable", run_id + "-stable")
        if action == "restore":
            if not restore(ops, stable, report, run_id):
                report["decision"] = "rollback_failed"
                return report
            accepted = True
            report.update(status="pass", decision="restored")
            return report
        candidate = ops.defaults()
        candidate.update(releaseRevision=revision, faultMode=fault_mode)
        if image:
            locked_image(image)
            candidate["image"] = image
        report["candidate_requested"] = public_values(candidate)
        ops.install(candidate)
        report["candidate_helm"] = ops.info("app-canary")
        report["candidate_ready"] = ops.ready(candidate)
        report["candidate_smoke"] = smoke(ops, candidate, "canary", run_id + "-candidate")
        event("candidate_smoke_passed")
        # Baseline is captured after the direct backend smoke has been scraped.
        # Subsequent observation creates requests only through the main route.
        smoke_finished = time.time()
        ops.start_metrics(deadline)
        baseline = ops.snapshot(smoke_finished, deadline)
        report["metrics_baseline"] = metrics.public_snapshot(baseline)
        report["split_weights"] = ops.set_weights(90, 10)
        event("traffic_90_10_confirmed")
        budget = after_split(ops, copy.deepcopy(candidate), report) if after_split else None
        observed = traffic(ops, stable, candidate, report, run_id, deadline, request_budget=budget)
        metric_problem = None
        try:
            current = ops.snapshot(observed["finished_at_unix"], deadline)
            report["metrics_current"] = metrics.public_snapshot(current)
            report["metrics_decision"] = metrics.compare(baseline, current,
                minimum=ops.settings["min_canary_requests"], max_error_rate=ops.settings["max_error_rate"])
        except metrics.Inconclusive as error:
            metric_problem = error
            report["metrics_decision"] = dict(getattr(error, "evidence", {}), status="inconclusive", reason=str(error))
        # Known HTTP faults remain rejections even when metrics are unavailable.
        if observed["status"] != "pass":
            raise Rejected("main route returned an incorrect status, body, or release identity")
        if metric_problem:
            raise metric_problem
        if observed["releases"].get("canary", 0) < ops.settings["min_canary_requests"]:
            raise metrics.Inconclusive("insufficient HTTP sample from the exact canary revision")
        if report["metrics_decision"]["status"] != "pass":
            raise Rejected("canary 5xx error rate exceeds the allowed threshold")
        report["candidate_ready_after"] = ops.ready(candidate)
        report["weights_after"] = ops.weights()
        if report["weights_after"] != {"stable": 90, "canary": 10}:
            raise Rejected("route weights changed during observation")
        accepted = True
        report.update(status="pass", decision="accepted")
        event("accepted")
    except BaseException as error:
        report["reason"] = safe_error(error)
        if getattr(error, "evidence", None) is not None:
            report["failure_http"] = error.evidence
        report["decision"] = "inconclusive" if isinstance(error, metrics.Inconclusive) else "interrupted" if isinstance(error, (KeyboardInterrupt, SystemExit)) else "rejected"
        event("decision_" + report["decision"])
    finally:
        # Recovery has independently bounded Helm/API operations and must still
        # run when the observation's whole-operation deadline has expired.
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, old_alarm)
        with contextlib.suppress(Exception):
            ops.stop_metrics()
        if touched and not accepted and stable is not None and "rollback" not in report:
            if not restore(ops, stable, report, run_id):
                report["original_decision"] = report["decision"]
                report["decision"] = "rollback_failed"
        report["finished_at"] = timestamp()
        report["duration_seconds"] = round(time.monotonic() - started, 3)
        write_json(path, report)
        if previous_timer[0]:
            signal.setitimer(signal.ITIMER_REAL, max(.001, previous_timer[0] - (time.monotonic() - started)), previous_timer[1])
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env", required=True)
    parser.add_argument("--action", choices=("deploy", "restore"), default="deploy")
    parser.add_argument("--image", help="candidate image already present in images.lock.yaml")
    parser.add_argument("--revision", help="explicit candidate identity, up to 64 safe characters")
    parser.add_argument("--fault-mode", choices=("none", "http500", "wrong-body"), default="none",
                        help="explicit demo candidate configuration; bad candidates fail the initial smoke")
    parser.add_argument("--run-id")
    parser.add_argument("--output", type=str, help="optional additional JSON report destination")
    args = parser.parse_args()
    run_id = args.run_id or dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ-") + uuid.uuid4().hex[:8]
    revision = args.revision or "candidate-" + uuid.uuid4().hex[:12]
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", revision) or not re.fullmatch(r"[A-Za-z0-9_-]{1,90}", run_id):
        parser.error("revision/run-id must be safe bounded identifiers")
    directory = ROOT / "artifacts/canary" / run_id
    directory.mkdir(parents=True, exist_ok=False)
    report = {"schema_version": 1, "run_id": run_id, "status": "fail", "decision": "rejected"}
    def interrupted(_signal, _frame):
        raise KeyboardInterrupt()
    old_handler = signal.signal(signal.SIGTERM, interrupted)
    try:
        control = importlib.import_module("release_control")
        env = environment(args.env)
        settings = validate_settings(control.settings(env))
        with deployment_lock(args.env):
            ops = Runtime(env, directory, settings)
            report = run_locked(ops, run_id=run_id, revision=revision, image=args.image,
                                fault_mode=args.fault_mode, action=args.action)
    except BaseException as error:
        report["reason"] = safe_error(error)
        write_json(directory / "report.json", report)
    finally:
        signal.signal(signal.SIGTERM, old_handler)
    if args.output:
        write_json(args.output, report)
    print(report["decision"].upper(), str(directory / "report.json"))
    return 0 if report["status"] == "pass" else 2


if __name__ == "__main__":
    raise SystemExit(main())
