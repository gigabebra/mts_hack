"""Strict rendering helpers: accepted image lock and pinned Calico VXLAN."""
import copy
import re


def image(reference, lock):
    if reference not in lock:
        raise ValueError(f"Image is absent from accepted lock: {reference}")
    digest = lock[reference]
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
        raise ValueError(f"Invalid image digest: {reference}")
    return reference + "@" + digest


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
    result = pin_images(documents, lock)
    config = next(doc for doc in result if doc and doc.get("kind") == "ConfigMap" and doc["metadata"]["name"] == "calico-config")
    config["data"]["calico_backend"] = "vxlan"
    daemon = next(doc for doc in result if doc and doc.get("kind") == "DaemonSet" and doc["metadata"]["name"] == "calico-node")
    node = next(c for c in daemon["spec"]["template"]["spec"]["containers"] if c["name"] == "calico-node")
    values = {
        "CALICO_IPV4POOL_CIDR": pod_cidr,
        "CALICO_IPV4POOL_IPIP": "Never",
        "CALICO_IPV4POOL_VXLAN": "Always",
        "CALICO_IPV6POOL_VXLAN": "Never",
        "IP": node_ip,
        "IP6": "none",
        "CLUSTER_TYPE": "k8s",
        "FELIX_IPV6SUPPORT": "false",
    }
    node["env"] = [entry for entry in node["env"] if entry["name"] not in values]
    node["env"].extend({"name": key, "value": value} for key, value in values.items())
    for probe in ("livenessProbe", "readinessProbe"):
        command = node[probe]["exec"]["command"]
        node[probe]["exec"]["command"] = [part for part in command if part not in ("-bird-live", "-bird-ready")]
    return result


class FilterModule:
    def filters(self):
        return {"mvp_image": image, "mvp_pin_images": pin_images, "mvp_calico": calico}
