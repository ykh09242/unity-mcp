# Source-linked verification profiles

`manage_verification` in the `testing` group composes existing operations into an
owned, source-linked report. Reads expose three code-defined defaults without
creating a project settings file:

| Profile | Stages |
| --- | --- |
| `script-check` | Freshness/import as authorized, required diagnostics, impact when a baseline exists, final freshness. |
| `editmode-check` | Script stages plus source-guarded EditMode tests and non-clearing console observations. |
| `playmode-check` | Script stages plus source-guarded PlayMode tests and non-clearing console observations. |

The test defaults select the complete requested mode. Explicit test, assembly or
category selectors narrow that scope and are retained in the report. Impact
analysis is reported separately from this explicit test selection. Passing
verification requires at least one test and a pass for every selected case.
Skipped or inconclusive cases leave the test stage incomplete. A failed case
makes the stage fail.

## Prepare, run, observe

1. Call `profiles` to inspect the resolved profiles and `byte_hash`.
2. Call `prepare` with `paths` XOR `change_set_id`, and optional `profile_id`
   (`script-check` by default). To compare an edit, request `capture_baseline=true`
   **before editing**, then poll `status` with `preparation_id` until ready.
   Alternatively supply an existing `baseline_id`.
3. Edit the intended source, then call `run` with `preparation_id`. A repeated run
   request for that preparation returns the same `run_id` without replaying work.
4. Read `status` or `report` with `run_id`. `cancel` requests cancellation; inspect
   `native_settled` and child IDs until actual native cleanup is observed.

Preparation freezes selection/profile policy, not source bytes that the user is
about to edit. Execution captures its own current source token. Each dependent
stage rechecks the exact Editor process and live permissions. Ordinary domain
reload may resume observations; another Editor process cannot inherit the run.

Reports include profile/hash, selection, baseline/source identity, stage timestamps,
provider/configuration evidence, test selectors/counts, console coverage, child job
IDs and overall `passed`, `failed`, `stale`, `incomplete` or `canceled` state. Native
compile failure blocks test admission while current-source analysis can still run.
Source drift overrides passing tests with `stale`. Required analyzer/context gaps,
uncertain termination or unknown console loss produce `incomplete`.

Both test profiles arm a threaded Unity application log listener before dispatch.
The admitted native job claims that checkpoint and closes it when its native
control settles. The console stage passes for an intact interval with zero
project Error, Assert or Exception callbacks. It excludes MCP logger messages
and retains up to 12 error previews of 256 UTF-8 bytes each. Observed errors
fail the stage. Overflow marks coverage unknown. Domain reload, expiry, or a
missing or mismatched job checkpoint also leave clean coverage unproven.
Older Editors return retained Console rows with `loss_unknown=true`; observed
errors can still fail verification, and an empty tail remains incomplete.
Likewise, a detected project analyzer or source generator is not executed by the
isolated host. Its coverage gap remains visible even when the pinned providers
return no diagnostics, so that required analysis does not become a false pass.
Test stages retain the native `succeeded`, `failed`, `cancelled` or
`inconclusive` job status and its `native_control` release evidence. On older
Editors, recorded progress or retained results can contradict a passing summary.
The test stage then reports `incomplete`.

A confirmed refusal before the Editor creates a test job releases the verification
slot. If the start request loses its reply, the runner retains the slot until it
can establish what happened to the native job.
A compile child settles when the Editor confirms that the identified request ended
and released ownership, or that it stopped before native dispatch. A completed
synchronous import settles with its own import receipt. Source freshness alone
cannot settle a different request. Concurrent status readers preserve confirmed
settlement.
The runner never clears Console, changes focus, switches scenes, formats code or
automatically fixes issues.

## Explicit project overrides

`set_profile` accepts `profile_id`, `profile`, and `expected_hash` from `profiles`
(`missing` for an absent file). The native store writes only
`ProjectSettings/MCPVerificationProfiles.json`, validates a closed schema, compares
existing bytes and publishes atomically. A sampled conflict refuses the write and
preserves external edits. This is not an OS-level transaction with arbitrary
concurrent external writers. Reads never create the file.

