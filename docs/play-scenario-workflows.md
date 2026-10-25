# Repeatable scenario workflows

Use a saved profile to run an existing verified Player build and retain separate regression
decisions. Use complete history ledgers, semantic UI snapshots and reviewed interaction drafts to
develop that profile. These workflows build on [scenario authoring](play-scenarios.md),
[Player assurance](play-scenario-player-assurance.md) and
[regression evidence](play-scenario-regressions.md).

Examples contain project-specific build, scene, object and artifact paths. Replace them with
verified values. Select the intended Unity instance for live capture, recording or scene editing;
offline comparisons and analysis read only their explicit local inputs.

## Run a regression profile

Save this as `profiles/menu-soak.json`:

```json
{
  "schema_version": 1,
  "name": "Reviewed menu soak",
  "execution": {
    "kind": "soak",
    "build_directory": "../build/menu-player",
    "expected_build_revision": "reviewed-build-revision",
    "timeout_seconds": 300,
    "cleanup_wait_seconds": 360,
    "soak": {
      "iterations": 30,
      "max_runtime_seconds": 3600,
      "failure_policy": "stop",
      "retain_reports": 4
    }
  },
  "baseline": {
    "registry": "../reports/baselines.json",
    "name": "menu-stable",
    "policy": "../policies/memory-budget.json"
  },
  "analyze": true
}
```

```sh
unity-mcp --format json play-scenario regression-run profiles/menu-soak.json \
  --output-dir reports/menu-regression-001
```

The output directory must be new and outside the immutable build. Profile build, registry, policy
and reference paths resolve relative to the profile file, independently of the shell's current
directory. Profiles use strict schema version 1, reject unknown fields and are bounded to 64 KiB.
The runner uses existing Player launchers and requires verified payload provenance; it
does not build a Player or infer a trusted revision. `expected_build_revision` and
`expected_build_id` optionally require exact existing build identities.

`execution.kind` selects one launcher:

| Kind | Selected execution fields |
| --- | --- |
| `run` | Ordinary Player; `repeat_count` 1-10, default 1. |
| `soak` | One native Player process; required `soak` object with 2-1,000 iterations and existing soak options. |
| `session` | Existing sequential session runner; `session_mode`, `iterations`, `batch_size`, `max_runtime_seconds`, `failure_policy`, `retain_reports`. |

All kinds accept `graphical`, `timeout_seconds`, `cleanup_wait_seconds` and expected build identity.
Options for another kind are rejected rather than ignored. A named baseline gate is allowed only
for `soak`; the exact name must already be pinned. Its reference is admitted before execution and
revalidated when comparing. The runner freezes the selected policy in the new output, uses the
existing memory budget gate and never registers, updates or repins a baseline.

For a graphical ordinary run, a profile may select up to 16 named visual checkpoints. For example,
save this as `profiles/menu-visual.json`:

```json
{
  "schema_version": 1,
  "name": "Menu visual checks",
  "execution": {
    "kind": "run",
    "build_directory": "../build/menu-player",
    "graphical": true,
    "repeat_count": 1
  },
  "visual_checks": [
    {
      "baseline_report": "../reports/reviewed/run.json",
      "policy": "../policies/visual-policy.json",
      "checkpoint": {
        "name": "menu-ready",
        "stage": "main",
        "step_index": 0,
        "iteration": 1
      }
    }
  ],
  "ui_checks": [
    {
      "baseline": "../reports/ui-baseline.json",
      "candidate": "../reports/ui-candidate.json",
      "geometry_tolerance": 0.5,
      "require_occlusion": false
    }
  ],
  "analyze": true
}
```

Visual gates require `kind: "run"` and `graphical: true`; the selected iteration must fit
`repeat_count`. The baseline capture and explicit policy are admitted before launch. Policy copies
are retained and the existing exact checkpoint/hash/dimension comparison determines each visual
gate. A graphical launch does not guarantee capture; unavailable native checkpoints cannot pass.

`ui_checks` compares up to 16 pairs of already saved semantic snapshots. These gates are labeled
`saved-ui-N`: the profile does not capture those snapshots from its current Player or establish
that they describe the newly executed build. Capture and review each artifact explicitly before
including it. `require_occlusion: true` currently produces unavailable evidence.

`analyze` defaults to true. Ordinary runs use the saved-run analyzer; soaks and sessions use the
complete-history analyzer below. Missing or incomplete analysis is an unavailable gate. A complete
analysis may describe observed failures; its successful computation never overrides the native
execution gate.

