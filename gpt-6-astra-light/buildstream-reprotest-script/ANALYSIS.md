# Findings and validation

## Why the original check takes so long

The original `buildstream_reprotest/__init__.py` requests a complete dependency
audit, even when you pass a single library:

- `restore_initial_state()` calls `artifact delete --deps all`, discarding local
  artifact references for the entire closure. Repeated runs must pull or rebuild
  those artifacts again. It then fetches sources for the entire closure.
- `bst_check_reproducibility_v2()` lists `--deps all` and iterates over every
  element. Each iteration deletes and rebuilds that element without artifact
  remotes, so GCC stage 1 is intentionally rebuilt as a test subject.
- Every comparison runs diffoscope, including identical trees.
- The rebuild phase caps jobs at 20. Whether increasing that helps depends on
  available RAM and CPU; it cannot eliminate unwanted compiler builds.

This is primarily a scope/cache policy issue. Rewriting the process orchestration
in C, assembly or JavaScript would leave the expensive compiler work in place.

I also found a correctness issue: `handle_results()` checks only the
`non_reproducible` list when deciding success. A run containing comparison errors
can therefore print “Project is reproducible.” The replacement distinguishes
different output from build/comparison errors in both JSON and exit status.

## SDK example inspected

Cloned separately at `/tmp/freedesktop-sdk-reprotest` from
`https://gitlab.com/freedesktop-sdk/freedesktop-sdk.git`.

- Revision: `b4942f234f5828539502bc02b2ad2fd605a0ce42`.
- Date inspected: 2026-09-24.
- BuildStream: 2.8.1, Python: 3.12.3.
- `bst show --deps all --format '%{name}' bootstrap/libxcrypt.bst` returned
  **31 elements**, including the target. Full list: `libxcrypt-dependencies.txt`.
- GCC stage 1 is an explicit build dependency in `elements/bootstrap/libxcrypt.bst`.
- The SDK declares `https://cache.freedesktop-sdk.io:11001` as an artifact cache.
  Availability of this particular pipeline's artifacts was not tested.

No libxcrypt/GCC compilation was performed, and there is no measured SDK wall-clock
speedup to report. A full cold SDK build would obscure the distinction between
one-time preparation and repeated target testing. The script records those phases
separately in `result.json` so real runs can be compared.

## What the replacement saves

| Phase | Original | Replacement |
| --- | --- | --- |
| Dependency preparation | Delete closure, fetch all sources, restore closure | Retain artifacts; pull/build only prerequisite selection |
| Reproducibility subjects | All 31 elements | The requested target |
| Fresh test builds after preparation | One per element in closure | Two target builds, or one against an available cached baseline |
| Expensive diagnostics | diffoscope on every element | diffoscope only when manifests differ |
| Missing prerequisite policy | Compile as needed | Optional `--prepare pull` refuses prerequisite compilation |

The original comparison for a dependency can use a baseline built locally during
preparation or pulled from a remote. The replacement's default deliberately uses
two fresh local target builds; `--baseline cached` provides the analogous
cached-versus-fresh target comparison.

The expected warm-cache cost is approximately preparation/check overhead plus
two target builds and two checkouts/hashes. Cached-baseline mode removes one
target build when the baseline is available. It does **not** prove reproducibility
of skipped prerequisites. Retain a full dependency audit when that is the question.

## Tests actually run

- Ten unit tests passed, covering content and metadata differences, symlinks,
  unsupported file types, remote disabling, preserved configuration, fresh versus
  cached baselines, failure propagation, and protection against cached rebuilds.
- One real BuildStream integration test passed with four scenarios:
  deterministic fresh builds; a cached baseline with cached prerequisites and no
  remote; deliberately varying output under the same cache key; and pull-only
  preparation with missing dependencies.
- The integration test checks unchanged dependency artifact references and the
  absence of dependency build-start events on subsequent runs. BuildStream touches
  reference mtimes for cache bookkeeping, so mtimes alone are not rebuild evidence.
- The final combined run passed **11 tests in 14.722 seconds**. This measures a
  small synthetic test suite, not libxcrypt or an SDK speedup.
- A separate real `import`-element smoke check also passed with two fresh target
  builds and a retained dependency.
- Ruff lint and formatting checks passed for the solution directory.

The real fixtures use local BuildStream plugins that create artifacts through
BuildStream's virtual filesystem, with no downloaded toolchain. They exercise
cache invalidation and artifact comparison but do not validate the SDK's compiler
sandbox or remote cache connectivity. Diffoscope's error path is unit-tested;
the integration fixtures use manifest comparison without diffoscope.

## BuildStream semantics used

BuildStream's documented [command interface](https://docs.buildstream.build/master/using_commands.html)
supports selecting build dependencies separately from the target. `build --deps
none` still permits missing prerequisites to be built on demand, which is why the
replacement checks readiness before each fresh build.

The [user configuration reference](https://docs.buildstream.build/master/using_config.html)
documents artifact remote overrides and scheduler controls. The replacement
preserves user settings and project options, then disables both global and
per-project artifact remotes for fresh builds. It avoids private BuildStream
Python APIs and operates through the CLI.
