"""Negative guards and real upstream render checks; no live cluster claim."""
import importlib.util
import pathlib
import json
import copy
import hashlib
import ipaddress
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "filter_plugins"))
from mvp import calico, image, pin_images, ipv6_servers  # noqa: E402

spec = importlib.util.spec_from_file_location("preflight", ROOT / "files/preflight.py")
preflight = importlib.util.module_from_spec(spec)
spec.loader.exec_module(preflight)


POD = "fd4d:5453:1000:100::/56"
SERVICE = "fd4d:5453:1000:200::/112"
NODE = "2001:db8:20::10"
DEFAULT = {"dst": "default", "dev": "ens18", "gateway": "fe80::1"}


class NetworkGuards(unittest.TestCase):
    def check_routes(self, routes, existing=False):
        preflight.validate_network(POD, SERVICE, NODE, [DEFAULT] + routes, existing)

    def test_unrelated_routes(self):
        self.check_routes([{"dst": "2001:db8:20::/64", "dev": "ens18"}])

    def test_usable_ipv6_default_route_required(self):
        for routes in ([], [{"dst": "::/0", "type": "unreachable"}], [{"dst": "default"}]):
            with self.subTest(routes=routes), self.assertRaises(ValueError):
                preflight.validate_network(POD, SERVICE, NODE, routes)

    def test_overlapping_pod_route_fails_before_modification(self):
        with self.assertRaises(ValueError):
            self.check_routes([{"dst": "fd4d:5453:1000:101::/64", "dev": "ens18"}])

    def test_overlapping_service_or_broader_route_fails(self):
        for route in ("fd4d:5453:1000:200::10/128", "fd4d:5453:1000::/48"):
            with self.subTest(route=route), self.assertRaises(ValueError):
                self.check_routes([{"dst": route, "dev": "ens18"}], existing=True)

    def test_existing_calico_routes_only_after_identity_verification(self):
        routes = [{"dst": "fd4d:5453:1000:101::2/128", "dev": "calif00"},
                  {"dst": "fd4d:5453:1000:102::/122", "dev": "vxlan-v6.calico"},
                  {"dst": "fd4d:5453:1000:103::/122", "type": "blackhole", "protocol": 80}]
        self.check_routes(routes, existing=True)
        with self.assertRaises(ValueError):
            self.check_routes(routes)

    def test_foreign_or_service_calico_routes_are_not_exempt(self):
        for route in ({"dst": "fd4d:5453:1000:200::/112", "dev": "calif00"},
                      {"dst": "fd4d:5453:1000:103::/122", "type": "blackhole", "protocol": "static"}):
            with self.subTest(route=route), self.assertRaises(ValueError):
                self.check_routes([route], existing=True)

    def test_node_or_service_overlap_rejected(self):
        for pod, service, node in ((POD, "fd4d:5453:1000:100::/112", NODE),
                                   (POD, SERVICE, "fd4d:5453:1000:101::1")):
            with self.assertRaises(ValueError):
                preflight.validate_network(pod, service, node, [DEFAULT])

    def test_ipv4_scoped_mapped_loopback_multicast_nodes_rejected(self):
        for node in ("192.0.2.1", "::ffff:192.0.2.1", "::1", "::", "ff02::1", "fe80::1", NODE + "%ens18"):
            with self.subTest(node=node), self.assertRaises(ValueError):
                preflight.validate_network(POD, SERVICE, node, [DEFAULT])

    def test_wrong_family_and_unsupported_prefixes_rejected(self):
        for pod, service in (("10.244.0.0/16", SERVICE), (POD, "10.96.0.0/12"),
                             ("fd4d:5453:1000:100::/80", SERVICE), (POD, "fd4d:5453:2000::/64"),
                             (POD, "fd4d:5453:1000:200::/124"), ("::/48", SERVICE),
                             ("ff00::/48", SERVICE)):
            with self.subTest(pod=pod, service=service), self.assertRaises(ValueError):
                preflight.validate_network(pod, service, NODE, [DEFAULT])

    def test_stable_assigned_address_normalization(self):
        interface = {"ifname": "ens18", "operstate": "UP", "addr_info": [
            {"family": "inet6", "local": "2001:0db8:0020::0010", "scope": "global"}]}
        self.assertEqual(preflight.validate_address(NODE, [interface]), "ens18")
        with self.assertRaises(ValueError):
            preflight.validate_address("2001:db8:20::20", [interface])
        for flag in ("tentative", "dadfailed", "deprecated", "temporary"):
            unstable = copy.deepcopy(interface)
            unstable["addr_info"][0][flag] = True
            with self.subTest(flag=flag), self.assertRaises(ValueError):
                preflight.validate_address(NODE, [unstable])

    def test_servers_cannot_fall_back_to_ipv4(self):
        self.assertEqual(ipv6_servers([NODE]), [NODE])
        for addresses in ([], [NODE] * 4, NODE, ["192.0.2.10"], ["::1"], ["fe80::1%ens18"]):
            with self.subTest(addresses=addresses), self.assertRaises(ValueError):
                ipv6_servers(addresses)


