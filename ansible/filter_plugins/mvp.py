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


def ipv6_servers(servers):
    if not isinstance(servers, list) or not 1 <= len(servers) <= 3:
        raise ValueError("Expected one to three explicit IPv6 server addresses")
    for server in servers:
        address = ipaddress.IPv6Address(server)
        if (address.is_unspecified or address.is_loopback or address.is_link_local or
                address.is_multicast or address.ipv4_mapped or address.scope_id):
            raise ValueError("Servers must be unscoped, non-link-local unicast IPv6 addresses")
    return servers


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


def calico(documents, lock, pod_cidr, node_ip):
    ipaddress.IPv6Network(pod_cidr)
    ipaddress.IPv6Address(node_ip)
    result = pin_images(documents, lock)
    config = next(doc for doc in result if doc and doc.get("kind") == "ConfigMap" and doc["metadata"]["name"] == "calico-config")
    config["data"]["calico_backend"] = "vxlan"
    # Upstream contains an unquoted numeric substitution token, not valid JSON.
    source = config["data"]["cni_network_config"]
    cni = json.loads(source.replace("__CNI_MTU__", '"__CNI_MTU__"'))
    plugin = next(plugin for plugin in cni["plugins"] if plugin["type"] == "calico")
    plugin["ipam"].update(assign_ipv4="false", assign_ipv6="true")
    config["data"]["cni_network_config"] = json.dumps(cni, indent=2).replace('"__CNI_MTU__"', "__CNI_MTU__")
    daemon = next(doc for doc in result if doc and doc.get("kind") == "DaemonSet" and doc["metadata"]["name"] == "calico-node")
    node = next(c for c in daemon["spec"]["template"]["spec"]["containers"] if c["name"] == "calico-node")
    values = {
        # The explicit IPPool is applied only after its CRD is Established.
        "NO_DEFAULT_POOLS": "true",
        "IP": "none",
        "IP6": node_ip,
        "CALICO_ROUTER_ID": "hash",
        "CLUSTER_TYPE": "k8s",
        "FELIX_IPV6SUPPORT": "true",
        "FELIX_HEALTHHOST": "::1",
    }
    node["env"] = [entry for entry in node["env"] if entry["name"] not in values
                   and not entry["name"].startswith(("CALICO_IPV4POOL_", "CALICO_IPV6POOL_"))]
    node["env"].extend({"name": key, "value": value} for key, value in values.items())
    for probe in ("livenessProbe", "readinessProbe"):
        # The pinned calico-node exec health utility concatenates host:port
        # without IPv6 brackets. Kubelet HTTP probes support literal IPv6.
        node[probe].pop("exec", None)
        node[probe]["httpGet"] = {"host": "::1", "port": 9099,
                                  "path": "/liveness" if probe == "livenessProbe" else "/readiness"}
    return result


class FilterModule:
    def filters(self):
        return {"mvp_image": image, "mvp_pin_images": pin_images, "mvp_calico": calico,
                "mvp_ipv6_servers": ipv6_servers}
