#!/usr/bin/env python3
"""Bounded, fail-closed Gateway API and external HTTP checks (stdlib only)."""

import argparse
import hashlib
import http.client
import ipaddress
import json
import math
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request


ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z")
CONTROLLER = "traefik.io/gateway-controller"
HELLO = b"Hello World!"


def check(name, ok, expected, actual):
    return {"name": name, "status": "pass" if ok else "fail",
            "expected": expected, "actual": actual}


def usable_ipv6(value):
    address = ipaddress.IPv6Address(value)
    if (address.ipv4_mapped is not None or address.scope_id is not None
            or address.is_unspecified or address.is_loopback or address.is_multicast
            or address.is_link_local):
        raise ValueError("Gateway address must be an unscoped, usable native IPv6 unicast address")
    return address


def conditions_checks(resources, expected_address=None):
    """Evaluate a single observed snapshot; absent/stale/False is never success."""
    checks = []

    def condition_checks(prefix, conditions, generation, required):
        for kind in required:
            matches = [c for c in conditions if c.get("type") == kind]
            condition = matches[0] if len(matches) == 1 else {}
            ok = (isinstance(generation, int) and generation > 0
                  and condition.get("status") == "True"
                  and condition.get("observedGeneration") == generation)
            actual = {key: condition.get(key) for key in
                      ("status", "observedGeneration", "reason")}
            checks.append(check(f"{prefix}.{kind}", ok,
                                {"status": "True", "observedGeneration": generation},
                                actual))

    gc = resources["gatewayclass"]
    gw = resources["gateway"]
    route = resources["httproute"]
    for key, resource, name in (("gatewayclass", gc, "mts-traefik"),
                                ("gateway", gw, "gateway"),
                                ("httproute", route, "hello")):
        metadata = resource.get("metadata", {})
        ok = metadata.get("name") == name
        if key != "gatewayclass":
            ok = ok and metadata.get("namespace") == "demo"
        checks.append(check(f"{key}.identity", ok,
                            {"name": name, "namespace": None if key == "gatewayclass" else "demo"},
                            {"name": metadata.get("name"), "namespace": metadata.get("namespace")}))
    checks.append(check("gatewayclass.controller", gc.get("spec", {}).get("controllerName") == CONTROLLER,
                        CONTROLLER, gc.get("spec", {}).get("controllerName")))
    condition_checks("gatewayclass", gc.get("status", {}).get("conditions", []),
                     gc.get("metadata", {}).get("generation"), ["Accepted"])
    checks.append(check("gateway.class", gw.get("spec", {}).get("gatewayClassName") == "mts-traefik",
                        "mts-traefik", gw.get("spec", {}).get("gatewayClassName")))
    generation = gw.get("metadata", {}).get("generation")
    condition_checks("gateway", gw.get("status", {}).get("conditions", []), generation,
                     ["Accepted", "Programmed"])
    addresses = gw.get("status", {}).get("addresses", [])
    expected = usable_ipv6(expected_address) if expected_address is not None else None
    valid_addresses = []
    for address in addresses:
        try:
            value = usable_ipv6(address.get("value", ""))
            valid_addresses.append(address.get("type") == "IPAddress"
                                   and (expected is None or value == expected))
        except ValueError:
            valid_addresses.append(False)
    # Do not store node addresses in the evidence report.
    checks.append(check("gateway.address", bool(valid_addresses) and all(valid_addresses),
                        "usable native IPv6 status address matching the configured node" if expected is not None
                        else "at least one usable native IPv6 status address",
                        {"count": len(addresses), "valid": valid_addresses,
                         "expected_address_checked": expected is not None}))
    listeners = [item for item in gw.get("status", {}).get("listeners", []) if item.get("name") == "web"]
    listener = listeners[0] if len(listeners) == 1 else {}
    condition_checks("gateway.listener.web", listener.get("conditions", []), generation,
                     ["Accepted", "Programmed", "ResolvedRefs"])
    checks.append(check("gateway.listener.routes", listener.get("attachedRoutes", 0) >= 1,
                        ">=1", listener.get("attachedRoutes")))
    parents = []
    for parent in route.get("status", {}).get("parents", []):
        ref = parent.get("parentRef", {})
        if (parent.get("controllerName") == CONTROLLER and ref.get("name") == "gateway"
                and ref.get("namespace", "demo") == "demo" and ref.get("sectionName") == "web"
                and ref.get("group", "gateway.networking.k8s.io") == "gateway.networking.k8s.io"
                and ref.get("kind", "Gateway") == "Gateway"):
            parents.append(parent)
    parent = parents[0] if len(parents) == 1 else {}
    checks.append(check("httproute.parent", len(parents) == 1,
                        "one parent demo/gateway/web owned by Traefik", {"matching_parents": len(parents)}))
    condition_checks("httproute", parent.get("conditions", []),
                     route.get("metadata", {}).get("generation"), ["Accepted", "ResolvedRefs"])
    return checks