The new output retains `profile.json` and selected frozen policies. `profile.json` stores resolved
absolute input paths, preserving their meaning when read from the
output directory. It does not make those external input artifacts portable.

The new output also retains native execution artifacts below
`execution/`, independent gate decisions in `regression.json`, JUnit in `junit.xml` and a portable
`report.html`. When the selected native run/soak report can be exported, `execution.html` preserves
its own outcome and diagnostics. Session summaries remain session evidence rather than invented
ordinary run reports. Keep source JSON, journals and process receipts alongside HTML.

| Regression exit | Meaning |
| --- | --- |
| `0` | Execution and every selected gate passed. |
| `1` | Native execution or a selected gate failed. |
| `2` | Required evidence or a selected operation was unavailable. |
| `130` | Owned execution was cancelled. |

Failures take precedence over unavailable downstream evidence; cancellation retains its exit code.
Native/client errors stay attached to their original execution. Output creation or export failure
does not become a passing profile. A report file alone is not a success receipt.

## Analyze complete soak and session history

```sh
unity-mcp --format json play-scenario analyze-history \
  reports/session/session.json reports/soak/client-soak.json \
  --output reports/history-analysis.json
```

Select at most 16 explicit summary files. Session sources use `session.json`; native soak sources
may use their owned `soak.json`, `client-soak.json` or parent `run.json`. Admission reads the matching
request, identity and bounded journal evidence. It does not discover histories, rerun a process,
repair source evidence or treat the last retained successful child as the parent outcome.

Session journals require schema-2 immutable session-start metadata and complete admissions/outcomes,
with at most 2,000 rows, 4,096 bytes per row, 8 MiB total and a 64 KiB summary. Soak histories retain
the existing complete native/process/client admission contract: at most 1,000 rows, 16,384 bytes per
row and 16,384,000 journal bytes. Torn or contradictory ledgers, missing process evidence and
unsupported versions produce incomplete evidence.

Results preserve counts, original outcomes, failure frequencies, first-failure information, streaks
and early/late failure rates. Soak streaks count iterations; session streaks count process outcomes,
not inferred ordering inside a compact batch. A compact batch may lack an exact first failure
iteration; that value stays null when the ledger cannot establish it. Counts and earlier failures
remain visible after retained report eviction.

Groups remain source-local and separate session modes and recorded definition/build/payload
identities. Session journals do not currently retain every child's native environment. They cannot
establish compatible cross-run history: `compatible_history` and `intermittent` remain false, with
an explicit evidence note. The analyzer does not fill that gap from one retained successful report
or merge unrelated sources into a trend.

Exit 0 means complete analysis, including observed failures; exit 2 means incomplete or
unavailable evidence. `analyze-flakiness` remains the separate bounded analyzer for up to 64 explicit
ordinary `run.json` files. It still rejects soak/session parent evidence; use `analyze-history` for
those complete ledgers.

## Capture and compare semantic UI trees

```sh
unity-mcp --instance "MyProject@<hash>" --format json play-scenario capture-ui-snapshot \
  --framework ugui --target "Canvas/Menu" --max-nodes 200 \
  --output reports/ui-baseline.json
unity-mcp --instance "MyProject@<hash>" --format json play-scenario capture-ui-snapshot \
  --framework uitoolkit --target "UI/MenuDocument" --max-nodes 200 \
  --output reports/toolkit-ui.json
unity-mcp --format json play-scenario compare-ui-snapshots \
  reports/ui-baseline.json reports/ui-candidate.json \
  --geometry-tolerance 0.5 --output reports/ui-comparison.json
```

The live `capture_ui_snapshot` tool takes `framework`, one explicit `target` hierarchy path or
instance ID, and `max_nodes` (1-1,000). It captures a bounded complete tree, including inactive
nodes, without clicking controls. A saved artifact is at most 2 MiB. Hierarchy depth is bounded to
128, sibling count to 1,000, node text to 8,192 characters and classes/components to 64 each.

Nodes include semantic identity/parent/order, type, text, components/classes, activity, render
eligibility, available interaction state, alpha and geometry. IDs combine loaded scene identity
with exact hierarchy names. Duplicate roots/siblings are ambiguous and fail capture. Saved scene
paths are preferred; unsaved scene-name identities carry an explicit limitation. Give UI Toolkit
siblings unique names when unnamed elements of the same type would otherwise be ambiguous.

