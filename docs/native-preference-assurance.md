# Native preference and version assurance

`tools/run_native_preference_lease.ps1` runs two explicitly supplied isolated
Windows Unity projects. Each must already contain frozen package and owned test
inputs, including identical peer and preference-lease source files. Preparation, package
resolution and licenses remain the caller's responsibility. The controller does
not install Unity, download packages or enable licensed CI. Never use an active
development project, copy a live Library database, or adopt another Editor.
Complete owned-project scene initialization before freezing inputs: Unity may
persist built-in scene-template dependency types on first scene creation. A
runtime settings delta still fails the guard. Archive and inspect its exact
before/after bytes, explicitly prepare only the understood private QA change,
and freeze a fresh manifest before replay; never whitelist arbitrary drift.

```powershell
pwsh -File tools/run_native_preference_lease.ps1 -EditorPath 'C:/Unity/Editor/Unity.exe' -ProjectA 'reports/owned/a' -ProjectB 'reports/owned/b' -Output 'reports/owned/run-001' -InputManifest 'reports/owned/frozen-inputs.json'
python tools/native_assurance.py reports/owned/run-001/receipt.json
```

The manifest uses `{"files":[{"path":"absolute input path","sha256":"..."}]}`.
Hash every frozen authored source, package input and project setting for both
projects, including locally referenced packages outside the project directory.
The controller verifies these files and its scripts before and after execution
and records the Editor binary hash. Library, Temp, Logs and generated settings are
separate cache/output evidence. Paths containing links or junctions are refused.
Use a fresh output directory and two distinct project paths. The startup budget
is configurable for cold imports; normal commands have a 60-second observation
budget and each peer has a 15-minute owned lifetime.

The peer installs fake preference delegates before delayed package recovery.
Actual throttling EditorPrefs are never read or written by the harness. Both native
Editors use the production lease journal and production PID/start-time liveness
check, sharing one persisted fake preference store in the fresh evidence root.
The interaction refresh delegate records calls without changing the actual
Editor's interaction settings. This proves durable lease coordination, not an
OS focus/throttling performance improvement.

The 23 required observations cover overlapping owners, graceful first-owner
exit, forced first-owner exit, last-owner forced death followed by a fresh
Editor, manual-change preservation, an intentionally stale start identity for
a still-live PID, and subsequent admission. The controller terminates only
process handles it created, after checking PID and UTC start ticks immediately
before termination. It records exits, owned survivors and preservation of the
baseline Editors/licensing processes. Logs, command replies and a failure
receipt survive unsuccessful runs. The checker rejects partial, skipped,
simulated-platform, changed-identity or cleanup-failed evidence.

Process ancestry is sampled while waiting for native observations and immediately
before termination. A child is owned only when its observed parent PID and start
time match an owned live process. Baseline processes and licensing processes are
excluded from termination. A crashed project's stale lock is archived and
removed only after the exact owned Editor has exited and a new OS inventory
shows no Editor using that project. Cleanup records observed descendants and
their verified identities; it does not infer ownership from an executable name.
Protected system processes whose exact start time is inaccessible are labeled
`cim_observation_only`; preservation requires matching before/after CIM PID and
creation timestamps. This observation never grants ownership or termination
rights. Missing or changed protected metadata fails preservation.

When embedding the owned package into a previously local-package project, a
manifest may list `generated_package_locks` separately from frozen source files.
The controller records their before/after hashes and accepts only the product
package's local-to-embedded source/path normalization. All package versions and
dependency maps must remain equivalent. The peer also reports its actual loaded
package path, which must be the project's owned embedded package.

`-ClientWindow` holds the two surviving peers after lease scenarios and writes a
nonce-bound `client-window.json`. An authorized external controller may start a
dedicated production server, then send `connect`, `status` and `disconnect`
operations through the existing command files. Connect requires a loopback
WebSocket endpoint and an owned token file inside the evidence root; the token
is never returned. It creates distinct synthetic scenes in the owned projects
and uses production discovery and WebSocket handling with explicit owned
connection options. Discovery uses actual TypeCache entries and production
classification/handler validation in a private cache, bypassing only global
preference default initialization. Existing tool-selection reads and consent
checks retain production behavior; this does not verify preference migration.
The external controller must await disconnect on both peers
before writing `client-complete.json` with the nonce and success result. The
window is bounded to eight minutes; the native controller retains Editor cleanup
ownership. Server/probe process cleanup belongs to its separate controller.

For a failed client phase after completed lease observations, use a fresh output
with `-ClientWindow -ClientOnly`. It retains the same full preflight, two owned
Editor starts, input guards, protected-process observations and cleanup. It
skips lease operations and emits a distinct `unity-mcp-native-client-window-v1`
receipt with zero requested lease observations. A client replay cannot replace
lease phase evidence or turn an earlier failed combined receipt into success.

```powershell
python tools/native_assurance.py reports/owned/client-replay/receipt.json --client-only
```

## Installed-version cohorts

The native group manifest exposes `native-entity-id-66` and
`native-panel-renderer-65`. Run them through the existing isolated native QA
runner on an installed supported Editor. The former writes and reads actual
serialized EntityIds; the latter configures Unity's actual PanelRenderer and
reads its native root through the production adapter with graphics enabled.
Neither fixture substitutes a project lookalike type. Earlier Editors explicitly
skip; the existing required-method result checker rejects skipped required
tests, so a reference compile or an older installed version cannot pass.

These gates follow the [Unity 6.6 API changes](https://unity.com/releases/editor/whats-new/6000.6.0f1)
and [Panel Renderer documentation](https://docs.unity.com/en-us/engine/6000.5/manual/uitoolkits/uielements/uie-support-for-runtime-ui/uie-render-runtime-ui/panel-renderer-component).

Native evidence must state the actual OS, Editor version, graphics API, package
versions, frozen inputs, required methods and process/result receipts. Windows
lease evidence does not establish macOS background reload/socket churn, Linux
Vulkan PlayMode, or an optional Roslyn/AI Assistant package combination. Record
each unavailable cohort explicitly with its missing host, Editor or package;
do not turn its absence into a successful test or silently enable license CI.
