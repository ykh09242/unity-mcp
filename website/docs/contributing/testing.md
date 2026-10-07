---
id: testing
slug: /contributing/testing
title: Testing And Evidence
sidebar_label: Testing
description: Choose focused checks and distinguish fixture tests, compilation, live Editor execution, and skipped jobs.
---

# Testing And Evidence

Choose checks that observe the changed contract. Markdown-only work needs links, command/schema examples and pins checked, not a product launch. Website presentation needs rendered desktop/mobile checks. Behavior changes need focused regressions, broadening when shared contracts are affected.

## Validate before changing state

Native handlers must validate their inputs even when the Python tool schema already does.
Parse all applicable values and resolve required types, references, indices, dependencies
and destination conflicts before creating settings, registering Undo, changing targets,
making folders or submitting external work. Validate an entire batch envelope before
dispatching its first command. Keep discovery and preparation free of writes; getters
such as `Renderer.material`, `Collider.material` and `MeshFilter.mesh` can allocate native objects too.

Regression tests should place an invalid value after an otherwise valid change and
compare target values, component/native-object inventories, dirty state, asset bytes,
GUIDs and output folders as relevant. Include accepted-value controls. When native
metadata requires an owned temporary object, check cleanup on rejection and avoid
applying any property until instance-dependent validation finishes.

Preserve each tool's documented partial-success boundary. A valid item may still fail
during execution; arbitrary user callbacks and external work are not transactional.
Distinguish those cases from malformed input that can be rejected before the first
effect. Explicitly test mixed-success responses and failed-item cleanup instead of
assuming that every error rolls back the whole request.

GameObject component creation accepts native `m_` aliases when their writable type is
known from reflection. Opaque native-only serialized fields require an explicit add
followed by editing the existing component; the add request must not create a component
just to discover whether such a field exists. Nested reference writes likewise require
a resolvable owner before mutation. Keep controls for the target's GameObject/Transform,
value-type members, indexed collection owners and references supplied by earlier
properties in the same request.

## Python and tooling

Prepare the locked development environment as described in [Dev Setup](./dev-setup.md). From `Server/`:

```bash
uv run --locked --extra dev python ../tools/lint_python.py
uv run --locked --extra dev pytest tests/test_manage_material.py -v -W error
uv run --locked --extra dev pytest tests/ -v -W error
```

From the repository root, using that prepared environment:

```bash
uv run --directory Server --locked --extra dev pytest ../tools/tests/test_update_versions.py -v -W error
```

Pylint checks all server sources and tests, repository tools and their tests, `mcp_source.py`, and `.github/scripts`. The shared policy in `Server/pyproject.toml` enables all error/fatal diagnostics plus selected correctness warnings, including mutable defaults, loop closures, unreachable code and invalid format strings. Formatting, naming and complexity rules are outside this check. Confirmed framework/platform inference limitations and intentional test fixtures have local, explained suppressions; do not globally disable error categories to make a change pass.

The lint entry point runs the server and repository tooling in separate processes with explicit source roots because both contain a package named `tests`. It checks both groups and exits nonzero if either fails. CI enforces this check on the newer interpreter; its analysis targets Python 3.11 and the test matrix still covers both interpreters. There is no warning-only phase, score threshold or saved diagnostic baseline.

CI tests the Python 3.11 support floor and a newer interpreter with locked dependencies. It also installs the candidate server without the lock and checks real tool/resource registration. The pinned distribution gets cold-cache and offline registration checks on Windows, macOS and Linux: `--help` alone does not exercise lazy dependency imports. These checks do not start a listener or connect to Unity. Most Server tests exercise controlled fixtures; a directory named `integration` does not prove live Unity/provider execution. Keep external scenarios explicit and secrets out of test output. Coverage upload is upstream-only; fork jobs retain test artifacts instead.

## Unity compile versus runtime

| Evidence | What it establishes | What it does not establish |
|---|---|---|
| License-free reference compilation | Assemblies compile against selected Editor APIs/platform defines, including optional examples. | Native Editor import, Play mode, rendering, callbacks or provider behavior. |
| Managed regression harness | Behavior of the exercised model/fixture and source contract. | All native Unity side effects. |
| Licensed EditMode/PlayMode run | Executed tests in the named Editor/project. | Other versions/platforms or unexecuted workflows. |
| Skipped license-gated job | No execution evidence. | A pass. |

