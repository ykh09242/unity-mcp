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
category selectors narrow that scope and are retained in the report. No guessed
impact-based test set is advertised as complete coverage. Zero tests and skipped
coverage cannot pass verification.

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

The current native console tail reports `loss_unknown=true`; even an empty tail
cannot prove a clean interval. Consequently a test profile can show passing native
tests while its overall report remains incomplete. Inspect the stage evidence.
Likewise, a detected project analyzer or source generator is not executed by the
isolated host. Its coverage gap remains visible even when the pinned providers
return no diagnostics, so that required analysis does not become a false pass.
Test stages retain the native `succeeded`/`failed`/`cancelled` status and matching
`native_control` release evidence; available test results alone do not release
the child operation.
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
