#!/usr/bin/env python3
"""Read-only FINAL-01 acceptance: current routes, dual-stack TLS and traffic split."""

import argparse
import collections
import hashlib
import http.client
import importlib.util
import io
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
import uuid

from stacklib import ROOT, environment, http_url, kubectl, write_json


SPEC = importlib.util.spec_from_file_location("mvp_gateway", ROOT / "scripts/check-gateway.py")
MVP = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MVP)
ROUTES = ("hello", "hello-host", "hello-stable", "hello-canary")
HOSTNAME = re.compile(r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]*[a-z0-9])?)+\Z")
check = MVP.check


def parent(section):
    return {"group": "gateway.networking.k8s.io", "kind": "Gateway",
            "name": "gateway", "sectionName": section}


def backend(name, weight):
    return {"group": "", "kind": "Service", "name": name, "port": 80, "weight": weight}


def current_conditions(prefix, source, generation, kinds):
    checks = []
    for kind in kinds:
        items = [item for item in source if item.get("type") == kind]
        item = items[0] if len(items) == 1 else {}
        ok = (type(generation) is int and generation > 0 and item.get("status") == "True"
              and item.get("observedGeneration") == generation)
        checks.append(check(f"{prefix}.{kind}", ok, "True at current generation",
                            {key: item.get(key) for key in ("status", "observedGeneration", "reason")}))
    return checks


def route_checks(resources, hostname, split=False, expected_address=None):
    """Validate exact managed route specs as well as current controller status."""
    routes = resources["routes"]
    result = MVP.conditions_checks({"gatewayclass": resources["gatewayclass"],
                                   "gateway": resources["gateway"], "httproute": routes.get("hello", {})},
                                  expected_address)
    gateway = resources["gateway"]
    generation = gateway.get("metadata", {}).get("generation")
    expected_listeners = [
        {"name": "web", "protocol": "HTTP", "port": 8000},
        {"name": "websecure", "protocol": "HTTPS", "port": 8443, "hostname": hostname,
         "tls": {"mode": "Terminate", "certificateRefs": [{"group": "", "kind": "Secret", "name": "hello-tls"}]}}]
    allowed = {"namespaces": {"from": "Same"},
               "kinds": [{"group": "gateway.networking.k8s.io", "kind": "HTTPRoute"}]}
    for listener in expected_listeners:
        listener["allowedRoutes"] = allowed
    actual = gateway.get("spec", {}).get("listeners", [])
    result.append(check("final.gateway.listeners", actual == expected_listeners, expected_listeners, actual))
    listeners = gateway.get("status", {}).get("listeners", [])
    for section in ("web", "websecure"):
        items = [item for item in listeners if item.get("name") == section]
        item = items[0] if len(items) == 1 else {}
        result += current_conditions(f"final.listener.{section}", item.get("conditions", []),
                                     generation, ("Accepted", "Programmed", "ResolvedRefs"))
        result.append(check(f"final.listener.{section}.routes", item.get("attachedRoutes") == len(ROUTES),
                            len(ROUTES), item.get("attachedRoutes")))

    for name in ROUTES:
        route = routes.get(name, {})
        metadata, spec = route.get("metadata", {}), route.get("spec", {})
        result.append(check(f"final.{name}.identity", metadata.get("name") == name
                            and metadata.get("namespace") == "demo", f"demo/{name}",
                            {key: metadata.get(key) for key in ("name", "namespace")}))
        result.append(check(f"final.{name}.parentRefs", spec.get("parentRefs") == [parent("web"), parent("websecure")],
                            [parent("web"), parent("websecure")], spec.get("parentRefs")))
        expected_hosts = [hostname] if name == "hello-host" else []
        result.append(check(f"final.{name}.hostnames", spec.get("hostnames", []) == expected_hosts,
                            expected_hosts, spec.get("hostnames", [])))
        if name in ("hello", "hello-host"):
            expected_rule = {
                "matches": [{"path": {"type": "Exact", "value": "/"}},
                            {"path": {"type": "PathPrefix", "value": "/verify"}}],
                "filters": [{"type": "ResponseHeaderModifier", "responseHeaderModifier": {
                    "set": [{"name": "X-Gateway-Route", "value": name}]}}],
                "backendRefs": [backend("hello", 90 if split else 100), backend("hello-canary", 10 if split else 0)]}
        else:
            track = name.removeprefix("hello-")
            expected_rule = {
                "matches": [{"path": {"type": "Exact", "value": "/backend/" + track}}],
                "filters": [{"type": "URLRewrite", "urlRewrite": {
                    "path": {"type": "ReplaceFullPath", "replaceFullPath": "/"}}}],
                "backendRefs": [backend("hello" if track == "stable" else "hello-canary", 100)]}
        # Kubernetes defaults omitted namespace to the local namespace. The chart
        # deliberately writes group/kind/weight explicitly, so equality is strict.
        result.append(check(f"final.{name}.rules", spec.get("rules") == [expected_rule],
                            [expected_rule], spec.get("rules")))
        for section in ("web", "websecure"):
            parents = [item for item in route.get("status", {}).get("parents", [])
                       if item.get("controllerName") == MVP.CONTROLLER
                       and item.get("parentRef", {}).get("name") == "gateway"
                       and item.get("parentRef", {}).get("namespace", "demo") == "demo"
                       and item.get("parentRef", {}).get("sectionName") == section
                       and item.get("parentRef", {}).get("group", "gateway.networking.k8s.io") == "gateway.networking.k8s.io"
                       and item.get("parentRef", {}).get("kind", "Gateway") == "Gateway"]
            status = parents[0] if len(parents) == 1 else {}
            result += current_conditions(f"final.{name}.{section}", status.get("conditions", []),
                                         metadata.get("generation"), ("Accepted", "ResolvedRefs"))
    return result