Default live captures use snapshot schema version 2 and require matching updated native plugin and
server versions. Each node adds a closed `state` object. Supported built-in controls expose Toggle
Booleans; Slider/SliderInt numeric value and range; Dropdown/TMP_Dropdown/DropdownField indices;
at most 64 sorted ListView selection indices; input read-only/password/redacted flags; and scroll
position. uGUI scroll positions use normalized coordinates; UI Toolkit uses panel-point offsets.
Unsupported/custom controls have empty state. No generic property dump, option/data-source text,
selected object or input string value is included in typed state.

Ordinary label/button text remains available. Recognized editable input contents, including ordinary
non-password fields, and directly owned text/placeholder renderers are omitted **before text
getters**. Redacted input nodes require `text=null`; no value, mask, length, hash, caret or selection
range is collected. Changing only redacted contents intentionally produces no content diff. This
does not identify secrets that application scripts copy into unrelated labels, names or metadata.
The other UI read tools have separate contracts; this is a semantic-snapshot privacy guarantee.

uGUI privacy preflight inventories loaded InputField/TMP_InputField owners, including inactive
owners and renderers outside the selected subtree. It processes at most 4,096 total entries and
retains at most 4,096 owner IDs and 8,192 renderer IDs. An incomplete inventory refuses the whole
capture before text publication. The native inventory call can allocate its result before these
managed caps are checked; neither native allocation nor elapsed time is bounded by those counts.
UI Toolkit uses physical input ancestry/native input-role evidence for privacy while preserving
the existing content-container order for snapshot identities. Invalid state, more than 64 selected
indices or unavailable privacy evidence produces no comparable partial tree or saved artifact.

uGUI geometry is a world-XY axis-aligned bounding box, not projected screen pixels. UI Toolkit uses
panel points from `worldBound` and requires a built, attached root with finite positive layout.
Comparison requires matching framework, mode, loaded-scene set, target, resolution and evidence
method. It reports added/removed nodes and changes in text, state, classes/components, sibling order
and geometry. `--geometry-tolerance` is 0-10,000 in the snapshot's declared geometry space.

Offline version-1/version-1 comparisons remain supported. Version-2/version-2 comparisons report
a control-only change as `fields: ["state"]`; numeric control values are exact and are not covered
by geometry tolerance. Mixed snapshot versions are `not_comparable` (exit 2), with no automatic
state upgrade or privacy claim for legacy files. The comparison report itself retains schema
version 1. Existing legacy artifacts are neither rewritten nor retrospectively certified private.

These are semantic/layout diagnostics. `visible` describes render eligibility, and component state
does not prove input reachability or event delivery. uGUI renderer culling is partial evidence;
pixel overlap, clipping and actual occlusion proof are unavailable. `--require-occlusion` makes that
requirement explicit and currently exits 2 rather than passing with guessed visibility. For rendered
pixel regression checks, use the named screenshot comparison in the regression guide.

Offline comparison makes no Unity/network calls: exit 0 is `same`, exit 1 is `changed`, exit 2 is
`not_comparable`. Capture and comparison outputs must be new; existing files are preserved.

## Save bounded input actions

After the mandatory initial scene load, an input step can inject a bounded virtual-device command:

```json
{
  "name": "Press jump",
  "action": "input",
  "input": {
    "action": "key",
    "key": "Space",
    "frames": 30
  },
  "wait_frames": 2
}
```

The nested action is `key`, `mouse`, `touch`, `gamepad`, `wheel` or `release_all`. Press is the default state; move and
release are explicit. Press leases accept `frames` 1-36000, default 1. `wait_frames` is 1-36000, default
1, and waits for actual game Input System updates rather than condition polls. `release_all` is
immediate and does not accept `wait_frames`. Keys use named controls; mouse buttons are `left`,
`right` or `middle`; touch IDs are 1-10. A position contains two finite screen-pixel coordinates
between 0 and 1,000,000. Move/touch requirements and unrelated fields are validated before saving.

For a deliberate drag, author mouse press with a long enough bounded lease, then move and release
steps; touch supports the same sequence. Releases are also enforced on failure, cancellation,
interruption and iteration completion, and on Editor pause, domain reload or Play Mode exit. An
30-second inactivity lease prevents a stalled host from retaining injected state; actual held-input updates
can renew it up to one hour.

Input steps require the optional Input System package 1.7 or newer and Input System Package/Both
active handling. Manual-update or legacy-only injection is explicitly unavailable. Each host owns
dedicated virtual devices; focus/settings still apply and device-paired `PlayerInput` bindings may
require pairing. Injection is not a claim of OS hardware input or a universal controller setup.
Old Input Handling supports uGUI event dispatch and legacy observation; Unity's public API does not
inject recorded raw state into `UnityEngine.Input`. Both-mode virtual devices affect Input System
consumers only. No tool changes the project's handling mode or focuses OS windows.

