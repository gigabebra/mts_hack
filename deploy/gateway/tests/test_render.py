#!/usr/bin/env python3
"""Pinned Traefik render checks; no Kubernetes or external endpoint calls."""

import os
import base64
from pathlib import Path
import subprocess
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[3]
HELM = os.environ.get("MTS_TEST_HELM", str(ROOT / ".cache/tools/helm"))
CHART = os.environ.get("MTS_TEST_TRAEFIK_CHART", str(ROOT / ".cache/downloads/traefik.tgz"))
# v3.6.2 cmd/configuration.go: NewTraefikConfiguration sets check=true;
# SendAnonymousUsage is a bool left at its false zero value.
DEFAULT_GLOBAL = {"checknewversion": True, "sendanonymoususage": False}


def rendered_global(*overrides):
    rendered = subprocess.run(
        [HELM, "template", "traefik", CHART, "--namespace", "edge", "--kube-version", "1.34.3",
         "-f", str(ROOT / "deploy/gateway/traefik-values.yaml"), *overrides],
        check=True, capture_output=True, text=True, timeout=30).stdout
    deployment = next(doc for doc in yaml.safe_load_all(rendered)
                      if doc and doc.get("kind") == "Deployment" and doc["metadata"]["name"] == "traefik")
    container = next(item for item in deployment["spec"]["template"]["spec"]["containers"]
                     if item["name"] == "traefik")
    effective = dict(DEFAULT_GLOBAL)
    for argument in container["args"]:
        flag, separator, value = argument.lower().partition("=")
        if flag.startswith("--global."):
            name = flag.removeprefix("--global.")
            if name in effective:
                if separator and value not in ("true", "false"):
                    raise AssertionError(f"Unexpected boolean argument: {argument}")
                effective[name] = value != "false" if separator else True
    return effective


def render_gateway(*overrides):
    rendered = subprocess.run(
        [HELM, "template", "gateway", str(ROOT / "deploy/gateway"), "-n", "demo", *overrides],
        check=True, capture_output=True, text=True, timeout=30).stdout
    return {(doc["kind"], doc["metadata"]["name"]): doc for doc in yaml.safe_load_all(rendered) if doc}


class TraefikRender(unittest.TestCase):
    def test_no_external_version_or_usage_requests(self):
        self.assertEqual(rendered_global(), {"checknewversion": False, "sendanonymoususage": False})

    def test_render_detects_either_reporter_enabled(self):
        # Enabling checkNewVersion removes its CLI flag: the runtime default is true.
        # Enabling sendAnonymousUsage adds a bare flag: its effective value is true.
        for setting in ("checkNewVersion", "sendAnonymousUsage"):
            with self.subTest(setting=setting):
                effective = rendered_global("--set", "global." + setting + "=true")
                self.assertTrue(effective[setting.lower()])
                self.assertNotEqual(effective, {"checknewversion": False, "sendanonymoususage": False})

    def test_https_nodeport_entrypoint_is_exposed_without_forcing_tls_on_http(self):
        rendered = subprocess.run(
            [HELM, "template", "traefik", CHART, "--namespace", "edge", "--kube-version", "1.34.3",
             "-f", str(ROOT / "deploy/gateway/traefik-values.yaml")],
            check=True, capture_output=True, text=True, timeout=30).stdout
        docs = [doc for doc in yaml.safe_load_all(rendered) if doc]
        service = next(doc for doc in docs if doc["kind"] == "Service" and doc["metadata"]["name"] == "traefik")
        ports = {port["name"]: port for port in service["spec"]["ports"]}
        self.assertEqual((ports["web"]["port"], ports["web"]["nodePort"]), (80, 30080))
        self.assertEqual((ports["websecure"]["port"], ports["websecure"]["nodePort"]), (443, 30443))
        deployment = next(doc for doc in docs if doc["kind"] == "Deployment")
        args = deployment["spec"]["template"]["spec"]["containers"][0]["args"]
        self.assertIn("--entryPoints.websecure.address=:8443/tcp", args)
        self.assertFalse(any("redirections" in arg or "websecure.http.tls=" in arg for arg in args))


