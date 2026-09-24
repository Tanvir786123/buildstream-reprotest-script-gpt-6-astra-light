import copy
import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / "reprotest.py"
spec = importlib.util.spec_from_file_location("reprotest", SCRIPT)
reprotest = importlib.util.module_from_spec(spec)
spec.loader.exec_module(reprotest)
KEY = "a" * 64


class ManifestTests(unittest.TestCase):
    def test_content_permissions_empty_dirs_and_symlinks(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            file = root / "file"
            file.write_bytes(b"hello")
            original = reprotest.manifest(root)
            os.utime(file, (1, 1))
            self.assertEqual(original, reprotest.manifest(root))
            file.chmod(0o755)
            self.assertNotEqual(original, reprotest.manifest(root))
            mode_changed = reprotest.manifest(root)
            file.write_bytes(b"world")  # Same length, different bytes.
            self.assertNotEqual(mode_changed, reprotest.manifest(root))
            (root / "empty").mkdir()
            (root / "link").symlink_to("missing")
            (root / "loop").symlink_to(".")
            tree = reprotest.manifest(root)
            self.assertEqual(tree["link"], ["symlink", "missing"])
            self.assertEqual(tree["empty"][0], "directory")
            (root / "link").unlink()
            (root / "link").symlink_to("another")
            self.assertNotEqual(tree, reprotest.manifest(root))

    def test_special_file_is_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.mkfifo(Path(tmp) / "fifo")
            with self.assertRaises(reprotest.CheckError):
                reprotest.manifest(Path(tmp))


class ConfigurationTests(unittest.TestCase):
    def test_disable_all_artifact_remotes_preserve_settings(self):
        config = {
            "cachedir": "/tmp/cache",
            "scheduler": {"builders": 3},
            "artifacts": [{"url": "https://example.org"}],
            "projects": {
                "sdk": {
                    "options": {"arch": "x86_64"},
                    "artifacts": [{"url": "https://example.org"}],
                }
            },
        }
        before = copy.deepcopy(config)
        local = reprotest.local_config(config)
        self.assertEqual(config, before)
        disabled = {"override-project-caches": True, "servers": []}
        self.assertEqual(local["artifacts"], disabled)
        self.assertEqual(local["projects"]["sdk"]["artifacts"], disabled)
        self.assertEqual(local["projects"]["sdk"]["options"], {"arch": "x86_64"})
        self.assertEqual(local["scheduler"], {"builders": 3})

    def test_reject_includes_and_remote_execution(self):
        with tempfile.TemporaryDirectory() as tmp:
            file = Path(tmp) / "config.yml"
            for content in ("(@): another.yml\n", "remote-execution: {url: example}\n"):
                file.write_text(content)
                with self.assertRaises(reprotest.CheckError):
                    reprotest.read_config(file)


class SimulatedRunner(reprotest.Runner):
    """Model cache hits so accidentally comparing a cache hit to itself is caught."""

    def __init__(
        self, args, *, different=False, fail=False, waiting=False, sticky=False
    ):
        super().__init__(args, [], {"commands": []})
        self.calls = []
        self.cached = True
        self.builds = 0
        self.different, self.fail, self.waiting, self.sticky = (
            different,
            fail,
            waiting,
            sticky,
        )

    def bst(self, *command, local=False, capture=False):
        self.calls.append((command, local))
        if command[:2] == ("workspace", "list"):
            return "workspaces: []"
        if command[0] == "show":
            state = (
                "cached" if self.cached else "waiting" if self.waiting else "buildable"
            )
            return f"{KEY}|{state}"
        if command[:2] == ("artifact", "delete") and not self.sticky:
            self.cached = False
        if command[:3] == ("build", "--deps", "none") and not self.cached:
            if self.fail:
                raise reprotest.CheckError("Compiler failed")
            self.builds += 1
            self.cached = True
        if command[:2] == ("artifact", "checkout"):
            path = Path(command[command.index("--directory") + 1])
            path.mkdir()
            (path / "binary").write_text(
                str(self.builds) if self.different else "fixed"
            )
        return ""


class WorkflowTests(unittest.TestCase):
    def make_runner(self, tmp, *extra, **kwargs):
        args = reprotest.parser().parse_args(
            ["target.bst", tmp, "--diffoscope", "never", *extra]
        )
        return SimulatedRunner(args, **kwargs)

    def test_two_actual_builds_and_no_dependency_deletion(self):
        with tempfile.TemporaryDirectory() as tmp:
            runner = self.make_runner(tmp)
            self.assertEqual(runner.check(), 0)
            self.assertEqual(runner.builds, 2)
            for cmd, local in runner.calls:
                if cmd[:2] == ("artifact", "delete"):
                    self.assertEqual(
                        cmd, ("artifact", "delete", "--deps", "none", "target.bst")
                    )
                    self.assertTrue(local)
                if cmd[:3] == ("build", "--deps", "none"):
                    self.assertTrue(local)

    def test_cached_baseline_only_rebuilds_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            runner = self.make_runner(tmp, "--baseline", "cached")
            self.assertEqual(runner.check(), 0)
            self.assertEqual(runner.builds, 1)

    def test_different_outputs_are_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            runner = self.make_runner(tmp, different=True)
            self.assertEqual(runner.check(), 1)
            self.assertEqual(runner.report["changed_paths"], ["binary"])

    def test_missing_dependencies_sticky_cache_and_build_failure_are_errors(self):
        for option in ("waiting", "sticky", "fail"):
            with self.subTest(option=option), tempfile.TemporaryDirectory() as tmp:
                runner = self.make_runner(tmp, **{option: True})
                with self.assertRaises(reprotest.CheckError):
                    runner.check()
                self.assertEqual(runner.builds, 0)

    def test_diffoscope_failure_does_not_become_success_or_difference(self):
        with tempfile.TemporaryDirectory() as tmp:
            runner = self.make_runner(tmp, "--diffoscope", "always", different=True)
            with (
                patch.object(reprotest.shutil, "which", return_value="diffoscope"),
                patch.object(reprotest.subprocess, "run") as run,
            ):
                run.return_value.returncode = 2
                with self.assertRaises(reprotest.CheckError):
                    runner.check()

    def test_error_report_and_exit_status(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "result"
            with (
                patch.object(
                    reprotest.Runner,
                    "check",
                    side_effect=reprotest.CheckError("failed"),
                ),
                patch.object(reprotest, "read_config", return_value={}),
            ):
                self.assertEqual(reprotest.main(["test.bst", str(output)]), 2)
            self.assertEqual(
                json.loads((output / "result.json").read_text())["status"], "error"
            )
            self.assertEqual(reprotest.main(["test.bst", str(output)]), 2)


if __name__ == "__main__":
    unittest.main()
