"""Negative guards and real upstream render checks; no live cluster claim."""
import importlib.util
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "filter_plugins"))
from mvp import calico, image, pin_images  # noqa: E402

spec = importlib.util.spec_from_file_location("preflight", ROOT / "files/preflight.py")
preflight = importlib.util.module_from_spec(spec)
spec.loader.exec_module(preflight)


class NetworkGuards(unittest.TestCase):
    def check_routes(self, routes, existing=False):
        preflight.validate_network("10.244.0.0/16", "10.96.0.0/12", "192.0.2.10", routes, existing)

    def test_unrelated_routes(self):
        self.check_routes([{"dst": "default"}, {"dst": "192.0.2.0/24", "dev": "ens18"}])

    def test_overlapping_pod_route_fails_before_modification(self):
        with self.assertRaises(ValueError):
            self.check_routes([{"dst": "10.244.0.0/24", "dev": "ens18"}])

    def test_overlapping_service_or_broader_route_fails(self):
        for route in ("10.100.0.0/24", "10.0.0.0/8"):
            with self.assertRaises(ValueError):
                self.check_routes([{"dst": route, "dev": "ens18"}], existing=True)

    def test_existing_calico_routes_only_after_identity_verification(self):
        routes = [{"dst": "10.244.1.2/32", "dev": "calif00"}, {"dst": "10.244.1.0/26", "dev": "vxlan.calico"}, {"dst": "10.244.2.0/26", "type": "blackhole", "protocol": 80}]
        self.check_routes(routes, existing=True)
        with self.assertRaises(ValueError):
            self.check_routes(routes)

    def test_node_or_service_overlap_rejected(self):
        with self.assertRaises(ValueError):
            preflight.validate_network("10.244.0.0/16", "10.244.0.0/24", "192.0.2.10", [])
        with self.assertRaises(ValueError):
            preflight.validate_network("10.244.0.0/16", "10.96.0.0/12", "10.244.1.1", [])


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