class GatewayRender(unittest.TestCase):
    def test_defaults_and_split_share_both_user_routes_but_preserve_directed_routes(self):
        for weights in ((100, 0), (90, 10)):
            docs = render_gateway("--set", f"traffic.stableWeight={weights[0]},traffic.canaryWeight={weights[1]}")
            self.assertNotIn(("Secret", "hello-tls"), docs)
            listeners = docs[("Gateway", "gateway")]["spec"]["listeners"]
            self.assertEqual([(item["name"], item["port"], item["protocol"]) for item in listeners],
                             [("web", 8000, "HTTP"), ("websecure", 8443, "HTTPS")])
            self.assertEqual(listeners[1]["tls"]["certificateRefs"][0]["name"], "hello-tls")
            routes = {name: doc["spec"] for (kind, name), doc in docs.items() if kind == "HTTPRoute"}
            self.assertEqual(set(routes), {"hello", "hello-host", "hello-stable", "hello-canary"})
            for name, spec in routes.items():
                self.assertEqual([item["sectionName"] for item in spec["parentRefs"]], ["web", "websecure"])
                rule = spec["rules"][0]
                if name in ("hello", "hello-host"):
                    self.assertEqual([(item["name"], item["weight"]) for item in rule["backendRefs"]],
                                     [("hello", weights[0]), ("hello-canary", weights[1])])
                else:
                    track = name.removeprefix("hello-")
                    self.assertEqual(rule["matches"], [{"path": {"type": "Exact", "value": "/backend/" + track}}])
                    self.assertEqual(rule["filters"], [{"type": "URLRewrite", "urlRewrite": {
                        "path": {"type": "ReplaceFullPath", "replaceFullPath": "/"}}}])
                    self.assertEqual([(item["name"], item["weight"]) for item in rule["backendRefs"]],
                                     [("hello" if track == "stable" else "hello-canary", 100)])
            self.assertEqual(routes["hello-host"]["hostnames"], ["hello.test"])
            self.assertNotIn("hostnames", routes["hello"])

    def test_tls_secret_requires_complete_credentials_and_keeps_public_ca(self):
        values = "tls.certificate=fixture-cert,tls.privateKey=fixture-key,tls.caCertificate=fixture-ca"
        doc = render_gateway("--set-string", values)[("Secret", "hello-tls")]
        self.assertEqual(doc["type"], "kubernetes.io/tls")
        self.assertEqual({key: base64.b64decode(value).decode() for key, value in doc["data"].items()},
                         {"tls.crt": "fixture-cert", "tls.key": "fixture-key", "ca.crt": "fixture-ca"})
        for values in ("tls.certificate=fixture-cert", "tls.privateKey=fixture-key", "tls.caCertificate=fixture-ca"):
            with self.subTest(values=values), self.assertRaises(subprocess.CalledProcessError):
                render_gateway("--set-string", values)

    def test_disabling_tls_preserves_http_for_explicit_render_only(self):
        docs = render_gateway("--set", "tls.enabled=false")
        self.assertEqual([item["name"] for item in docs[("Gateway", "gateway")]["spec"]["listeners"]], ["web"])
        for (kind, _), doc in docs.items():
            if kind == "HTTPRoute":
                self.assertEqual([item["sectionName"] for item in doc["spec"]["parentRefs"]], ["web"])

    def test_invalid_weights_names_and_half_secrets_fail_before_apply(self):
        for values in ("traffic.stableWeight=90", "traffic.canaryWeight=-1", "traffic.stableWeight=1.5",
                       "traffic.stableWeight=0,traffic.canaryWeight=0", "tls.hostname=invalid/name",
                       "tls.secretName=Bad_Name", "tls.enabled=false,tls.certificate=fixture-cert"):
            with self.subTest(values=values), self.assertRaises(subprocess.CalledProcessError):
                render_gateway("--set", values)


if __name__ == "__main__":
    unittest.main(verbosity=2)
