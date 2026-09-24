"""Opt-in real BuildStream checks; no SDK downloads or compiler required."""

import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

PLUGIN = """
import uuid
from buildstream import Element

class Probe(Element):
    BST_MIN_VERSION = "2.0"
    BST_RUN_COMMANDS = False

    def configure(self, node):
        node.validate_keys(["vary"])
        self.vary = node.get_bool("vary", default=False)

    def preflight(self):
        pass

    def get_unique_key(self):
        return {"vary": self.vary}

    def configure_sandbox(self, sandbox):
        pass

    def stage(self, sandbox):
        pass

    def assemble(self, sandbox):
        output = sandbox.get_virtual_directory().open_directory("output", create=True)
        with output.open_file("payload", mode="w") as stream:
            stream.write(str(uuid.uuid4()) if self.vary else "fixed")
        return "/output"

def setup():
    return Probe
"""


@unittest.skipUnless(
    os.environ.get("BST_INTEGRATION"), "set BST_INTEGRATION to the bst executable"
)
class BuildStreamIntegration(unittest.TestCase):
    def test_fresh_cached_and_nonreproducible(self):
        with tempfile.TemporaryDirectory(prefix="bst-reprotest-integration-") as tmp:
            root = Path(tmp)
            project = root / "project"
            (project / "elements").mkdir(parents=True)
            (project / "plugins").mkdir()
            (project / "plugins" / "probe.py").write_text(PLUGIN)
            (project / "project.conf").write_text(
                "name: reprotest-integration\nmin-version: 2.0\nelement-path: elements\n"
                "plugins:\n- origin: local\n  path: plugins\n  elements: [probe]\n"
            )
            (project / "elements" / "dep.bst").write_text("kind: probe\n")
            for target, vary in (("stable", "false"), ("varying", "true")):
                (project / "elements" / f"{target}.bst").write_text(
                    f"kind: probe\nbuild-depends: [dep.bst]\nconfig:\n  vary: {vary}\n"
                )
            config = root / "user.conf"
            config.write_text(f"cachedir: {root / 'cache'}\nlogdir: {root / 'logs'}\n")
            for target, mode, expected in (
                ("stable", "fresh", 0),
                ("stable", "cached", 0),
                ("varying", "fresh", 1),
            ):
                output = root / f"{target}-{mode}"
                extra = ["--prepare", "pull"] if mode == "cached" else []
                proc = subprocess.run(
                    [
                        sys.executable,
                        str(Path(__file__).resolve().parents[1] / "reprotest.py"),
                        "--bst",
                        os.environ["BST_INTEGRATION"],
                        "--config",
                        str(config),
                        "-C",
                        str(project),
                        "--baseline",
                        mode,
                        "--diffoscope",
                        "never",
                        *extra,
                        f"{target}.bst",
                        str(output),
                    ],
                    text=True,
                    check=False,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                )
                self.assertEqual(proc.returncode, expected, proc.stdout)
                report = json.loads((output / "result.json").read_text())
                self.assertEqual(
                    report["status"], "different" if expected else "reproducible"
                )
                # BuildStream touches reference mtimes for LRU, even on cache hits.
                # Compare artifact references and actual build-start events instead.
                dependency_refs = (
                    root
                    / "cache"
                    / "artifacts"
                    / "refs"
                    / "reprotest-integration"
                    / "dep"
                )
                refs = {p.name: p.read_bytes() for p in dependency_refs.iterdir()}
                builds = re.findall(r"\[\s*build:dep\.bst\s*\]\s+START", proc.stdout)
                if target == "stable" and mode == "fresh":
                    initial_refs = refs
                    self.assertTrue(builds)
                else:
                    self.assertEqual(refs, initial_refs)
                    self.assertEqual(builds, [])

            # Pull-only preparation must never compile a missing prerequisite.
            config.write_text(
                f"cachedir: {root / 'empty-cache'}\nlogdir: {root / 'logs'}\n"
            )
            proc = subprocess.run(
                [
                    sys.executable,
                    str(Path(__file__).resolve().parents[1] / "reprotest.py"),
                    "--bst",
                    os.environ["BST_INTEGRATION"],
                    "--config",
                    str(config),
                    "-C",
                    str(project),
                    "--prepare",
                    "pull",
                    "stable.bst",
                    str(root / "missing"),
                ],
                text=True,
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
            )
            self.assertEqual(proc.returncode, 2, proc.stdout)
            self.assertNotRegex(proc.stdout, r"\[\s*build:.*\]\s+START")


if __name__ == "__main__":
    unittest.main()
