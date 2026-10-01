"""Strict rendering helpers: accepted image lock and pinned Calico VXLAN."""
import copy
import ipaddress
import json
import re


def image(reference, lock):
    if reference not in lock:
        raise ValueError(f"Image is absent from accepted lock: {reference}")
    digest = lock[reference]
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
        raise ValueError(f"Invalid image digest: {reference}")
    return reference + "@" + digest


def servers(servers):
    if not isinstance(servers, list) or not 1 <= len(servers) <= 3:
        raise ValueError("Expected one to three explicit IPv6/IPv4 server addresses")
    for server in servers:
        address = ipaddress.ip_address(server)
        if (address.is_unspecified or address.is_loopback or address.is_link_local or
                address.is_multicast or getattr(address, "ipv4_mapped", None) or getattr(address, "scope_id", None)):
            raise ValueError("Servers must be unscoped, non-link-local unicast addresses")
    return servers


def dual_ips(addresses):
    """Require two real IPs in the contract order, not two of one family."""
    parsed = [ipaddress.ip_address(address) for address in addresses]
    if [address.version for address in parsed] != [6, 4]:
        raise ValueError("Expected exactly IPv6,IPv4 addresses with IPv6 primary")
    if any(address.is_unspecified or address.is_loopback or address.is_multicast or
           address.is_link_local or getattr(address, "ipv4_mapped", None) or
           getattr(address, "scope_id", None) for address in parsed):
        raise ValueError("Expected usable dual-stack unicast addresses")
    return {"ipv6": str(parsed[0]), "ipv4": str(parsed[1])}


def pin_images(documents, lock):
    def walk(value):
        if isinstance(value, dict):
            for key, item in value.items():
                if key == "image" and isinstance(item, str):
                    value[key] = image(item, lock)
                else:
                    walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)
    result = copy.deepcopy(list(documents))
    walk(result)
    return result


def calico(documents, lock, pod_cidr, node_ip, pod_cidr_ipv4, node_ipv4):
    ipaddress.IPv6Network(pod_cidr)
    ipaddress.IPv6Address(node_ip)
    ipaddress.IPv4Network(pod_cidr_ipv4)
    ipaddress.IPv4Address(node_ipv4)
    result = pin_images(documents, lock)
    config = next(doc for doc in result if doc and doc.get("kind") == "ConfigMap" and doc["metadata"]["name"] == "calico-config")
    config["data"]["calico_backend"] = "vxlan"
    # Upstream contains an unquoted numeric substitution token, not valid JSON.
    source = config["data"]["cni_network_config"]
    cni = json.loads(source.replace("__CNI_MTU__", '"__CNI_MTU__"'))
    plugin = next(plugin for plugin in cni["plugins"] if plugin["type"] == "calico")
    plugin["ipam"].update(assign_ipv4="true", assign_ipv6="true")
    config["data"]["cni_network_config"] = json.dumps(cni, indent=2).replace('"__CNI_MTU__"', "__CNI_MTU__")
    daemon = next(doc for doc in result if doc and doc.get("kind") == "DaemonSet" and doc["metadata"]["name"] == "calico-node")
    node = next(c for c in daemon["spec"]["template"]["spec"]["containers"] if c["name"] == "calico-node")
    values = {
        # The explicit IPPool is applied only after its CRD is Established.
        "NO_DEFAULT_POOLS": "true",
        "IP": node_ipv4,
        "IP6": node_ip,
        "CALICO_ROUTER_ID": "hash",
        "CLUSTER_TYPE": "k8s",
        "FELIX_IPV6SUPPORT": "true",
        # Felix otherwise calls an external HTTPS usage/version-warning service.
        # Optional telemetry remains disabled independently of IP family.
        "FELIX_USAGEREPORTINGENABLED": "false",
    }
    node["env"] = [entry for entry in node["env"] if entry["name"] not in values
                   and not entry["name"].startswith(("CALICO_IPV4POOL_", "CALICO_IPV6POOL_"))]
    node["env"].extend({"name": key, "value": value} for key, value in values.items())
    for probe in ("livenessProbe", "readinessProbe"):
        # VXLAN needs Felix health only. Keep the upstream localhost exec probe.
        command = node[probe]["exec"]["command"]
        node[probe]["exec"]["command"] = [part for part in command if part not in ("-bird-live", "-bird-ready")]
    return result


class FilterModule:
    def filters(self):
        return {"mvp_image": image, "mvp_pin_images": pin_images, "mvp_calico": calico,
                "mvp_servers": servers, "mvp_dual_ips": dual_ips}