Profiles allow `schema_version=1`, `refresh_policy=never|if_needed`,
`analysis_required`, `diagnostic_scope=all|introduced`, `fail_on` severities
(`error`, `warning`, `info`), `impact=off|if_baseline|required`, `test_mode`, and
`test_names` / `assembly_names` / `category_names` (at most 128 entries each).
Default failure severity is `error`. Introduced-only policy requires a comparable
baseline; missing comparison cannot pass. There are no executable paths, shell
commands, tool names, downloads, formatter stages or arbitrary profile hooks.

Stage deadlines in seconds (default / maximum): freshness 120/300, analysis
120/300, impact 30/120, tests 300/600, console 30/60, final 30/120. The sum bounds the
whole run; nested checks do not reset that deadline. A timed-out observer records
existing child ownership and does not issue another mutation.

At most 64 preparations (16 per owner/project) are retained for 30 minutes; pending
baseline ownership is preserved. Reports are bounded to 512 KiB each, 64 global /
16 completed per owner/project, with 16 active global / 2 active per scope. Settled
reports expire after 30 minutes; unsettled native ownership is never evicted merely
because time elapsed. A server restart or expired handle never reruns work.
An oversized initial report is refused before it occupies a slot. If retained
evidence would push an active report over the limit, `verification_report_limit`
identifies the incomplete evidence and the report keeps its child IDs for follow-up.

## CLI

Each source observation in a report retains the captured `unity_version` and
bounded loaded/output assembly identities. Missing version evidence is unknown.
The diagnostic provider's Roslyn version describes the isolated analysis host;
it does not identify Unity's native C# compiler version.

```sh
unity-mcp --no-project-context --instance 'Game@0123456789abcdef' --format json verification profiles
unity-mcp --no-project-context --instance 'Game@0123456789abcdef' --format json verification prepare --options '{"paths":["Assets/Scripts/Player.cs"],"profile_id":"script-check"}'
unity-mcp --no-project-context --instance 'Game@0123456789abcdef' --format json verification run --options '{"preparation_id":"<returned-id>"}'
unity-mcp --no-project-context --instance 'Game@0123456789abcdef' --format json verification report --options '{"run_id":"<returned-id>"}'
```

The same JSON options, bounds and target restrictions apply as in
[script quality](script-quality.md). Public MCP composition owns preparation/run
handles; the native command implements only profile reads/writes. This repository's
CLI integration does not require Unity's separate official CLI.

## Verify a registered standalone Player

On a local server, set `UNITY_MCP_VERIFICATION_ROOT` to the absolute directory
of builds the operator trusts to execute. Put each project-produced v2 bundle
under `<root>/<candidate_id>/build/` and its registration beside the build in
`candidate.json`. Candidate IDs use the saved scenario name grammar. The selected
Editor's absolute project root must match the registration. Remote-hosted servers
cannot launch these local builds.

The project capture writes a JSON object with the following fields.

| Field | Source |
| --- | --- |
| `schema_version`, `project`, `scenario` | Integer 1, the Editor's absolute project root and the bundle's saved scenario name. |
| `build_id`, `payload_hash` | The v2 bundle's embedded build ID and payload inventory SHA-256. |
| `inputs`, `source_revision` | Map each explicit project-relative input to its exact-byte SHA-256. Hash the UTF-8 concatenation of each sorted path, LF, lowercase digest and LF. Supply the resulting lowercase digest when building the Player. |
| `assets`, `observations` | Optional maps from captured asset paths to Unity GUIDs and project-defined names to scalar observations. Include each identified asset and its metadata in `inputs`. |
| `mappings`, `broader_check` | Each optional mapping has a `path` string and `tests` and `scenarios` arrays of IDs. An unmapped change identifies the configured broader profile or remains outstanding. |
| `package_lock_evidence` | Optional raw and semantic SHA-256 receipt from the Git draft. It distinguishes JSON order changes from dependency changes when both registrations supply the receipt. See [project configuration and additional Player inputs](verification-dependency-closure.md). |
| `state_check` | Optional `log_marker` and `expected` object. Start a dedicated Player log line with the marker, followed by a JSON object. The verifier compares named fields, retaining their JSON types. Missing or contradictory observations leave the check incomplete. |

