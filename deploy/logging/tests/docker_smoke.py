#!/usr/bin/env python3
"""Isolated real Fluentd/Loki test with synthetic CRI fixtures, not live acceptance.

Requires Docker, PyYAML and --helm. Creates only uniquely named disposable
containers/network and temporary directories; never opens a kubeconfig.
"""

import argparse
import datetime
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import uuid

import yaml


ROOT = Path(__file__).resolve().parents[3]
FLUENT = "docker.io/grafana/fluent-plugin-loki@sha256:c87bd0e76339c2eef04a768af413e8637f0c673c36e7f996d07aba10e5a180f2"
LOKI = "docker.io/grafana/loki@sha256:fad94db64eeb0e14544953789c942453df68a860b6f760d867f06c5b31f461ab"


def command(*args, timeout=60, check=True):
    return subprocess.run(args, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                          timeout=timeout, check=check).stdout.strip()


def wait_ready(url):
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        try:
            with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(url + "/ready", timeout=2) as response:
                if response.status == 200:
                    return
        except (urllib.error.URLError, OSError):
            pass
        time.sleep(1)
    raise RuntimeError("Loki did not become ready within 90 seconds")


def fixture(path, marker):
    now = time.time_ns()
    date = datetime.datetime.fromtimestamp(now // 1_000_000_000, datetime.timezone.utc)
    cri = date.strftime("%Y-%m-%dT%H:%M:%S") + "." + f"{now % 1_000_000_000:09d}" + "Z"
    record = {"time": date.isoformat(), "request_id": marker, "method": "GET", "uri": "/",
              "status": 200, "bytes_sent": 12, "request_time": 0.001}
    error = (date.strftime("%Y/%m/%d %H:%M:%S") + ' [error] 1#1: *1 open() "/app/verify/missing/'
             + marker + '-err" failed (2: No such file or directory), client: 2001:db8::1, request: "GET /verify/missing/'
             + marker + '-err HTTP/1.1", host: "fixture"')
    with path.open("a") as target:
        target.write(cri + " stdout F " + json.dumps(record) + "\n")
        target.write(cri + " stderr F " + error + "\n")
    return str((now - 1_000_000_000) / 1_000_000_000)


def verify(url, marker, start, output):
    result = subprocess.run([sys.executable, str(ROOT / "scripts/check-logs.py"),
                             "--loki-url", url, "--base-url", "http://[::1]:1",
                             "--run-id", marker, "--request-id", marker, "--error-marker", marker + "-err",
                             "--start-time", start, "--timeout", "45", "--output", str(output)],
                            timeout=50, check=False)
    report = json.loads(output.read_text())
    if result.returncode or report["status"] != "pass":
        raise AssertionError(json.dumps(report))
    assert report["data"]["generated_probes"] is False


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--helm", required=True)
    args = parser.parse_args()
    suffix = uuid.uuid4().hex[:10]
    network, loki, fluent = ["mvp04-" + name + "-" + suffix for name in ("network", "loki", "fluent")]
    names = []
    temp = tempfile.TemporaryDirectory(prefix="mvp04-smoke-")
    directory = Path(temp.name)
    try:
        rendered = command(args.helm, "template", "logging", str(ROOT / "deploy/logging"), "--namespace", "logging")
        config = next(doc["data"] for doc in yaml.safe_load_all(rendered) if doc["kind"] == "ConfigMap")
        for name, text in config.items():
            (directory / name).write_text(text)
        for name in ("containers", "pods", "loki", "fluent"):
            (directory / name).mkdir(mode=0o777)
            (directory / name).chmod(0o777)
        podpath = directory / "pods/demo_hello-fixture_uid/nginx"
        podpath.mkdir(parents=True)
        logfile = podpath / "0.log"
        logfile.touch()
        (directory / "containers/hello-fixture_demo_nginx-1234.log").symlink_to(
            "/var/log/pods/demo_hello-fixture_uid/nginx/0.log")
        command("docker", "run", "--rm", "--network", "none", "--read-only", "--cap-drop", "ALL",
                "-v", str(directory / "loki.yaml") + ":/etc/loki/config.yaml:ro", LOKI,
                "-config.file=/etc/loki/config.yaml", "-verify-config=true")
        print("PASS actual Loki 3.6.2 config validation", flush=True)
        command("docker", "network", "create", "--ipv6", "--ipv4=false",
                "--subnet", f"fd7a:115c:{suffix[:4]}:{suffix[4:8]}::/64", network)
        names.append(loki)
        command("docker", "run", "-d", "--name", loki, "--network", network,
                "--network-alias", "loki.logging.svc.cluster.local", "--read-only", "--cap-drop", "ALL",
                "--user", "10001:10001", "--security-opt", "no-new-privileges", "--tmpfs", "/tmp",
                "-p", "[::1]::3100", "-v", str(directory / "loki.yaml") + ":/etc/loki/config.yaml:ro",
                "-v", str(directory / "loki") + ":/var/lib/loki", LOKI, "-config.file=/etc/loki/config.yaml")
        port = command("docker", "port", loki, "3100/tcp").split(":")[-1]
        url = "http://[::1]:" + port
        wait_ready(url)
        names.append(fluent)
        command("docker", "run", "-d", "--name", fluent, "--network", network,
                "--read-only", "--cap-drop", "ALL", "--user", "0:0", "--security-opt", "no-new-privileges",
                "--tmpfs", "/tmp", "-v", str(directory / "fluent.conf") + ":/fluentd/etc/fluent.conf:ro",
                "-v", str(directory / "containers") + ":/var/log/containers:ro",
                "-v", str(directory / "pods") + ":/var/log/pods:ro",
                "-v", str(directory / "fluent") + ":/var/lib/fluentd", "--entrypoint", "tini", FLUENT,
                "--", "fluentd", "--no-supervisor", "--suppress-config-dump", "-c", "/fluentd/etc/fluent.conf",
                "-p", "/fluentd/plugins")
        resolved = json.loads(command("docker", "exec", fluent, "ruby", "-rsocket", "-rjson", "-e",
                              'puts Addrinfo.getaddrinfo("loki.logging.svc.cluster.local", 3100, nil, :STREAM).map { |a| a.ip_address }.uniq.to_json'))
        import ipaddress
        assert resolved and all(ipaddress.ip_address(address).version == 6 for address in resolved), resolved
        print("PASS Fluentd service DNS resolves only IPv6 addresses", flush=True)
        marker = "fixture-" + suffix
        start = fixture(logfile, marker)
        verify(url, marker, start, directory / "first.json")
        print("PASS CRI symlink → Fluentd JSON/stdout + text/stderr → real Loki API; exact labels and nanoseconds", flush=True)
        before = command("docker", "exec", fluent, "cat", "/var/lib/fluentd/nginx.pos")
        command("docker", "stop", "-t", "5", loki)
        recovery = "recovery-" + suffix
        recovery_start = fixture(logfile, recovery)
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            after = command("docker", "exec", fluent, "cat", "/var/lib/fluentd/nginx.pos")
            if after != before:
                break
            time.sleep(1)
        else:
            raise AssertionError("Fluentd positions did not advance during Loki outage")
        command("docker", "restart", "-t", "5", fluent)
        command("docker", "start", loki)
        # Docker may allocate a different ephemeral published port at restart.
        port = command("docker", "port", loki, "3100/tcp").split(":")[-1]
        url = "http://[::1]:" + port
        wait_ready(url)
        verify(url, recovery, recovery_start, directory / "recovery.json")
        verify(url, marker, start, directory / "history.json")
        print("PASS buffered records survive collector restart during Loki outage; prior Loki records persist", flush=True)
    except Exception:
        for name in names:
            print(command("docker", "logs", "--tail", "50", name, check=False), file=sys.stderr)
        raise
    finally:
        for name in reversed(names):
            command("docker", "rm", "-f", name, check=False)
        command("docker", "network", "rm", network, check=False)
        # Data directories were written by root/10001; return only this fixture's files to the caller.
        import os
        command("docker", "run", "--rm", "--network", "none", "--user", "0", "--entrypoint", "chown",
                "-v", str(directory) + ":/fixture", FLUENT, "-R", f"{os.getuid()}:{os.getgid()}", "/fixture", check=False)
        temp.cleanup()


if __name__ == "__main__":
    main()