Input has no object selector. The existing stable `target_id` is reused by `wait_object` and
`click_ui` when a game exposes a registered target; it was not introduced by this workflow. Use
those actions for semantic readiness/targeted UI interaction and input actions for bounded device
commands. The Editor authoring surface and persisted definition share the same validation.

## Record supported interactions into a draft

Open a saved scene, enter Play Mode and explicitly start recording supported uGUI Button pointer
clicks and observed active-scene transitions:

```sh
unity-mcp --instance "MyProject@<hash>" --format json scenario-recording start menu-recorded \
  --max-events 31 --duration-seconds 300
unity-mcp --instance "MyProject@<hash>" --format json scenario-recording status RECORDING_ID
unity-mcp --instance "MyProject@<hash>" --format json scenario-recording \
  checkpoint RECORDING_ID menu-ready
unity-mcp --instance "MyProject@<hash>" --format json scenario-recording stop RECORDING_ID
```

The MCP surface is `manage_scenario_recording` with `start`, `status`, `checkpoint`, `stop` or
`cancel`. `start` returns immediately; `status` observes once. `max_events` is 1-127 (default 31) and
duration 1-3600 seconds (default 300). The initial scene load is retained separately from the event
count. Stopping saves a new named scenario with `draft: true` and a `recorded-draft` tag; it refuses
to replace an existing scenario. Cancelling discards the session without saving.

Recording observes supported left-button/touch pointer clicks without suppressing or redispatching
them. Clicks become `click_ui` with `click_mode: "raycast"`, preserving a reliable existing
`target_id` or exact hierarchy path; ambiguous selectors are diagnosed and omitted. Scene transitions become
observed `wait_scene` steps. It does not infer readiness conditions, delays, state assertions or
reset policy. Available Input System or Old-mode legacy observers capture delivered key, mouse, touch
and wheel changes; `status.input_observation` identifies source, frame clock and replay limits.
UI Toolkit, gamepad and programmatic `onClick` callbacks need manual authoring. Additive-scene interactions and objects created between observer scans may be
omitted with diagnostics.

Old-mode recordings retain the `recorded-legacy-input` tag. Their raw steps cannot replay into
`UnityEngine.Input`; keep target-based `click_ui` and remove overlapping raw pointer steps during
review. The recorder retains legacy uGUI target clicks with a duplication diagnostic. Wheel units,
keyboard layout and timing require review before any cross-backend replay. Existing Unity focus
and background policy determines which physical input is observed; the recorder never changes it.
See [input observation and limitations](INPUT_AND_LOGGING.md) for supported controls and cleanup.

The uGUI observer reconciles every two seconds and after scene loads, with hierarchy changes coalesced
to at most four scans per second and at most 512 loaded Buttons. Event or
duration limits stop observation without silently saving. Explicitly stop to save or cancel to
discard the in-memory draft. Play Mode exit, reload and quit release observers. After reload, the
last bounded status remains inspectable, but that session cannot resume or save; review its returned
definition manually if needed.

An explicit checkpoint adds a future named screenshot request to the draft, initially 1280x720;
it does not capture a reference image or create an image assertion while recording. Review the
request and actual graphical resolution before running. Duration/event bounds and interruptions
remain visible in status; partial observations do not become a complete game-flow claim.

Open the saved draft in **Play Scenarios** and review selectors, recorded order, scene readiness,
expected state, cleanup and repeat reset policy. Clear **Recorded draft (review required)** and
save with `draft: false` only after that review. Drafts are blocked by preflight, execution and Player
bundle admission. Old definitions that omit `draft` keep their existing behavior and hashes.

## Preview scene edits and apply one Undo group

Save an explicit array as `scene-edits.json`:

```json
[
  {
    "target": "Player",
    "search_method": "by_path",
    "component_type": "Transform",
    "property": "localPosition",
    "value": [1, 2, 3]
  }
]
```

```sh
unity-mcp --instance "MyProject@<hash>" --format json scene-edit preview scene-edits.json
unity-mcp --instance "MyProject@<hash>" --format json scene-edit apply \
  --preview-id PREVIEW_ID --undo-name "Move reviewed player"
```

`manage_scene_edits(action="preview", edits=[...])` resolves explicit targets and returns before/after
values plus `preview_id` without changing scene values. Default search is `by_id`; `by_name` and
`by_path` are explicit and reject ambiguous matches. Apply the returned one-attempt token or use
`scene-edit apply --edits-file scene-edits.json`, with `expected_old` supplied on every direct edit.
Do not supply both a token and an edit list.

