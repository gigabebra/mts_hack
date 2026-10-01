#!/usr/bin/env python3
"""Read-only network/disk guard run on the explicitly inventoried VM."""
import ipaddress
import json
import shutil
import subprocess
import sys


def validate_network(pod_cidr, service_cidr, node_ip, routes, existing=False):
    pod, service = map(ipaddress.IPv4Network, (pod_cidr, service_cidr))
    node = ipaddress.IPv4Address(node_ip)
    if pod.overlaps(service) or node in pod or node in service:
        raise ValueError("Pod, service and node networks overlap")
    if pod.prefixlen > 26 or service.prefixlen > 24:
        raise ValueError("Pod /26 and service /24 are minimum supported sizes")
    for route in routes:
        destination = route.get("dst", "default")
        if destination == "default":
            continue
        network = ipaddress.IPv4Network(destination, strict=False)
        if not (network.overlaps(pod) or network.overlaps(service)):
            continue
        # Only routes from this already-identified Calico cluster are allowed.
        owned = route.get("dev", "").startswith(("cali", "vxlan.calico"))
        owned |= route.get("type") == "blackhole" and str(route.get("protocol")) in ("bird", "80")
        if existing and owned and network.subnet_of(pod):
            continue
        raise ValueError(f"Cluster CIDR overlaps existing route {destination}")


def main():
    pod, service, node, mode = sys.argv[1:]
    routes = json.loads(subprocess.check_output(["ip", "-j", "-4", "route", "show", "table", "all"], text=True))
    validate_network(pod, service, node, routes, mode == "existing")
    disk = shutil.disk_usage("/")
    required_free = (10 if mode == "existing" else 35) * 1024**3
    if disk.total < 50 * 1024**3 or disk.free < required_free:
        raise ValueError("Need >=50 GiB root filesystem and >=35 GiB free initially (10 GiB on repeat)")
    print(json.dumps({"network": "pass", "disk": "pass", "mode": mode}))


if __name__ == "__main__":
    main()
