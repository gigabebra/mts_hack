#!/usr/bin/env python3
"""Sequential, opt-in live canary acceptance on the explicitly selected target.

Faults are injected after healthy smoke and observed 90/10 by this harness only.
The shared deployment lock covers every scenario and the final cleanup.
"""
import argparse
import datetime as dt
import hashlib
from pathlib import Path
import signal
import sys
import subprocess
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import canary
from stacklib import ROOT, deployment_lock, environment, write_json

SCENARIOS = ("healthy", "http500", "wrong-body", "wrong-body-preflight", "metrics-unavailable", "insufficient-sample")


def injector(scenario):
    def inject(ops, candidate, report):
        if ops.weights() != {"stable": 90, "canary": 10}:
            raise canary.Rejected("test cannot inject before actual 90/10")
        fault = {"scenario": scenario, "started_at": canary.timestamp(), "weights_before": ops.weights()}
        report["test_fault"] = fault
        if scenario in ("http500", "wrong-body"):
            candidate["faultMode"] = scenario
            ops.install(candidate)
            fault["ready"] = ops.ready(candidate)
            fault["kind"] = "Helm candidate configuration after healthy smoke"
        elif scenario == "metrics-unavailable":
            # Really remove the gate's local Prometheus transport. The shared
            # Prometheus workload and other consumers remain available.
            ops.stop_metrics()
            fault["kind"] = "Prometheus port-forward terminated; gate endpoint unavailable"
        elif scenario == "insufficient-sample":
            fault["kind"] = "primary traffic limited to 20 requests; minimum remains 100 canary requests"
        fault["finished_at"] = canary.timestamp()
        fault["status"] = "injected"
        return 20 if scenario == "insufficient-sample" else None
    return inject