def snapshot(env, deadline):
    def read(kind, name=None, namespace=None):
        timeout = min(10, MVP.remaining(deadline))
        args = [f"--request-timeout={timeout:.3f}s", "get", kind]
        if name:
            args.append(name)
        if namespace:
            args += ["-n", namespace]
        value = kubectl(env, *args, "-o", "json", capture=True, timeout=timeout)
        return json.loads(value.stdout)
    return {"gatewayclass": read("gatewayclass", "mts-traefik"),
            "gateway": read("gateway", "gateway", "demo"),
            "routes": {item["metadata"]["name"]: item for item in read("httproutes", namespace="demo")["items"]}}


def wait_routes(env, deadline, split):
    until = min(deadline, time.monotonic() + 60)
    while True:
        result = route_checks(snapshot(env, until), env["tls_hostname"], split, env["node_ip"])
        if all(item["status"] == "pass" for item in result) or time.monotonic() >= until:
            return result
        time.sleep(min(1, max(0, until - time.monotonic())))


def curl_probe(env, family, path, request_id, deadline, *, hostname=None, tls=False, ca=None):
    """One connection and one attempt. No proxy, redirect, insecure flag or retries."""
    ip = env["node_ip"] if family == 6 else env["node_ipv4"]
    port = env["https_node_port"] if tls else env["node_port"]
    host = hostname or (env["tls_hostname"] if tls else None)
    origin = f'{"https" if tls else "http"}://{host}:{port}' if host else http_url(ip, port)
    timeout = min(10, MVP.remaining(deadline))
    with tempfile.TemporaryDirectory(prefix="mts-gateway-probe-") as directory:
        headers_file, body_file = Path(directory) / "headers", Path(directory) / "body"
        argv = ["curl", "--silent", "--show-error", "--noproxy", "*", "--http1.1",
                "--retry", "0", "--max-redirs", "0", "--connect-timeout", str(min(5, timeout)),
                "--max-time", str(timeout), "--max-filesize", "65536", "--ipv6" if family == 6 else "--ipv4",
                "--header", "X-Request-ID: " + request_id, "--dump-header", str(headers_file),
                "--output", str(body_file), "--write-out", "%{http_code}"]
        if host:
            argv += ["--resolve", f"{host}:{port}:{'[' + ip + ']' if family == 6 else ip}"]
        if tls:
            argv += ["--cacert", str(ca)]
        process = subprocess.run([*argv, origin + path], capture_output=True, timeout=timeout + 1)
        if process.returncode:
            return {"curl_exit": process.returncode, "status": 0}
        raw = headers_file.read_bytes()
        if len(raw) > 65536:
            raise ValueError("HTTP headers exceed 64 KiB")
        # GET over HTTP/1.1 has one final response and no CONNECT proxy preamble.
        status_line, _, header_block = raw.partition(b"\r\n")
        headers = http.client.parse_headers(io.BytesIO(header_block))
        body = body_file.read_bytes()
        result = {"curl_exit": 0, "status": int(process.stdout), "body_exact": body == MVP.HELLO,
                  "bytes": len(body)}
        if not status_line.startswith(b"HTTP/1.1 "):
            raise ValueError("unexpected HTTP response protocol")
        for header, key in (("X-Release", "release"), ("X-Revision", "revision"),
                            ("X-Request-ID", "request_id"), ("X-Gateway-Route", "route")):
            values = headers.get_all(header, [])
            result[key] = values[0] if len(values) == 1 else None
        return result


