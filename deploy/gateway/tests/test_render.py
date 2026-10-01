#!/usr/bin/env python3
"""Pinned Traefik render checks; no Kubernetes or external endpoint calls."""

import os
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


if __name__ == "__main__":
    unittest.main(verbosity=2)