def remaining(deadline):
    value = deadline - time.monotonic()
    if value <= 0:
        raise TimeoutError("overall deadline exceeded")
    return value


def wait_conditions(args, deadline):
    # kubectl uses Go's proxy environment independently of urllib's ProxyHandler.
    # Keep standalone checks on direct API transport for either IP family.
    kubectl_env = {key: value for key, value in os.environ.items()
                   if key.lower() not in {"http_proxy", "https_proxy", "all_proxy", "no_proxy"}}
    last_checks = []
    attempts = 0
    while True:
        attempts += 1
        try:
            resources = {}
            for kind, name in (("gatewayclass", "mts-traefik"), ("gateway", "gateway"), ("httproute", "hello")):
                timeout = min(10.0, remaining(deadline))
                command = [args.kubectl, "--kubeconfig", str(Path(args.kubeconfig).resolve()),
                           f"--request-timeout={timeout:.3f}s", "get", f"{kind}.gateway.networking.k8s.io", name,
                           "-o", "json"]
                if kind != "gatewayclass":
                    command.extend(["--namespace", "demo"])
                # Even with a KUBECONFIG in the calling environment, always pass the explicit file.
                result = subprocess.run(command, capture_output=True, text=True, timeout=timeout, check=False,
                                        env=kubectl_env)
                if result.returncode:
                    raise RuntimeError(f"kubectl get {kind} failed (exit {result.returncode})")
                resources[kind] = json.loads(result.stdout)
            last_checks = conditions_checks(resources, args.expected_address)
            if all(item["status"] == "pass" for item in last_checks):
                return last_checks, attempts
        except (OSError, ValueError, RuntimeError, TimeoutError, subprocess.TimeoutExpired,
                TypeError, KeyError, AttributeError) as exc:
            last_checks = [check("gateway.conditions.read", False,
                                 "read and evaluate all Gateway API objects", type(exc).__name__)]
        if deadline - time.monotonic() <= 0:
            return last_checks, attempts
        time.sleep(min(2.0, deadline - time.monotonic()))


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def request(base_url, path, request_id, deadline):
    headers = {} if request_id is None else {"X-Request-ID": request_id}
    req = urllib.request.Request(base_url.rstrip("/") + path, headers=headers, method="GET")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    try:
        response = opener.open(req, timeout=min(10.0, remaining(deadline)))
    except urllib.error.HTTPError as exc:
        response = exc
    with response:
        body = response.read(65537)
        if len(body) > 65536:
            raise ValueError("response exceeds 64 KiB")
        ids = response.headers.get_all("X-Request-ID", [])
        return {"method": "GET", "uri": path, "status": response.status,
                "body": body.decode("utf-8", errors="replace"), "bytes": len(body),
                "request_id": ids[0] if len(ids) == 1 else None,
                "request_id_header_count": len(ids), "body_exact": body == HELLO}


def derived_id(seed, suffix):
    return hashlib.sha256(f"{seed}:{suffix}".encode()).hexdigest()[:32]


def traffic_checks(args, deadline):
    checks = []
    data = {"request_id": args.request_id, "error_marker": args.error_marker,
            "request_count": 0, "requested_load_count": args.requests}

    def probe(name, path, request_id, expected_status, exact_body=False, fallback=False):
        try:
            result = request(args.base_url, path, request_id, deadline)
            if result["status"] == 200:
                data["request_count"] += 1
            returned_id = result["request_id"]
            id_ok = (returned_id is not None and ID_PATTERN.fullmatch(returned_id) is not None
                     and (returned_id != request_id if fallback else returned_id == request_id))
            ok = (result["status"] == expected_status and (not exact_body or result["body_exact"])
                  and (expected_status != 404 or not result["body_exact"])
                  and id_ok)
            checks.append(check(name, ok,
                                {"status": expected_status, "body": "Hello World!" if exact_body else "not Hello World!",
                                 "request_id": "generated valid fallback" if fallback else request_id}, result))
            return result
        except (OSError, ValueError, TimeoutError, urllib.error.URLError, http.client.HTTPException) as exc:
            checks.append(check(name, False, f"HTTP {expected_status}", {"error": type(exc).__name__}))
            return {"method": "GET", "uri": path, "error": type(exc).__name__}

    data["http"] = probe("http.primary", "/", args.request_id, 200, exact_body=True)
    # Exactly --requests load requests, including the primary access-log marker.
    for index in range(1, args.requests):
        probe(f"http.load.{index}", "/", derived_id(args.request_id, index), 200, exact_body=True)
    probe("http.verify", "/verify", derived_id(args.request_id, "verify"), 200, exact_body=True)
    probe("http.request_id.absent", "/", None, 200, exact_body=True, fallback=True)
    probe("http.request_id.invalid", "/", "invalid id!", 200, exact_body=True, fallback=True)
    probe("http.request_id.overlong", "/", "x" * 65, 200, exact_body=True, fallback=True)
    data["error_http"] = probe("http.error", f"/verify/missing/{args.error_marker}",
                               derived_id(args.request_id, "error"), 404)
    try:
        result = request(args.base_url, "/unrouted", derived_id(args.request_id, "unrouted"), deadline)
        checks.append(check("http.unrouted", result["status"] == 404 and not result["body_exact"],
                            {"status": 404, "body": "not Hello World!"}, result))
        data["unrouted_http"] = result
    except (OSError, ValueError, TimeoutError, urllib.error.URLError, http.client.HTTPException) as exc:
        checks.append(check("http.unrouted", False, "HTTP 404", {"error": type(exc).__name__}))
    return checks, data


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise ValueError(message)