The capture may retain immutable copies of the selected sources at
`<root>/<candidate_id>/sources/<project-relative-path>`. Reference analysis
reads sources matching the registered digests. Missing historical sources leave
explicit coverage gaps. An input set allows up to 128 Unity script, scene,
prefab, assembly definition, shader, material, controller or asset files,
including their metadata. Live observation reads up to 4 MiB per file and
32 MiB total. Registrations are bounded to 64 KiB.

Call `manage_verification` with `action="prepare"` and the same options accepted
by the CLI. A preparation freezes both manifests, the selected live bytes, the
target identity and the running server's installed build metadata. Its server
commit is null when immutable install metadata is unavailable. The package's
configured server pin is reported separately. Use the returned preparation ID
with `run`, then use the run ID with `status` and `report`.

```sh
unity-mcp --no-project-context --instance 'Game@0123456789abcdef' --format json verification prepare --options '{"candidate_id":"lighting-v2","baseline_candidate_id":"lighting-v1"}'
```

Each run starts a fresh Player job after verifying its source, payload, build ID
and scenario. Reports retain the job ID and artifact directory containing the
native final report, owned process exit evidence and Player log. Passing requires
the selected scenario and every configured required observation to pass.
By default, the report lists the observed native job in `executed_checks` and
other selected IDs in `additional_checks_outstanding`.
Read `data.state` for the verdict. Observing a failed scenario is a successful
report retrieval.

Set `execute_selected_checks=true` on candidate preparation or direct run to
execute the change mappings in the selected Editor after the standalone Player.
The prepared run retains this choice. A mapped scenario that already ran in the
Player uses that job's result. Other saved scenarios run once by project name and
may enter Play Mode or load their declared scenes. Each mapped test uses its
Unity Test Framework test name, a fresh-source admission and an owned console
interval. Set `selected_test_mode` to `EditMode` (default) or `PlayMode` for those
tests. A configured broader profile uses its frozen project settings and the
candidate's registered C# inputs. An unresolved profile, missing input, history
that differs from the live Editor, or unavailable coverage leaves the check
incomplete. Each native operation requires its own permission.

`selected_check_results` retains each check's kind, ID, state, native job ID
when available, and evidence. Tests report their counts and profiles include
the states of their stages and child job IDs. Scenario evidence identifies the
execution environment and native outcome. A failed or unavailable check leaves
later checks eligible while native ownership is settled. Uncertain dispatch
retains its child record, blocks further Editor mutations and remains observable
through the same run's status. Passing requires all selected checks and their
selection coverage to pass. Reuse the preparation handle to observe the same
execution.

For executed selections, `evidence.definition_hash` matches the digest on the
execution record and on native children tagged with the same `check_kind` and
`check_id`. For tests and profiles, this is SHA-256 of the UTF-8 JSON object
with keys `profile` (all resolved settings) and `selection` (the effective
selectors or source paths), with sorted keys, literal Unicode and no whitespace.
Scenarios use the native reproduction definition hash. The separate
`evidence.candidate_source_revision` identifies the candidate inputs used for
that run, so the same definition can be compared across source revisions.
An individual test's `evidence.test_result` records the job ID, full NUnit name
and native case state only when a complete one-case result page matches the
selected name and counts. `test_result_gap` marks a missing exact-case witness.

The default `source_mode="current"` requires live inputs to match the build.
Choose `"frozen"` to replay a historical build after later edits. The report
records whether live bytes match. A subsequent edit to selected inputs during
either run invalidates the comparison. A requested missing baseline leaves the
comparison incomplete. Source observations are sampled at the preparation and
execution boundaries.

## Prepare inputs and compare runs

- Read the [PR native fixture coverage](development/native-case-coverage.md) for the current source SHA, earlier observations and unavailable cases.
- Start with a reviewable Git diff using [`verification-candidate draft`](verification-candidate-draft.md). For large inventories, [capture and assemble input chunks](verification-input-chunks.md).
- To use a build on another machine, [export its proof and verify the receiving machine's files](verification-candidate-portability.md).
- To classify a change or locate an earlier failing build, [compare retained verification and Player reports](development/verification-change-evidence.md).
- For repeated Player trials, [vary the seed and timing or reduce a failing scenario](play-scenario-regressions.md). [Phase diagnostics](phase-diagnostics.md) describes the accompanying timing evidence.
