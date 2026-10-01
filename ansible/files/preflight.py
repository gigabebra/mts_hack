#!/usr/bin/env python3
"""Read-only network/disk guard run on the explicitly inventoried VM."""
import ipaddress
import json
import shutil
import subprocess
import sys


def validate_network(pod_cidr, service_cidr, node_ip, routes, existing=False, version=6):
    pod, service = map(ipaddress.ip_network, (pod_cidr, service_cidr))
    node = ipaddress.ip_address(node_ip)
    if any(address.version != version for address in (pod, service, node)):
        raise ValueError(f"Expected IPv{version} pod, service and node addresses")
    if (node.is_unspecified or node.is_loopback or node.is_link_local or
            node.is_multicast or getattr(node, "ipv4_mapped", None) or getattr(node, "scope_id", None)):
        raise ValueError("Node must have an unscoped, non-link-local unicast address")
    if version == 6:
        for network in (pod, service):
            if not any(network.subnet_of(ipaddress.IPv6Network(prefix)) for prefix in ("2000::/3", "fc00::/7")):
                raise ValueError("Pod and service CIDRs must be unicast IPv6 networks")
        if not 48 <= pod.prefixlen <= 64 or not 108 <= service.prefixlen <= 120:
            raise ValueError("Supported IPv6 prefixes: pod /48../64 (node /64), service /108../120")
    elif version == 4:
        for network in (pod, service):
            address = network.network_address
            if address.is_unspecified or address.is_loopback or address.is_link_local or address.is_multicast or address.is_reserved:
                raise ValueError("Pod and service CIDRs must be unicast IPv4 networks")
        if not 8 <= pod.prefixlen <= 24 or not 12 <= service.prefixlen <= 24:
            raise ValueError("Supported IPv4 prefixes: pod /8../24 (node /24), service /12../24")
    else:
        raise ValueError("Expected IP family 6 or 4")
    if pod.overlaps(service) or node in pod or node in service:
        raise ValueError("Pod, service and node networks overlap")
    default_route = False
    for route in routes:
        destination = route.get("dst", "default")
        if destination in ("default", "::/0" if version == 6 else "0.0.0.0/0"):
            default_route |= bool(route.get("dev")) and route.get("type", "unicast") == "unicast"
            continue
        network = ipaddress.ip_network(destination, strict=False)
        if network.version != version:
            raise ValueError(f"Unexpected route family in IPv{version} table")
        if not (network.overlaps(pod) or network.overlaps(service)):
            continue
        # Only routes from this already-identified Calico cluster are allowed.
        owned = route.get("dev", "").startswith(("cali", "vxlan-v6.calico", "vxlan.calico"))
        owned |= route.get("type") == "blackhole" and str(route.get("protocol")) in ("bird", "80")
        if existing and owned and network.subnet_of(pod):
            continue
        raise ValueError(f"Cluster CIDR overlaps existing route {destination}")
    if not default_route:
        raise ValueError(f"A usable IPv{version} default route is required before bootstrap")


def validate_address(node_ip, interfaces):
    """Find a stable, assigned node address using canonical IP equality."""
    node = ipaddress.ip_address(node_ip)
    family = "inet6" if node.version == 6 else "inet"
    for interface in interfaces:
        for address in interface.get("addr_info", []):
            if address.get("family") != family or ipaddress.ip_address(address["local"]) != node:
                continue
            flags = set(address.get("flags", []))
            invalid = ("tentative", "dadfailed", "deprecated", "temporary")
            if (interface.get("operstate") != "UP" or address.get("scope") != "global" or
                    any(address.get(flag) or flag in flags for flag in invalid) or
                    address.get("preferred_life_time") == 0):
                raise ValueError("Node IP must be stable, preferred and assigned to an UP interface")
            name = interface["ifname"]
            if "/" in name or "\n" in name or " " in name:
                raise ValueError("Unsupported uplink interface name")
            return name
    raise ValueError("Explicit node IP is not assigned to this VM")


def main():
    pod, service, node, pod4, service4, node4, mode = sys.argv[1:]
    if mode not in ("existing", "new"):
        raise ValueError("Expected explicit new or existing cluster mode")
    node_interfaces = {}
    for version, pod_net, service_net, node_address in ((6, pod, service, node), (4, pod4, service4, node4)):
        routes = json.loads(subprocess.check_output(["ip", "-j", f"-{version}", "route", "show", "table", "all"], text=True))
        validate_network(pod_net, service_net, node_address, routes, mode == "existing", version=version)
        interfaces = json.loads(subprocess.check_output(["ip", "-j", f"-{version}", "address", "show"], text=True))
        node_interfaces[version] = validate_address(node_address, interfaces)
    disk = shutil.disk_usage("/")
    required_free = (10 if mode == "existing" else 35) * 1024**3
    if disk.total < 50 * 1024**3 or disk.free < required_free:
        raise ValueError("Need >=50 GiB root filesystem and >=35 GiB free initially (10 GiB on repeat)")
    print(json.dumps({"network": "pass", "network_family": "dual-stack-ipv6-primary", "node_interface": node_interfaces[6],
                      "node_ipv4_interface": node_interfaces[4],
                      "disk": "pass", "mode": mode}))


if __name__ == "__main__":
    main()