def parse_args(argv):
    parser = Parser(description=__doc__)
    parser.add_argument("--mode", choices=("conditions", "traffic", "all"), default="all")
    parser.add_argument("--kubeconfig", required=True)
    parser.add_argument("--kubectl", default="kubectl")
    parser.add_argument("--base-url")
    parser.add_argument("--expected-address", help="Require Gateway status to equal this primary IPv6 node address")
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--request-id")
    parser.add_argument("--error-marker")
    parser.add_argument("--requests", type=int, default=20)
    parser.add_argument("--timeout", type=float, default=120)
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    if args.expected_address is not None:
        args.expected_address = str(usable_ipv6(args.expected_address))
    if not math.isfinite(args.timeout) or args.timeout <= 0 or args.timeout > 3600:
        raise ValueError("--timeout must be finite and in (0, 3600]")
    if not 1 <= args.requests <= 10000:
        raise ValueError("--requests must be in [1, 10000]")
    if not ID_PATTERN.fullmatch(args.run_id):
        raise ValueError("--run-id must use the safe Request-ID format")
    args.request_id = args.request_id or derived_id(args.run_id, "access")
    args.error_marker = args.error_marker or derived_id(args.run_id, "missing")
    for name in ("request_id", "error_marker"):
        if not ID_PATTERN.fullmatch(getattr(args, name)):
            raise ValueError(f"--{name.replace('_', '-')} must use the safe Request-ID format")
    if not Path(args.kubeconfig).is_file():
        raise ValueError("--kubeconfig must name an existing explicit file")
    if args.mode in ("traffic", "all"):
        url = urllib.parse.urlsplit(args.base_url or "")
        if (url.scheme != "http" or not url.hostname or url.username or url.password
                or url.path not in ("", "/") or url.query or url.fragment):
            raise ValueError("--base-url must be an HTTP origin without credentials, path, query or fragment")
        # Accessing port also validates malformed/out-of-range values before requests start.
        _ = url.port
        address = ipaddress.ip_address(url.hostname)
        if address.version == 6 and (address.ipv4_mapped is not None or address.scope_id is not None):
            raise ValueError("--base-url must use a native IPv4 or bracketed IPv6 literal")
    return args


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    report = {"status": "fail", "checks": [], "data": {}}
    output = None
    previous_handler = None
    # Recover the output destination even on argument-validation errors.
    for index, item in enumerate(argv):
        if item == "--output" and index + 1 < len(argv):
            output = argv[index + 1]
        elif item.startswith("--output="):
            output = item.partition("=")[2]
    try:
        args = parse_args(argv)
        report["data"]["run_id"] = args.run_id
        deadline = time.monotonic() + args.timeout
        # Socket timeouts alone do not bound a peer that continuously trickles data.
        # Ubuntu's process timer bounds the entire live check, including reads.
        def timeout_handler(signum, frame):
            raise TimeoutError("overall deadline exceeded")
        previous_handler = signal.signal(signal.SIGALRM, timeout_handler)
        signal.setitimer(signal.ITIMER_REAL, args.timeout)
        if args.mode in ("conditions", "all"):
            checks, attempts = wait_conditions(args, deadline)
            report["checks"].extend(checks)
            report["data"]["condition_attempts"] = attempts
        if args.mode in ("traffic", "all"):
            checks, data = traffic_checks(args, deadline)
            report["checks"].extend(checks)
            report["data"].update(data)
    except Exception as exc:
        report["checks"].append(check("gateway.execution", False, "valid inputs and completed checks",
                                      {"error": type(exc).__name__, "reason": str(exc)}))
    finally:
        if previous_handler is not None:
            signal.setitimer(signal.ITIMER_REAL, 0)
            signal.signal(signal.SIGALRM, previous_handler)
    report["status"] = "pass" if report["checks"] and all(
        item["status"] == "pass" for item in report["checks"]) else "fail"
    rendered = json.dumps(report, indent=2, ensure_ascii=False) + "\n"
    if output:
        try:
            destination = Path(output)
            destination.parent.mkdir(parents=True, exist_ok=True)
            temporary = destination.with_name(destination.name + f".{os.getpid()}.tmp")
            temporary.write_text(rendered, encoding="utf-8")
            temporary.replace(destination)
        except OSError as exc:
            print(f"cannot write report: {type(exc).__name__}", file=sys.stderr)
            return 1
    else:
        print(rendered, end="")
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
