#!/usr/bin/env python3
"""Isolated Docker smoke for real Nginx; does not validate Gateway/Kubernetes."""

import argparse
import importlib.util
import json
from pathlib import Path
import re
import subprocess
import tempfile
import time
import uuid


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("gateway_check", ROOT / "scripts/check-gateway.py")
GATEWAY = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(GATEWAY)


def docker(*args, check=True):
    return subprocess.run(["docker", *args], capture_output=True, text=True, check=check, timeout=30)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True, help="Nginx image from config/images.lock.yaml including @sha256")
    args = parser.parse_args()
    if not re.fullmatch(r"docker\.io/nginxinc/nginx-unprivileged:[^@]+@sha256:[a-f0-9]{64}", args.image):
        parser.error("--image must be the digest-locked nginx-unprivileged image")
    name = "mts-mvp02-test-" + uuid.uuid4().hex[:12]
    network = name + "-network"
    with tempfile.TemporaryDirectory(prefix="mts-mvp02-") as temporary:
        directory = Path(temporary)
        directory.chmod(0o755)
        config = directory / "nginx.conf"
        config.write_text((ROOT / "deploy/app/files/nginx.conf").read_text())
        config.chmod(0o444)
        page = directory / "html"
        page.mkdir(mode=0o755)
        (page / "index.html").write_bytes(b"Hello World!")
        (page / "index.html").chmod(0o444)
        kubeconfig = directory / "unused-explicit-kubeconfig"
        kubeconfig.write_text("Traffic-only smoke: no kubectl invocation is made.")
        try:
            docker("network", "create", "--ipv6", "--ipv4=false", "--subnet", f"fd7a:115c:{name[-8:-4]}:{name[-4:]}::/64", network)
            docker("run", "--detach", "--name", name, "--network", network, "--read-only", "--user", "101:101",
                   "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
                   "--tmpfs", "/tmp:rw,noexec,nosuid,size=16m,uid=101,gid=101",
                   "--tmpfs", "/var/cache/nginx:rw,noexec,nosuid,size=16m,uid=101,gid=101",
                   "--publish", "[::1]::8080", "--volume", f"{config}:/etc/nginx/nginx.conf:ro",
                   "--volume", f"{page}:/usr/share/nginx/html:ro", "--entrypoint", "nginx",
                   args.image, "-g", "daemon off;")
            endpoint = docker("port", name, "8080/tcp").stdout.strip()
            assert re.fullmatch(r"\[::1\]:\d+", endpoint), endpoint
            url = "http://" + endpoint
            deadline = time.monotonic() + 20
            while True:
                try:
                    response = GATEWAY.request(url, "/healthz", None, deadline)
                    assert response["status"] == 200
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise
                    time.sleep(0.2)
            report_path = directory / "report.json"
            result = subprocess.run([
                "python3", str(ROOT / "scripts/check-gateway.py"), "--mode", "traffic",
                "--kubeconfig", str(kubeconfig), "--base-url", url, "--run-id", "container-smoke",
                "--request-id", "container-access", "--error-marker", "container-missing",
                "--requests", "2", "--timeout", "20", "--output", str(report_path)],
                capture_output=True, text=True, timeout=25)
            report = json.loads(report_path.read_text())
            assert result.returncode == 0, report
            # Exercise JSON escaping with a quote in URI and the upper valid ID limit.
            extra = GATEWAY.request(url, '/verify?escape=%22', "A" * 64, time.monotonic() + 5)
            assert extra["status"] == 200 and extra["request_id"] == "A" * 64
            logs = docker("logs", name)
            access = [json.loads(line) for line in logs.stdout.splitlines() if line.strip()]
            primary = [line for line in access if line.get("request_id") == "container-access"]
            assert len(primary) == 1, primary
            assert primary[0]["method"] == "GET" and primary[0]["uri"] == "/"
            assert primary[0]["status"] == 200 and primary[0]["bytes_sent"] == 12
            assert isinstance(primary[0]["request_time"], (float, int))
            assert primary[0]["time"]
            assert not any(line["uri"] == "/healthz" for line in access)
            assert any(line["request_id"] == "A" * 64 for line in access)
            assert "container-missing" in logs.stderr and "open()" in logs.stderr, logs.stderr
            runtime = docker("exec", name, "cat", "/proc/1/status").stdout
            assert re.search(r"^Uid:\s+101\s+101\s+101\s+101$", runtime, re.M), runtime
            assert re.search(r"^CapEff:\s+0+$", runtime, re.M), runtime
            assert re.search(r"^NoNewPrivs:\s+1$", runtime, re.M), runtime
            mounts = docker("exec", name, "cat", "/proc/1/mountinfo").stdout
            root_mounts = [line.split() for line in mounts.splitlines() if line.split()[4] == "/"]
            assert len(root_mounts) == 1 and "ro" in root_mounts[0][5].split(","), root_mounts
            assert docker("exec", name, "touch", "/root-write-probe", check=False).returncode != 0
            assert docker("exec", name, "test", "!", "-e",
                          "/var/run/secrets/kubernetes.io/serviceaccount/token").returncode == 0
            print(json.dumps({"status": "pass", "scope": "isolated Nginx container, not Gateway",
                              "checks": ["HTTP body/Request-ID/fallbacks/404", "JSON stdout access",
                                         "real missing-file stderr", "UID 101", "zero effective capabilities",
                                         "no-new-privileges", "read-only root", "no service-account token"],
                              "http_checks": len(report["checks"])}))
        finally:
            # The unique name also allows cleanup when docker run times out after creation.
            docker("rm", "--force", name, check=False)
            docker("network", "rm", network, check=False)


if __name__ == "__main__":
    main()