def acceptance(scenario, result):
    expected = "accepted" if scenario == "healthy" else "inconclusive" if scenario in (
        "metrics-unavailable", "insufficient-sample") else "rejected"
    events = {event["name"] for event in result.get("events", [])}
    split = "traffic_90_10_confirmed" in events
    okay = result.get("decision") == expected and (result.get("status") == "pass") == (scenario == "healthy")
    if scenario == "wrong-body-preflight":
        requested = result.get("candidate_requested", {})
        samples = result.get("failure_http", {}).get("samples", [])
        observed = any(s.get("status") == 200 and s.get("release") == "canary"
                       and s.get("revision") == requested.get("releaseRevision")
                       and s.get("body_sha256") != hashlib.sha256(b"Hello World!").hexdigest() for s in samples)
        okay = okay and not split and observed
    else:
        okay = okay and split and "candidate_smoke_passed" in events
    if scenario not in ("healthy", "wrong-body-preflight"):
        fault = result.get("test_fault", {})
        okay = okay and fault.get("scenario") == scenario and fault.get("status") == "injected"
        observation = result.get("http_observation", {})
        if scenario in ("http500", "wrong-body"):
            requested = result.get("candidate_requested", {})
            invalid = observation.get("invalid_examples", [])
            observed = any(s.get("release") == "canary" and s.get("revision") == requested.get("releaseRevision")
                and s.get("request_id_matches") is True
                and (s.get("status") == 500 if scenario == "http500" else
                     s.get("status") == 200 and s.get("body_sha256") != hashlib.sha256(b"Hello World!").hexdigest())
                for s in invalid)
            okay = okay and observation.get("status") == "fail" and observed
        else:
            decision = result.get("metrics_decision", {})
            okay = okay and observation.get("status") == "pass" and decision.get("status") == "inconclusive"
            if scenario == "metrics-unavailable":
                okay = okay and decision.get("reason", "").startswith("Prometheus query failed (")
            else:
                okay = (okay and observation.get("requests") == 20 and decision.get("requests", 100) < 100
                        and decision.get("reason", "").startswith("insufficient canary metric sample:"))
    if scenario != "healthy":
        rollback = result.get("rollback", {})
        okay = (okay and rollback.get("status") == "pass" and rollback.get("weights_after") == {"stable": 100, "canary": 0}
                and rollback.get("stable_http", {}).get("passed", 0) >= 20)
    return {"name": scenario, "status": "pass" if okay else "fail", "expected_decision": expected,
            "actual_decision": result.get("decision"), "release_exit_code": 0 if result.get("status") == "pass" else 2,
            "traffic_enabled": split, "duration_seconds": result.get("duration_seconds"),
            "first_bad_at": result.get("http_observation", {}).get("first_bad_at"),
            "rollback_seconds": result.get("rollback", {}).get("duration_seconds"), "run_id": result["run_id"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env", required=True)
    parser.add_argument("--test-scenario", choices=("all", *SCENARIOS), default="all")
    parser.add_argument("--output")
    args = parser.parse_args()
    run_id = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ-") + uuid.uuid4().hex[:8]
    directory = ROOT / "artifacts/canary" / ("acceptance-" + run_id)
    directory.mkdir(parents=True, exist_ok=False)
    report = {"schema_version": 1, "run_id": run_id, "kind": "live_canary_acceptance", "status": "fail",
              "started_at": canary.timestamp(), "checks": [], "scope": "TLS routing, canary release and rollback",
              **canary.code_identity()}
    def interrupted(_signal, _frame):
        raise KeyboardInterrupt()
    previous = signal.signal(signal.SIGTERM, interrupted)
    try:
        import release_control
        env = environment(args.env)
        settings = canary.validate_settings(release_control.settings(env))
        scenarios = SCENARIOS if args.test_scenario == "all" else (args.test_scenario,)
        with deployment_lock(args.env):
            try:
                for index, scenario in enumerate(scenarios):
                    case_id = run_id + "-" + scenario
                    case_dir = directory / scenario
                    case_dir.mkdir()
                    ops = canary.Runtime(env, case_dir, settings)
                    result = canary.run_locked(ops, run_id=case_id, revision="acceptance-" + str(index) + "-" + uuid.uuid4().hex[:10],
                        fault_mode="wrong-body" if scenario == "wrong-body-preflight" else "none",
                        after_split=injector(scenario) if scenario not in ("healthy", "wrong-body-preflight") else None)
                    report["checks"].append(acceptance(scenario, result))
                    if scenario == "healthy" and result.get("decision") == "accepted":
                        # Read-only integration acceptance while the candidate
                        # still receives ten percent, before the next scenario.
                        verified = subprocess.run([sys.executable, str(ROOT / "scripts/verify-release.py"),
                                                   "--env", args.env], timeout=1500, check=False)
                        report["checks"][-1]["release_verification_exit_code"] = verified.returncode
                        if verified.returncode:
                            report["checks"][-1]["status"] = "fail"
                    write_json(directory / "report.json", report)
                    print(report["checks"][-1]["status"].upper(), scenario, result["decision"], flush=True)
                    if result.get("decision") in ("rollback_failed", "interrupted"):
                        break
            finally:
                cleanup_dir = directory / "cleanup"
                cleanup_dir.mkdir(exist_ok=True)
                cleanup = canary.run_locked(canary.Runtime(env, cleanup_dir, settings),
                    run_id=run_id + "-cleanup", revision="unused", action="restore")
                report["cleanup"] = {"status": cleanup["status"], "decision": cleanup["decision"],
                                     "run_id": cleanup["run_id"]}
                if cleanup["status"] == "pass":
                    verified = subprocess.run([sys.executable, str(ROOT / "scripts/verify-release.py"),
                                               "--env", args.env], timeout=1500, check=False)
                    report["cleanup"]["release_verification_exit_code"] = verified.returncode
                    if verified.returncode:
                        report["cleanup"]["status"] = "fail"
        if (len(report["checks"]) == len(scenarios) and all(c["status"] == "pass" for c in report["checks"])
                and report["cleanup"]["status"] == "pass"):
            report["status"] = "pass"
    except BaseException as error:
        report["reason"] = canary.safe_error(error)
    finally:
        signal.signal(signal.SIGTERM, previous)
        report["finished_at"] = canary.timestamp()
        write_json(directory / "report.json", report)
        if args.output:
            write_json(args.output, report)
    print(report["status"].upper(), str(directory / "report.json"))
    return 0 if report["status"] == "pass" else 2


if __name__ == "__main__":
    raise SystemExit(main())
