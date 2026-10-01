#!/usr/bin/env python3
"""Inspect rendered Helm contracts and lock coverage. Requires project's PyYAML."""

import argparse
from pathlib import Path
import subprocess

import yaml


ROOT = Path(__file__).resolve().parents[3]


def render(helm, release, chart, *options):
    result = subprocess.run([helm, "template", release, str(chart), "--namespace", "monitoring",
                             "--kube-version", "1.34.3", *options], check=True, capture_output=True, text=True)
    return [doc for doc in yaml.safe_load_all(result.stdout) if doc]


def walk(value):
    if isinstance(value, dict):
        for key, item in value.items():
            if key == "image" and isinstance(item, str):
                yield item
            yield from walk(item)
    elif isinstance(value, list):
        for item in value:
            yield from walk(item)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--helm", required=True)
    parser.add_argument("--chart", required=True)
    args = parser.parse_args()
    objects = render(args.helm, "monitoring", args.chart, "-f", str(ROOT / "deploy/monitoring/values.yaml"))
    extras = render(args.helm, "monitoring-extras", ROOT / "deploy/monitoring/extras")
    lock = yaml.safe_load((ROOT / "config/images.lock.yaml").read_text())["images"]
    images = set(walk(objects))
    assert len(images) == 4, images
    assert images <= lock.keys(), images - lock.keys()
    assert not any(obj["kind"] in {"Job", "Ingress", "Alertmanager", "PrometheusRule",
                                   "ValidatingWebhookConfiguration", "MutatingWebhookConfiguration"} for obj in objects)
    workloads = [obj for obj in objects if obj["kind"] in ("Deployment", "DaemonSet")]
    assert {obj["metadata"]["name"] for obj in workloads} == {
        "monitoring-operator", "monitoring-kube-state-metrics", "monitoring-prometheus-node-exporter"}
    operator = next(obj for obj in workloads if obj["metadata"]["name"] == "monitoring-operator")
    args = operator["spec"]["template"]["spec"]["containers"][0]["args"]
    image = "quay.io/prometheus-operator/prometheus-config-reloader:v0.87.1"
    assert "--prometheus-config-reloader=" + image + "@" + lock[image] in args
    for obj in workloads:
        for container in obj["spec"]["template"]["spec"]["containers"]:
            assert all(isinstance(arg, str) for arg in container.get("args", [])), container["args"]
            assert container["resources"]["requests"] and container["resources"]["limits"]
    services = [obj for obj in objects if obj["kind"] == "Service"]
    assert services and all(obj["spec"].get("ipFamilies") == ["IPv6"]
                            and obj["spec"].get("ipFamilyPolicy") == "SingleStack" for obj in services)
    assert "--localhost=[::1]" in args
    ksm = next(obj for obj in workloads if obj["metadata"]["name"] == "monitoring-kube-state-metrics")
    ksm_args = ksm["spec"]["template"]["spec"]["containers"][0]["args"]
    assert "--host=::" in ksm_args and "--telemetry-host=::" in ksm_args
    prometheus = next(obj for obj in objects if obj["kind"] == "Prometheus")["spec"]
    assert prometheus["serviceDiscoveryRole"] == "EndpointSlice"
    assert [arg['value'] for arg in prometheus['additionalArgs'] if arg['name'] == 'web.listen-address'] == [
        '[$(POD_IP)]:9090', '[::1]:9090']
    assert prometheus['containers'] == [{'name': 'prometheus', 'env': [
        {'name': 'POD_IP', 'valueFrom': {'fieldRef': {'fieldPath': 'status.podIP'}}}]}]
    assert prometheus["scrapeInterval"] == "15s" and prometheus["scrapeTimeout"] == "10s"
    assert prometheus["retention"] == "24h" and prometheus["retentionSize"] == "4GB"
    assert prometheus["serviceMonitorSelector"] == {"matchLabels": {"release": "monitoring"}}
    assert prometheus["serviceMonitorNamespaceSelector"] == {}
    claim = prometheus["storage"]["volumeClaimTemplate"]["spec"]
    assert (claim["volumeName"], claim["storageClassName"], claim["resources"]["requests"]["storage"]) == (
        "mts-prometheus", "local-retain", "12Gi")
    assert prometheus["securityContext"]["runAsUser"] == 1000
    assert prometheus["securityContext"]["runAsGroup"] == 2000
    monitors = [obj for obj in objects if obj["kind"] == "ServiceMonitor"]
    assert len(monitors) == 3
    assert all(obj["metadata"]["labels"]["release"] == "monitoring" for obj in monitors)
    assert len(extras) == 1 and extras[0]["kind"] == "ServiceMonitor"
    monitor = extras[0]
    assert monitor["metadata"]["namespace"] == "edge"
    assert monitor["metadata"]["labels"]["release"] == "monitoring"
    assert monitor["spec"]["selector"]["matchLabels"] == {
        "app.kubernetes.io/name": "traefik", "app.kubernetes.io/instance": "traefik-edge"}
    assert monitor["spec"]["endpoints"] == [
        {"port": "metrics", "path": "/metrics", "interval": "15s", "scrapeTimeout": "10s"}]
    print("PASS: Helm render contracts, image lock coverage, dynamic reloader digest, storage and monitors")


if __name__ == "__main__":
    main()
