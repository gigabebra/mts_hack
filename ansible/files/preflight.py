#!/usr/bin/env python3
"""Read-only network/disk guard run on the explicitly inventoried VM."""
import ipaddress
import json
import shutil
import subprocess
import sys


def validate_network(pod_cidr, service_cidr, node_ip, routes, existing=False):
    pod, service = map(ipaddress.IPv6Network, (pod_cidr, service_cidr))
    node = ipaddress.IPv6Address(node_ip)
    if (node.is_unspecified or node.is_loopback or node.is_link_local or
            node.is_multicast or node.ipv4_mapped or node.scope_id):
        raise ValueError("Node must have an unscoped, non-link-local unicast IPv6 address")
    for network in (pod, service):
        if not any(network.subnet_of(ipaddress.IPv6Network(prefix)) for prefix in ("2000::/3", "fc00::/7")):
            raise ValueError("Pod and service CIDRs must be unicast IPv6 networks")
    if pod.overlaps(service) or node in pod or node in service:
        raise ValueError("Pod, service and node networks overlap")
    if not 48 <= pod.prefixlen <= 64 or not 108 <= service.prefixlen <= 120:
        raise ValueError("Supported IPv6 prefixes: pod /48../64 (node /64), service /108../120")
    default_route = False
    for route in routes:
        destination = route.get("dst", "default")
        if destination in ("default", "::/0"):
            default_route |= bool(route.get("dev")) and route.get("type", "unicast") == "unicast"
            continue
        network = ipaddress.IPv6Network(destination, strict=False)
        if not (network.overlaps(pod) or network.overlaps(service)):
            continue
        # Only routes from this already-identified Calico cluster are allowed.
        owned = route.get("dev", "").startswith(("cali", "vxlan-v6.calico"))
        owned |= route.get("type") == "blackhole" and str(route.get("protocol")) in ("bird", "80")
        if existing and owned and network.subnet_of(pod):
            continue
        raise ValueError(f"Cluster CIDR overlaps existing route {destination}")
    if not default_route:
        raise ValueError("A usable IPv6 default route is required before bootstrap")


def validate_address(node_ip, interfaces):
    """Resolve equivalent IPv6 spellings and reject temporary/failed addresses."""
    node = ipaddress.IPv6Address(node_ip)
    for interface in interfaces:
        for address in interface.get("addr_info", []):
            if address.get("family") != "inet6" or ipaddress.IPv6Address(address["local"]) != node:
                continue
            flags = set(address.get("flags", []))
            invalid = ("tentative", "dadfailed", "deprecated", "temporary")
            if (interface.get("operstate") != "UP" or address.get("scope") != "global" or
                    any(address.get(flag) or flag in flags for flag in invalid) or
                    address.get("preferred_life_time") == 0):
                raise ValueError("Node IPv6 must be stable, preferred and assigned to an UP interface")
            name = interface["ifname"]
            if "/" in name or "\n" in name or " " in name:
                raise ValueError("Unsupported IPv6 uplink interface name")
            return name
    raise ValueError("Explicit node IPv6 is not assigned to this VM")


def main():
    pod, service, node, mode = sys.argv[1:]
    if mode not in ("existing", "new"):
        raise ValueError("Expected explicit new or existing cluster mode")
    routes = json.loads(subprocess.check_output(["ip", "-j", "-6", "route", "show", "table", "all"], text=True))
    validate_network(pod, service, node, routes, mode == "existing")
    interfaces = json.loads(subprocess.check_output(["ip", "-j", "-6", "address", "show"], text=True))
    node_interface = validate_address(node, interfaces)
    disk = shutil.disk_usage("/")
    required_free = (10 if mode == "existing" else 35) * 1024**3
    if disk.total < 50 * 1024**3 or disk.free < required_free:
        raise ValueError("Need >=50 GiB root filesystem and >=35 GiB free initially (10 GiB on repeat)")
    print(json.dumps({"network": "pass", "network_family": "IPv6", "node_interface": node_interface,
                      "disk": "pass", "mode": mode}))


if __name__ == "__main__":
    main()