Requests contain 1-64 edits and at most 65,536 characters. At most 32 previews are retained; tokens
expire after ten minutes and disappear on reload/restart. Applying validates target/component,
scene/path/parent, component index and targeted old values before mutation. Drift or expiry consumes
that attempt and requires a fresh review. The Undo label is 1-80 characters.

Only existing supported built-in serialized properties are allowed:

| Component | Fields |
| --- | --- |
| `Transform` | `localPosition`, unit quaternion `localRotation` `[x,y,z,w]`, `localScale`, with supported `m_` aliases. |
| `BoxCollider` | `m_Center`, `m_Size`, `m_IsTrigger`, `m_Enabled`. |
| `SphereCollider` | `m_Center`, `m_Radius`, `m_IsTrigger`, `m_Enabled`. |
| `Rigidbody` | `m_Mass`, `m_UseGravity`, `m_IsKinematic`. |

Full `UnityEngine.*` component names are accepted too. Custom components, object references,
structural changes, asset/build/package edits, prefab stages and Play Mode edits are unsupported.
Supported scene-instance prefab overrides are recorded.

Successful apply uses one named native Undo group. Failure attempts compensating rollback of the
recorded native component state and reports `rollback_verified` and any `rollback_error`. This is
not a transaction over arbitrary callbacks, external effects, filesystem contents or dirty flags.
Keep scene save/review explicit; preview and Undo are not evidence that a game scenario passed.


## Compare repeated UI rows with explicit item IDs

Use `capture_ui_snapshot(identity_map=[{"element_path":"/0","id":"item-A"}, ...])`
to opt into schema version3 for either uGUI or Toolkit. The map is caller data for
this request, not a persistent registry. Current paths select Transform children for
uGUI and snapshot/content-container children for Toolkit; the root is not assignable.
Provide stable nonsecret item IDs and update their path associations after reorder or
rebinding. Valid stale logical associations cannot be detected. Same-key recreation
under the same effective ancestor path retains comparison identity; reparenting or
root identity changes can change it.

The CLI accepts a local file such as `row-ids.json`:

```json
{"identity_map":[{"element_path":"/0","id":"item-A"},{"element_path":"/1","id":"item-B"}]}
```

```sh
unity-mcp --instance "MyProject@<hash>" play-scenario capture-ui-snapshot \
  --framework uitoolkit --target "123" --identity-map-file row-ids.json --output current.json
```

Replace123 with the current target's actual ID/path. The file is a closed object,
at most256KiB, checked before dispatch. Map assignments are1-999 entries and no more
than `max_nodes - 1`; total path/ID characters are at most65536. Each non-root path has
at most127 canonical indices0-999 and512 characters. IDs are1-128 ASCII letters,
digits, period, underscore, colon or hyphen, compared exactly. Null/empty maps,
duplicate IDs/paths, unknown fields, nonexistent paths and remaining ambiguity refuse.
The reader also bounds JSON depth/nodes before decoding. Existing output files are
preserved; a failed capture or wrong-version response produces no new artifact.

Omitting the map preserves schema2 and existing ambiguity refusal. Mapped captures
use reserved `@id:` segments and version3 identity evidence. Their state/privacy fields
remain version2-compatible, but v2/v3 comparisons refuse instead of guessing matches.
The comparator matches exact mapped IDs: reorder can report sibling/geometry changes
without false added/removed rows, and rebinding to a different item reports removed
and added identities. These IDs are comparison evidence, not editing handles.
See [UI Toolkit target semantics](UI_TOOLKIT_TARGETS.md) for partial-map and selector
limitations. Never use editable contents or credentials as explicit IDs.


## Consume a completed JSON/JUnit export

Suite and Player exports retain their fixed JSON and `junit.xml` filenames and publish
`report.complete.json` last. The completion manifest records a generation ID and SHA-256
for every member, including unchanged native `run.json` bytes. Old members are withdrawn
before new members are published; interruption cannot leave a completed mixed pair. Failed
publication restores the previous reports and completion manifest. A blocked rollback retains
its private recovery directory and reports its path instead of discarding the originals.

A consumer that can run concurrently with export must require the completion manifest,
verify each member's hash, and re-read the unchanged manifest after reading all members.
The server environment provides `cli.utils.play_scenario_artifact_io.read_completed_reports`
for this contract; it raises `OSError` while a writer is active or a generation is incomplete
or changes during the read. Retry after publication finishes. Missing members or a missing
completion manifest are incomplete output, even when an older export used only fixed filenames.