class IPv6Render(unittest.TestCase):
    def test_coredns_initial_and_repeat_patch_keep_ipv6_self_health(self):
        import jinja2
        import yaml
        corefile = (ROOT / "files/coredns.Corefile").read_text()
        env = jinja2.Environment(loader=jinja2.FileSystemLoader(ROOT / "templates"), undefined=jinja2.StrictUndefined)
        env.filters.update(to_json=json.dumps, mvp_image=image,
                           hash=lambda value, kind: hashlib.new(kind, value.encode()).hexdigest())
        variables = {"coredns_corefile": corefile,
                     "image_lock": yaml.safe_load((ROOT.parent / "config/images.lock.yaml").read_text())}
        template = env.get_template("corednsdeployment+strategic.yaml.j2")
        patch = yaml.safe_load(template.render(**variables))
        self.assertEqual(patch, yaml.safe_load(template.render(**variables)))
        pod = patch["spec"]["template"]
        container = pod["spec"]["containers"][0]
        self.assertIn("@sha256:", container["image"])
        self.assertEqual(container["env"], [{"name": "POD_IP", "valueFrom": {
            "fieldRef": {"apiVersion": "v1", "fieldPath": "status.podIP"}}}])
        rendered = corefile.replace("{$POD_IP}", NODE)
        directives = {line.strip().split()[0]: line.strip().split()[1:]
                      for line in rendered.splitlines() if line.strip() and line.strip() != "}"}
        self.assertEqual(directives["bind"], [NODE])
        self.assertEqual(directives["health"], [f"[{NODE}]:8080", "{"])
        self.assertEqual(directives["ready"], [f"[{NODE}]:8181"])
        self.assertEqual(directives["prometheus"], [f"[{NODE}]:9153"])
        self.assertIn("loop", directives)
        self.assertNotIn("127.0.0.1", corefile)
        changed = yaml.safe_load(template.render(**{**variables, "coredns_corefile": corefile + "# changed\n"}))
        self.assertNotEqual(pod["metadata"]["annotations"], changed["spec"]["template"]["metadata"]["annotations"])

    def test_ntp_fallback_and_service_filter_cannot_select_ipv4(self):
        import jinja2
        import yaml
        tasks = yaml.safe_load((ROOT / "tasks/os.yaml").read_text())
        source = next(task["ansible.builtin.copy"]["content"] for task in tasks
                      if task["name"] == "Configure NTP traffic to explicit IPv6 servers")
        env = jinja2.Environment(undefined=jinja2.StrictUndefined)
        env.filters["mvp_ipv6_servers"] = ipv6_servers
        config = env.from_string(source).render(ntp_servers=[NODE])
        entries = dict(line.split("=", 1) for line in config.splitlines() if "=" in line)
        self.assertEqual(entries, {"NTP": NODE, "FallbackNTP": NODE})
        with self.assertRaises(ValueError):
            env.from_string(source).render(ntp_servers=["192.0.2.1"])
        filters = (ROOT / "files/timesyncd-ipv6.conf").read_text().splitlines()
        self.assertEqual([line for line in filters if line.startswith("IPAddressDeny=")],
                         ["IPAddressDeny=", "IPAddressDeny=any"])
        self.assertEqual([line for line in filters if line.startswith("IPAddressAllow=")],
                         ["IPAddressAllow=", "IPAddressAllow=::/0"])
        allow = ipaddress.ip_network("::/0")
        self.assertIn(ipaddress.ip_address(NODE), allow)
        self.assertNotIn(ipaddress.ip_address("192.0.2.1"), allow)

    def test_calico_assigns_ipv6_only_without_unintended_default_pool(self):
        original = [
            {"kind": "ConfigMap", "metadata": {"name": "calico-config"}, "data": {
                "calico_backend": "bird",
                "cni_network_config": '{"plugins":[{"type":"calico","mtu":__CNI_MTU__,"ipam":{"type":"calico-ipam"}}]}' }},
            {"kind": "DaemonSet", "metadata": {"name": "calico-node"}, "spec": {"template": {"spec": {"containers": [{
                "name": "calico-node", "image": "calico/node:v1",
                "env": [{"name": "CALICO_IPV4POOL_VXLAN", "value": "Never"}],
                "livenessProbe": {"exec": {"command": ["/bin/calico-node", "-bird-live", "-felix-live"]}},
                "readinessProbe": {"exec": {"command": ["/bin/calico-node", "-bird-ready", "-felix-ready"]}}
            }]}}}}
        ]
        result = calico(original, {"calico/node:v1": "sha256:" + "a" * 64}, POD, NODE)
        config = json.loads(result[0]["data"]["cni_network_config"].replace("__CNI_MTU__", "0"))
        self.assertEqual(config["plugins"][0]["ipam"], {"type": "calico-ipam", "assign_ipv4": "false", "assign_ipv6": "true"})
        container = result[1]["spec"]["template"]["spec"]["containers"][0]
        env = {entry["name"]: entry["value"] for entry in container["env"]}
        self.assertEqual((env["IP"], env["IP6"], env["FELIX_IPV6SUPPORT"]), ("none", NODE, "true"))
        self.assertEqual(env["NO_DEFAULT_POOLS"], "true")
        self.assertFalse(any(name.startswith(("CALICO_IPV4POOL_", "CALICO_IPV6POOL_")) for name in env))
        self.assertEqual(container["readinessProbe"]["httpGet"]["host"], "::1")
        self.assertNotIn("exec", container["livenessProbe"])
        self.assertEqual(original[0]["data"]["calico_backend"], "bird")

    def test_rendered_control_plane_and_workload_traffic_are_ipv6(self):
        import jinja2
        import yaml
        env = jinja2.Environment(loader=jinja2.FileSystemLoader(ROOT / "templates"), undefined=jinja2.StrictUndefined)
        env.filters.update(to_json=json.dumps, mvp_image=image)
        repo = ROOT.parent
        variables = yaml.safe_load((repo / "config/versions.yaml").read_text())
        variables.update(yaml.safe_load((repo / "config/environments/ubuntu.example.yaml").read_text()))
        variables.update(remote_state="/etc/mts-stack", health_uuid={"stdout": "fixture"},
                         health_server_ipv6="fd4d:5453:1000:100::20", health_service_ipv6="fd4d:5453:1000:200::20",
                         health_kubernetes_ipv6="fd4d:5453:1000:200::1",
                         image_lock=yaml.safe_load((repo / "config/images.lock.yaml").read_text()))
        docs = list(yaml.safe_load_all(env.get_template("kubeadm.yaml.j2").render(**variables)))
        by_kind = {doc["kind"]: doc for doc in docs}
        cluster = by_kind["ClusterConfiguration"]
        self.assertEqual(cluster["networking"]["podSubnet"], POD)
        self.assertEqual(cluster["networking"]["serviceSubnet"], SERVICE)
        self.assertEqual(cluster["controllerManager"]["extraArgs"][0]["value"], "::1")
        self.assertEqual(by_kind["KubeletConfiguration"]["address"], NODE)
        self.assertEqual(by_kind["KubeletConfiguration"]["healthzBindAddress"], "::1")
        self.assertEqual(by_kind["KubeProxyConfiguration"]["nodePortAddresses"], [NODE + "/128"])
        pool = yaml.safe_load(env.get_template("calico-pool.yaml.j2").render(**variables))
        self.assertEqual((pool["spec"]["cidr"], pool["spec"]["vxlanMode"]), (POD, "Always"))
        server, service = list(yaml.safe_load_all(env.get_template("health.yaml.j2").render(**variables)))
        self.assertIn("[::]:8080", server["spec"]["containers"][0]["command"][-1])
        self.assertEqual(service["spec"]["ipFamilies"], ["IPv6"])
        client = yaml.safe_load(env.get_template("health-client.yaml.j2").render(**variables))
        command = client["spec"]["template"]["spec"]["containers"][0]["command"][-1]
        self.assertIn("nslookup -type=AAAA", command)
        self.assertIn("http://[fd4d:5453:1000:100::20]:8080", command)


class ImmutableImages(unittest.TestCase):
    def test_unknown_and_malformed_lock_fail(self):
        with self.assertRaises(ValueError):
            image("unaccepted/image:latest", {})
        with self.assertRaises(ValueError):
            image("accepted/image:1", {"accepted/image:1": "sha256:broken"})

    def test_init_and_regular_container_images_pinned_without_mutating_source(self):
        ref = "accepted/image:1"
        lock = {ref: "sha256:" + "a" * 64}
        original = [{"spec": {"initContainers": [{"image": ref}], "containers": [{"image": ref}]}}]
        pinned = pin_images(original, lock)
        for key in ("containers", "initContainers"):
            self.assertEqual(original[0]["spec"][key][0]["image"], ref)
            self.assertEqual(pinned[0]["spec"][key][0]["image"], ref + "@" + lock[ref])


if __name__ == "__main__":
    unittest.main()