The matrix's exact versions/profile roles live in `tools/unity-versions.json`; workflow triggers and permissions live in `.github/workflows/`. Record the actual run/revision rather than promising that every PR runs every job. Reference-only compilation is separate from licensed Unity tests.

### Feature and platform boundaries

| Surface | Native verification needed |
|---|---|
| PanelRenderer | Unity 6.5 component attachment, root reload, live element edits and rendering. Adapter fixtures and older-version compilation cover compatibility contracts only. |
| Input simulation | Supported Input System package/handling mode, dynamic/fixed update release, pause/exit cleanup and target application response. Direct uGUI dispatch does not establish physical pointer hit testing. |
| View recording | Visible Game/Scene View, codec availability, explicit job polling, final playable file and interrupted-job cleanup on the named Windows/macOS Editor. Linux/headless capture is unsupported. |
| Play readiness | Scene-load or simulation-frame progress, reload recovery and cancellation. Readiness is distinct from rendered output and application async initialization. |

Linux/Vulkan test hangs and macOS background reload recovery require reproduction on those platforms. Diagnostic heartbeat ages, resume state and retry information help identify where progress stopped; they do not establish that an OS-specific stall is fixed or that an inaccessible native modal dialog has been identified. Preserve save/reload dialogs and unsaved state during reproduction.

`ManageRecordingInteractiveTests` is an opt-in public-tool recording scenario. It requires
`UNITY_MCP_RECORDING_INTERACTIVE_QA=1`, an owned `.unity-ci/.../project` fixture with uGUI,
and an Editor launched without `-batchmode` or `-nographics`. Run its EditMode filter
without `-quit`; the test runner exits when complete. The tests exercise rendered
Game/Scene frames, explicit stop, automatic completion, pause interruption and cleanup.
They retain MP4/PNG/JSON evidence under the fixture's `Captures/Recordings/` directory.
Decode the MP4 independently (for example with ffprobe/ffmpeg) and verify the green
Screen Space Overlay marker in the Game View recording. A valid container alone does
not prove that the intended rendered content was captured.

Local shutdown tests use the actual ASGI authentication/routes and controlled Uvicorn
owners to exercise admission races, unknown outcomes and deadlines without binding a
port, reading live tokens or signaling a process. Report a separate owned-process run
if asserting that a real managed server exited; ASGI tests establish the handshake,
not native process termination.

## Local Unity tests

When native testing is deliberately in scope, prepare the local test project's package source as described in [Dev Setup](./dev-setup.md#connect-a-local-unity-package), open `TestProjects/UnityMCPTests`, and use **Window > General > Test Runner**. Add tests under its existing EditMode/PlayMode assemblies.

Compatibility changes can use the repository-root version-check helpers:

```bash
tools/check-unity-versions.sh
tools/check-unity-versions.sh --full
```

These helpers can launch installed Editors; `--full` runs native tests. Missing installations are not runtime passes. Inspect the helper's current flags and report exact versions that actually ran. [Unity compatibility](../architecture/unity-compat.md) documents shim policy.

### Local headless test harness

Only when intentionally testing native runtime behavior:

```bash
python tools/local_harness.py
```

This boots a Hub-licensed Editor and runs selected smoke/EditMode/PlayMode legs. `--legs`, `--project-path`, `--reuse`, `--keep-alive`, and `--no-warmup` control the scenario. Exit codes: 0 pass, 1 blocking regression, 2 bridge/setup failure, 3 compile failure, 4 unavailable license, 5 missing Editor. Do not run it solely because a guide changed.

## Generated references and docs

Tool/resource schemas are generated by `tools/generate_docs_reference.py`; do not hand-edit generated content outside authored example markers. [Docs Workflow](./docs.md) covers generation/check mode, page links and site validation. Optional `tools/install-hooks.sh` hooks are conveniences, not substitutes for reviewing the diff or reporting executed checks.

## Report honest results

Include changed scope, exact commands, interpreter/Editor versions, source revision, results/skips and remaining limits. Do not hide external/vendor warnings or describe compile evidence as runtime certification. Load/stress/provider/Blender scenarios require deliberate execution scope and are not implicit prerequisites for every change. See the [stable release evidence](https://github.com/ykh09242/unity-mcp/releases/tag/ykh09242-v1.0.0) for that immutable snapshot, not a guarantee for the moving branch.