def response_ok(response, request_id, expected_release=None, expected_route=None, revisions=None):
    release, revision = response.get("release"), response.get("revision")
    return (response.get("curl_exit") == 0 and response.get("status") == 200
            and response.get("body_exact") is True and response.get("request_id") == request_id
            and release in ("stable", "canary") and isinstance(revision, str)
            and MVP.ID_PATTERN.fullmatch(revision) is not None
            and (expected_release is None or release == expected_release)
            and (expected_route is None or response.get("route") == expected_route)
            and (revisions is None or revisions.get(release) == revision))


def distribution_check(counts, total, split):
    canary, stable = counts.get("canary", 0), counts.get("stable", 0)
    if split:
        ok = total >= 1000 and canary + stable == total and 0.05 <= canary / total <= 0.15
        expected = "100% valid responses; canary share 5–15% in >=1000 independent requests"
    else:
        ok = total >= 100 and stable == total and canary == 0
        expected = "100% valid stable responses and zero canary in >=100 requests"
    return check("distribution", ok, expected,
                 {"requested": total, "stable": stable, "canary": canary,
                  "canary_share": canary / total if total else None})


def traffic_checks(env, ca, args, deadline):
    checks, data = [], {"revisions": {}, "samples": {}}
    revisions = data["revisions"]

    def request(family, path, **kwargs):
        request_id = hashlib.sha256(f"{args.run_id}:{uuid.uuid4().hex}".encode()).hexdigest()[:32]
        response = curl_probe(env, family, path, request_id, deadline, ca=ca, **kwargs)
        return request_id, response

    for family in (6, 4):
        for tls in (False, True):
            scheme = "https" if tls else "http"
            for track in ("stable", "canary"):
                request_id, result = request(family, "/backend/" + track, tls=tls)
                ok = response_ok(result, request_id, expected_release=track)
                if track in revisions:
                    ok = ok and result.get("revision") == revisions[track]
                elif ok:
                    revisions[track] = result["revision"]
                checks.append(check(f"final.ipv{family}.{scheme}.backend.{track}", ok,
                                    f"healthy {track}, matching Request-ID and stable revision", result))
            for path in ("/", "/verify"):
                request_id, result = request(family, path, tls=tls, hostname=env["tls_hostname"])
                checks.append(check(f"final.ipv{family}.{scheme}.host.{path}",
                                    response_ok(result, request_id, expected_route="hello-host", revisions=revisions),
                                    "hostname route; exact body and release/revision headers", result))
            # The precise path matcher must not expose arbitrary suffixes.
            _, result = request(family, "/backend/canary/extra", tls=tls)
            checks.append(check(f"final.ipv{family}.{scheme}.path.negative",
                                result.get("curl_exit") == 0 and result.get("status") == 404,
                                "HTTP 404 for unmatched path", result))
        request_id, result = request(family, "/", hostname="unmatched.invalid")
        checks.append(check(f"final.ipv{family}.hostname.fallback",
                            response_ok(result, request_id, expected_route="hello", revisions=revisions),
                            "unknown hostname falls back to the preserved IP route", result))
        _, result = request(family, "/", tls=True, hostname="unmatched.invalid")
        checks.append(check(f"final.ipv{family}.tls.wrong_name", result.get("curl_exit") == 60,
                            "curl rejects certificate hostname (exit 60)", result))
        counts, errors = collections.Counter(), 0
        first_error = None
        for _ in range(args.requests):
            request_id, result = request(family, "/")
            if response_ok(result, request_id, expected_route="hello", revisions=revisions):
                counts[result["release"]] += 1
            else:
                errors += 1
                first_error = first_error or result
        outcome = distribution_check(counts, args.requests, args.split)
        outcome["name"] = f"final.ipv{family}.distribution"
        outcome["actual"].update(errors=errors, first_error=first_error)
        checks.append(outcome)
        data["samples"][f"ipv{family}"] = outcome["actual"]

    # Generate an unrelated CA only in a private temporary directory, and never
    # export the key. A missing CA file would test curl arguments, not TLS trust.
    with tempfile.TemporaryDirectory(prefix="mts-gateway-wrong-ca-") as directory:
        wrong_ca = Path(directory) / "ca.crt"
        subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
                        "-subj", "/CN=mts-unrelated-acceptance-ca", "-addext", "basicConstraints=critical,CA:TRUE",
                        "-keyout", str(Path(directory) / "ca.key"), "-out", str(wrong_ca)],
                       check=True, capture_output=True, timeout=min(15, MVP.remaining(deadline)))
        for family in (6, 4):
            result = curl_probe(env, family, "/", "wrong-ca-probe", deadline, tls=True, ca=wrong_ca)
            checks.append(check(f"final.ipv{family}.tls.wrong_ca", result.get("curl_exit") == 60,
                                "curl rejects unrelated CA (exit 60)", result))
    return checks, data


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env", default="ubuntu")
    parser.add_argument("--split", action="store_true", help="read-only validation of existing90/10; default100/0")
    parser.add_argument("--requests", type=int, default=1000, help="no-retry HTTP samples per IP family")
    parser.add_argument("--timeout", type=float, default=300)
    parser.add_argument("--run-id", default="gateway-final-" + uuid.uuid4().hex[:12])
    parser.add_argument("--output")
    args = parser.parse_args(argv)
    report = {"status": "fail", "checks": [], "data": {"run_id": args.run_id, "split": args.split}}
    try:
        if (not math.isfinite(args.timeout) or not 1 <= args.timeout <= 3600
                or not (1000 if args.split else 100) <= args.requests <= 10000
                or not MVP.ID_PATTERN.fullmatch(args.run_id)
                or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", args.env)):
            raise ValueError("invalid bounded timeout, sample count, environment or run id")
        deadline = time.monotonic() + args.timeout
        env = environment(args.env)
        env.setdefault("tls_hostname", "hello.test")
        env.setdefault("https_node_port", 30443)
        if not HOSTNAME.fullmatch(env["tls_hostname"]) or len(env["tls_hostname"]) > 253:
            raise ValueError("invalid TLS hostname")
        if not Path(env["kubeconfig"]).is_file():
            raise ValueError("environment kubeconfig does not exist")
        ca = ROOT / ".state/tls" / args.env / "ca.crt"
        if not ca.is_file():
            raise ValueError("environment CA certificate does not exist")
        report["checks"] += wait_routes(env, deadline, args.split)
        if all(item["status"] == "pass" for item in report["checks"]):
            checks, data = traffic_checks(env, ca, args, deadline)
            report["checks"] += checks
            report["data"].update(data)
            # Fail if routing changed during the sample window.
            after = route_checks(snapshot(env, deadline), env["tls_hostname"], args.split, env["node_ip"])
            for item in after:
                item["name"] = "after." + item["name"]
            report["checks"] += after
        else:
            report["checks"].append(check("final.traffic", False, "current accepted routes before HTTP", "not run: route checks failed"))
    except Exception as error:
        # Command strings/errors can contain local addresses or credentials.
        report["checks"].append(check("final.execution", False, "completed bounded acceptance",
                                      {"error": type(error).__name__,
                                       "reason": str(error) if isinstance(error, ValueError) else "operation failed or timed out"}))
    report["status"] = "pass" if report["checks"] and all(item["status"] == "pass" for item in report["checks"]) else "fail"
    if args.output:
        write_json(args.output, report)
    else:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":
    sys.exit(main())
