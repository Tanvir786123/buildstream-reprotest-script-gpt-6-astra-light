#!/usr/bin/env python3
"""Check one BuildStream 2 element, reusing its dependency artifacts."""

import argparse
import copy
import hashlib
import json
import os
import re
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError


class CheckError(RuntimeError):
    pass


def read_config(filename):
    if filename:
        path = Path(filename).expanduser().resolve(strict=True)
    else:
        root = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
        path = next(
            (
                root / n
                for n in ("buildstream2.conf", "buildstream.conf")
                if (root / n).exists()
            ),
            None,
        )
    data = YAML(typ="safe").load(path.read_text()) if path else {}
    data = data or {}
    if not isinstance(data, dict):
        raise CheckError("BuildStream user configuration must be a mapping")

    def validate(value):
        if isinstance(value, dict):
            # Do not silently reinterpret includes relative to a temporary file.
            if any(k in value for k in ("(@)", "(?)", "(<)", "(>)", "(=)")):
                raise CheckError(
                    "Use a flattened user config (no YAML directives) with --config"
                )
            for item in value.values():
                validate(item)
        elif isinstance(value, list):
            for item in value:
                validate(item)

    validate(data)
    if data.get("remote-execution") or any(
        p.get("remote-execution") for p in data.get("projects", {}).values()
    ):
        raise CheckError(
            "Remote execution is unsupported: use a local-build user config"
        )
    return data


def local_config(data):
    data = copy.deepcopy(data)
    disabled = {"override-project-caches": True, "servers": []}
    data["artifacts"] = copy.deepcopy(disabled)
    for project in data.get("projects", {}).values():
        project["artifacts"] = copy.deepcopy(disabled)
    return data


def manifest(root):
    """Compare exported content, types, modes and link targets, not checkout times."""
    result = {}

    def visit(directory):
        for path in sorted(directory.iterdir()):
            info = path.lstat()
            mode = stat.S_IMODE(info.st_mode)
            relative = str(path.relative_to(root))
            if stat.S_ISLNK(info.st_mode):
                result[relative] = ["symlink", os.readlink(path)]
            elif stat.S_ISDIR(info.st_mode):
                result[relative] = ["directory", mode]
                visit(path)
            elif stat.S_ISREG(info.st_mode):
                with path.open("rb") as stream:
                    digest = hashlib.file_digest(stream, "sha256").hexdigest()
                result[relative] = ["file", mode, info.st_size, digest]
            else:
                raise CheckError(f"Unsupported special file in artifact: {relative}")

    visit(root)
    return result


