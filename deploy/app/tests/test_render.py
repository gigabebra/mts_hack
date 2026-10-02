#!/usr/bin/env python3
"""App chart contract/negative tests. These are render fixtures, not live acceptance."""
import os
from pathlib import Path
import subprocess
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[3]
HELM = os.environ.get("MTS_TEST_HELM", str(ROOT / ".cache/tools/helm"))


def render(release="app", **values):
    args = [HELM, "template", release, str(ROOT / "deploy/app"), "-n", "demo"]
    for key, value in values.items():
        args += ["--set-string" if isinstance(value, str) else "--set", f"{key}={value}"]
    result = subprocess.run(args, check=True, capture_output=True, text=True, timeout=30)
    return {item["kind"]: item for item in yaml.safe_load_all(result.stdout) if item}


class AppRender(unittest.TestCase):
    def test_releases_are_disjoint_and_stable_identity_is_preserved(self):
        stable = render(releaseRevision="stable-1")
        canary = render("app-canary", fullnameOverride="hello-canary", releaseTrack="canary", replicas=1,
                        releaseRevision="candidate-2")
        for docs, name, release, track, revision, replicas in (
                (stable, "hello", "app", "stable", "stable-1", 2),
                (canary, "hello-canary", "app-canary", "canary", "candidate-2", 1)):
            with self.subTest(track=track):
                self.assertEqual({item["metadata"]["name"] for item in docs.values()}, {name})
                selector = {"app.kubernetes.io/name": "hello", "app.kubernetes.io/instance": release}
                self.assertEqual(docs["Service"]["spec"]["selector"], selector)
                deployment = docs["Deployment"]["spec"]
                self.assertEqual(deployment["selector"]["matchLabels"], selector)
                self.assertEqual(deployment["replicas"], replicas)
                self.assertEqual(deployment["strategy"]["rollingUpdate"]["maxUnavailable"], 0)
                pod = deployment["template"]["spec"]
                self.assertEqual(pod["terminationGracePeriodSeconds"], 30)
                self.assertEqual(pod["containers"][0]["lifecycle"]["preStop"]["exec"]["command"],
                                 ["/bin/sh", "-c", "sleep 5"])
                volumes = deployment["template"]["spec"]["volumes"]
                self.assertEqual({v["configMap"]["name"] for v in volumes if "configMap" in v}, {name})
                self.assertEqual(docs["ConfigMap"]["data"]["index.html"].encode(), b"Hello World!")
                config = docs["ConfigMap"]["data"]["nginx.conf"]
                self.assertIn(f'add_header X-Release "{track}" always;', config)
                self.assertIn(f'add_header X-Revision "{revision}" always;', config)
                self.assertIn(f'"release":"{track}","revision":"{revision}"', config)
                self.assertNotIn("{{", config)
        self.assertNotEqual(stable["Service"]["spec"]["selector"], canary["Service"]["spec"]["selector"])

    def test_revision_and_fault_change_config_checksum_and_do_not_break_probes(self):
        charts = [render("app-canary", fullnameOverride="hello-canary", releaseTrack="canary",
                         releaseRevision=revision, faultMode=fault)
                  for revision, fault in (("one", "none"), ("two", "none"), ("two", "http500"), ("two", "wrong-body"))]
        checksums = {doc["Deployment"]["spec"]["template"]["metadata"]["annotations"]["checksum/config"] for doc in charts}
        self.assertEqual(len(checksums), 4)
        self.assertEqual(charts[2]["ConfigMap"]["data"]["nginx.conf"].count('return 500 "canary fault";'), 2)
        self.assertEqual(charts[3]["ConfigMap"]["data"]["index.html"], "Broken World!")
        for chart in charts:
            self.assertIn('location = /healthz {\n            access_log off;\n            return 200 "ok";',
                          chart["ConfigMap"]["data"]["nginx.conf"])

    def test_schema_rejects_header_injection_and_accidental_stable_faults(self):
        for values in ({"releaseRevision": "bad\nheader"}, {"releaseRevision": 'bad"; stop;'},
                       {"releaseRevision": ""}, {"releaseRevision": "a" * 65}, {"releaseTrack": "other"},
                       {"faultMode": "http500"}, {"faultMode": "typo"}, {"replicas": 0},
                       {"fullnameOverride": "bad/name"}):
            with self.subTest(values=values), self.assertRaises(subprocess.CalledProcessError):
                render(**values)


if __name__ == "__main__":
    unittest.main(verbosity=2)
