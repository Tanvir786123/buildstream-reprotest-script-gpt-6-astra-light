# Faster checks of individual BuildStream elements

The expensive part of the existing script is the work it requests, not Python.
It deletes the entire dependency closure, fetches all sources, restores the
pipeline, then rebuilds and compares **every dependency**, including GCC stage 1.

This replacement tests **one element with fixed dependency artifacts**. It keeps
the dependency cache and, by default, builds the target twice from scratch.
This deliberately has narrower coverage than the original tool: a passing target
does not certify that GCC, glibc, or the entire SDK is reproducible.

## Run

Use Python 3.11+ and an existing working BuildStream 2 environment (validated with
2.8.1). Keep the SDK's normal plugin dependencies and sandbox tools installed.
Only one extra Python dependency is required by this script:

```sh
python3 -m pip install -r buildstream-reprotest-script/requirements.txt

python3 buildstream-reprotest-script/reprotest.py \
  -C /path/to/freedesktop-sdk \
  --prepare pull \
  bootstrap/libxcrypt.bst /tmp/libxcrypt-check-001
```

Run these commands from the buildstream-reprotest repository. Alternatively use
the absolute path to `reprotest.py`. The SDK checkout lives elsewhere.
The output directory must be new. Substitute `components/nasm.bst` to test nasm.
Use `--bst /path/to/bst` when BuildStream is not on PATH.

`--prepare pull` is recommended when you want to avoid an unexpected toolchain
build: it pulls missing prerequisites and fails if they are unavailable. Omit it
to allow BuildStream to compile missing dependencies once. With a cold cache and
no usable artifact server, those prerequisites still have to come from somewhere;
changing the implementation language cannot remove that work.

For the fastest check against the existing local/remote artifact:

```sh
python3 buildstream-reprotest-script/reprotest.py \
  -C /path/to/freedesktop-sdk --prepare pull --baseline cached \
  bootstrap/libxcrypt.bst /tmp/libxcrypt-check-002
```

The cached mode compares an existing/pulled target artifact with one fresh local
build. If no target artifact exists, BuildStream builds the baseline too. The
default `--baseline fresh` guarantees two fresh local target builds.

Additional options:

- `--config /path/to/buildstream2.conf`: use your normal cache and scheduler setup.
  Otherwise the usual XDG BuildStream 2 configuration is discovered.
- `-o target_arch x86_64`: forward a project option; repeat as needed.
- `--max-jobs 16`: override parallel jobs within each build. Otherwise keep your
  normal setting; this script does not impose the original 20-job cap.
- `--diffoscope never`: skip detailed diagnostics. The content comparison still runs.
  The default runs diffoscope only for different outputs, if it is installed.
  `--diffoscope always` requires it when differences are found.

## What happens

1. Reject open workspaces to avoid incremental target builds.
2. Prepare build dependencies using the normal local/remote caches; preserve them.
3. Fetch only the target's sources.
4. For each fresh target build, delete only that target's artifact references.
5. Check that the target is buildable before building it. BuildStream's `--deps
   none` can otherwise build missing prerequisites on demand.
6. Rebuild with all user/project artifact remotes disabled, preventing a download
   from masquerading as a rebuild. Remote artifact uploads are disabled in this
   phase too. Keep strict cache keys and verify they do not change.
7. Export both artifacts without integration commands and compare paths, file
   types, SHA-256 content hashes, permissions, empty directories and symlink targets.
8. Run diffoscope only on differences, and write `result.json` with command timings.

Comparison ignores checkout timestamps and ownership, which are not a useful
reproducibility signal for these exports. It does not compare xattrs, hardlink
topology, or BuildStream logs/build trees. Symlinks are not followed. Unsupported
special files cause an error. Embedded timestamps inside files **are** compared.

The output contains `a/`, `b/`, both manifests, and `result.json`; differences may
also produce `diff.html` and `diffoscope.log`. Treat the exported trees as read-only:
the checkouts can hardlink to CAS files. Delete the output directory when finished
to release the space held by these artifacts.

Exit codes: **0** matching target outputs; **1** different outputs; **2** setup,
build, or comparison error; **130** interrupted. Errors never count as a pass.

## Expected savings and limits

On the inspected SDK revision, libxcrypt's closure contains 31 elements, including
GCC stage 1. After dependency preparation, the original does 31 individual
rebuild-and-compare checks. This does two target builds, or one with a cached
baseline. It also avoids throwing away reusable dependencies on every invocation.
That is a reduction in requested work, **not a measured 15-hour-to-X-minute claim**.

With cached dependencies, expect roughly two libxcrypt build times plus startup,
checkout and comparison overhead (one build time in cached-baseline mode). A slow
target still takes time. To certify all 31 elements, each must actually be tested;
use the original full-closure workflow for that broader claim.

The project, sources, options and dependency artifacts must stay fixed during a
run. Do not run concurrent builds, cache deletion or another check using the same
cache. Target cache references are replaced with the last build; dependencies
remain intact. A failed run can leave the target absent or failed in the cache.
Use a separate user config/cache if you need isolation, at the cost of repopulating it.

Remote execution and non-flattened **user** configs with BuildStream YAML directives
are explicitly rejected. Normal project includes/conditionals, such as those in
freedesktop-sdk, are handled by BuildStream as usual. The checker does not vary
time, locale, CPU count or build paths: this is repeat-build testing, as in the
original script, not exhaustive testing of all reproducibility conditions.

## Validation

```sh
python3 -m unittest discover -s buildstream-reprotest-script/tests -v

# Optional real BuildStream integration tests, entirely local and small:
BST_INTEGRATION="$(command -v bst)" \
  python3 -m unittest discover -s buildstream-reprotest-script/tests -v
```

The integration tests create an isolated cache/project with deterministic and
deliberately varying elements. They check fresh and cached baselines, detect real
output differences, and verify the dependency was not rebuilt.
See `ANALYSIS.md` for findings and the actual validation performed here.