class Runner:
    def __init__(self, args, configs, report):
        self.args, self.configs, self.report = args, configs, report

    def bst(self, *command, local=False, capture=False):
        cmd = [
            self.args.bst,
            "--config",
            str(self.configs[local]),
            "--directory",
            str(self.args.project),
            "--no-colors",
            "--no-interactive",
            "--strict",
        ]
        if self.args.max_jobs:
            cmd += ["--max-jobs", str(self.args.max_jobs)]
        for name, value in self.args.option:
            cmd += ["--option", name, value]
        cmd += list(command)
        print("+ " + shlex.join(cmd), file=sys.stderr, flush=True)
        started = time.monotonic()
        proc = subprocess.run(
            cmd, stdout=subprocess.PIPE if capture else None, text=True, check=False
        )
        self.report["commands"].append(
            {
                "command": list(command),
                "local": local,
                "seconds": time.monotonic() - started,
                "exit_code": proc.returncode,
            }
        )
        if proc.returncode:
            raise CheckError(
                f"BuildStream failed ({proc.returncode}): {shlex.join(command)}"
            )
        return proc.stdout or ""

    def info(self):
        output = (
            self.bst(
                "show",
                "--deps",
                "none",
                "--format",
                "%{full-key}|%{state}",
                self.args.element,
                local=True,
                capture=True,
            )
            .strip()
            .splitlines()
        )
        if len(output) != 1 or "|" not in output[0]:
            raise CheckError("Expected exactly one target from bst show")
        key, state = output[0].split("|", 1)
        if not re.fullmatch(r"[0-9a-f]{64}", key):
            raise CheckError(
                "Target has no resolved full cache key; check source refs/workspaces"
            )
        return key, state

    def checkout(self, name):
        path = self.args.output / name
        self.bst(
            "artifact",
            "checkout",
            "--deps",
            "none",
            "--no-integrate",
            "--hardlinks",
            "--directory",
            str(path),
            self.args.element,
            local=True,
        )
        return path

    def fresh(self, key):
        self.bst("artifact", "delete", "--deps", "none", self.args.element, local=True)
        current_key, state = self.info()
        if current_key != key:
            raise CheckError("Target cache key changed during the check")
        # --deps none can still build missing prerequisites. Fail before that happens.
        if state != "buildable":
            raise CheckError(
                f"Target is {state!r}, not buildable after deletion; "
                "dependencies/sources must be cached and no workspace may be open"
            )
        self.bst("build", "--deps", "none", self.args.element, local=True)
        current_key, state = self.info()
        if current_key != key or state != "cached":
            raise CheckError(
                "Fresh build did not produce a successful artifact at the expected key"
            )

    def check(self):
        workspace = (
            YAML(typ="safe").load(
                self.bst("workspace", "list", local=True, capture=True)
            )
            or {}
        )
        if workspace.get("workspaces"):
            raise CheckError(
                "Close open workspaces before checking (incremental builds are not fresh)"
            )

        if self.args.prepare == "build":
            self.bst("build", "--deps", "build", self.args.element)
        else:
            # Missing remote prerequisites fail here instead of starting a toolchain build.
            def dependencies_cached():
                states = self.bst(
                    "show",
                    "--deps",
                    "build",
                    "--format",
                    "%{state}",
                    self.args.element,
                    local=True,
                    capture=True,
                ).splitlines()
                return all(state.strip() == "cached" for state in states)

            # bst artifact pull errors without a remote, even if everything is local.
            if not dependencies_cached():
                self.bst("artifact", "pull", "--deps", "build", self.args.element)
                if not dependencies_cached():
                    raise CheckError(
                        "Some build dependencies remain uncached after pulling"
                    )
        self.bst("source", "fetch", "--deps", "none", self.args.element)

        if self.args.baseline == "cached":
            self.bst("build", "--deps", "none", self.args.element)
        key, _ = self.info()
        self.report["cache_key"] = key
        if self.args.baseline == "fresh":
            self.fresh(key)
        a = self.checkout("a")
        self.fresh(key)
        b = self.checkout("b")

        started = time.monotonic()
        left, right = manifest(a), manifest(b)
        changes = [
            name
            for name in sorted(left.keys() | right.keys())
            if left.get(name) != right.get(name)
        ]
        self.report.update(
            comparison_seconds=time.monotonic() - started,
            changed_paths=changes,
            status="different" if changes else "reproducible",
        )
        for name, value in (("manifest-a.json", left), ("manifest-b.json", right)):
            (self.args.output / name).write_text(json.dumps(value, indent=2) + "\n")
        if changes and self.args.diffoscope != "never":
            binary = shutil.which("diffoscope")
            if not binary:
                if self.args.diffoscope == "always":
                    raise CheckError("diffoscope was requested but is not installed")
                self.report["diffoscope"] = (
                    "not installed; see changed_paths and manifests"
                )
            else:
                with (self.args.output / "diffoscope.log").open("w") as log:
                    proc = subprocess.run(
                        [
                            binary,
                            "--html",
                            str(self.args.output / "diff.html"),
                            "--exclude-directory-metadata=recursive",
                            str(a),
                            str(b),
                        ],
                        stdout=log,
                        stderr=subprocess.STDOUT,
                        check=False,
                    )
                self.report["diffoscope_exit_code"] = proc.returncode
                if proc.returncode not in (0, 1):
                    raise CheckError(
                        f"diffoscope failed ({proc.returncode}); see diffoscope.log"
                    )
                # The manifest is authoritative: diffoscope may ignore mode differences.
        return 1 if changes else 0


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("element", help="One .bst element; only this element is tested")
    p.add_argument(
        "output", type=Path, help="New output directory (includes both artifact trees)"
    )
    p.add_argument("-C", "--project", type=Path, default=Path.cwd())
    p.add_argument("--bst", default="bst", help="BuildStream 2 executable path")
    p.add_argument(
        "--config", help="BuildStream user config; otherwise uses normal XDG discovery"
    )
    p.add_argument(
        "-o",
        "--option",
        nargs=2,
        action="append",
        default=[],
        metavar=("NAME", "VALUE"),
    )
    p.add_argument(
        "--max-jobs",
        type=int,
        help="Jobs per build; default preserves BuildStream config",
    )
    p.add_argument(
        "--baseline",
        choices=("fresh", "cached"),
        default="fresh",
        help="fresh: two clean builds; cached: cached/pulled baseline versus one clean build",
    )
    p.add_argument(
        "--prepare",
        choices=("build", "pull"),
        default="build",
        help="build: pull/build prerequisites; pull: fail if unavailable, never compile them",
    )
    p.add_argument(
        "--diffoscope",
        choices=("auto", "always", "never"),
        default="auto",
        help="Detailed diagnostics on differences only; auto uses it when installed",
    )
    return p


def main(argv=None):
    p = parser()
    args = p.parse_args(argv)
    if not args.element.endswith(".bst") or args.element.startswith("-"):
        p.error("element must be a .bst name")
    if args.max_jobs is not None and args.max_jobs < 1:
        p.error("--max-jobs must be positive")
    args.project = args.project.expanduser().resolve()
    args.output = args.output.expanduser().resolve()
    report = {
        "element": args.element,
        "project": str(args.project),
        "scope": "target-only",
        "baseline": args.baseline,
        "prepare": args.prepare,
        "status": "error",
        "commands": [],
    }
    started = time.monotonic()
    try:
        # Exclusive creation prevents stale successful reports and accidental overwrite.
        args.output.mkdir(parents=True, exist_ok=False)
    except OSError as exc:
        print(f"Cannot create output directory: {exc}", file=sys.stderr)
        return 2
    try:
        config = read_config(args.config)
        with tempfile.TemporaryDirectory(prefix="bst-reprotest-") as tmp:
            configs = [Path(tmp) / "prepare.yml", Path(tmp) / "local.yml"]
            yaml = YAML()
            for path, data in zip(configs, (config, local_config(config))):
                with path.open("w") as stream:
                    yaml.dump(data, stream)
                path.chmod(0o600)
            code = Runner(args, configs, report).check()
    except (CheckError, OSError, ValueError, YAMLError) as exc:
        report.update(status="error", error=str(exc))
        print(f"ERROR: {exc}", file=sys.stderr)
        code = 2
    except KeyboardInterrupt:
        report.update(status="error", error="Interrupted")
        code = 130
    finally:
        report["total_seconds"] = time.monotonic() - started
        (args.output / "result.json").write_text(json.dumps(report, indent=2) + "\n")
    print(
        f"{args.element}: {report['status']} (target only); {args.output / 'result.json'}"
    )
    return code


if __name__ == "__main__":
    sys.exit(main())
