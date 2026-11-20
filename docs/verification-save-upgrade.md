# Verify a previous-version Player save

Use `save-upgrade` with two reviewed standalone Player payloads that explicitly support a disposable save slot. The previous Player creates a real save, the candidate opens or migrates it, and a fresh candidate process opens it again. The command runs without the Unity Editor or a Unity license.

```sh
uv run --project Server --locked unity-mcp --no-project-context verification-candidate save-upgrade --project /checkouts/game --manifest /trusted/transfer/save-upgrade.json --sha256 <reviewed-manifest-sha256> --output /new/evidence
```

The transfer uses the same layout and admission checks as [a reviewed Player pair](verification-player-reproduction.md). Include the full payload, portable proof and dependency closure under both `baseline/` and `candidate/`. Both builds use the same frozen scenario definition. The previous and candidate project commits and payload fingerprints must differ. The selected project checkout must match the candidate's native inputs. Review and pin the manifest bytes before launching either payload.

## Author the save scenario

Have the game use `UNITY_MCP_VERIFICATION_SAVE_DIRECTORY` in this opt-in scenario for every save read and write. The command creates a new empty `storage/` under the fresh output root and supplies its absolute path to the three child processes. The game owns its save format, migration and validation. Use a test profile with data suitable for logs. The launcher does not set Unity's normal persistent-data path or isolate an arbitrary Player's other I/O. Review both builds for exclusive use of the disposable slot when running this scenario.

The first process should write the legacy save. The candidate should follow its normal upgrade path whenever it finds that format. On restart it should use the same path to open the existing result. Report observable state read back from the actual saved and loaded data, including a value that would expose duplicate application, such as a migration history count or a balance affected by a one-time grant. Pin the expected state and one main checkpoint for each launch.

Use `schema_version: 1` and `kind: "verification_player_pair"`. Set both side statuses to `succeeded`, including when checking whether a faulty candidate is detected. Add this object to the pair manifest. The `restart` object uses the candidate's existing build evidence and supplies its own checkpoint and expected log state.

```json
{
  "save_upgrade": {
    "previous_file": "progress.json",
    "candidate_file": "progress.json",
    "preserve": ["profile", "coins"],
    "restart": {
      "status": "succeeded",
      "checkpoint": "Save checkpoint",
      "action": "wait_state",
      "state": {
        "log_marker": "SAVE_UPGRADE ",
        "expected": {
          "outcome": "reopened",
          "migration_error": null,
          "schema": 2,
          "profile": "fixture-profile",
          "coins": 37,
          "migration_count": 1
        }
      }
    }
  }
}
```

`previous_file` and `candidate_file` are slash-separated paths within the slot. Each named file must be regular, nonempty and at most 32 MiB at its checkpoint. Both fields may name the same file. Each `preserve` key needs the same exact expectation in all three observations. Candidate-specific fields can have different expectations, for example `schema: 1` before the upgrade and `schema: 2` afterward.

## Read the result

`save-upgrade.json` retains the approved build identities, separate native job IDs, checkpoints, state observations and hashes of each run's evidence. Each phase also records the save's presence, size and SHA-256. The save contents stay in the local slot. A completed candidate run that fails its checkpoint or loses the file still gets a restart so the receipt can show the persisted result. The verifier requires the reviewed checkpoint and an independent state observation on both candidate launches to attribute a failure to the migration. Startup failures and missing observations leave the migration result unavailable.

| Exit | Receipt status | Meaning |
| --- | --- | --- |
| 0 | `save_preserved` | All three runs met their expectations. |
| 1 | `migration_failed` | The candidate or restart disagreed with a reviewed expectation. Invalid CLI invocations also exit 1. |
| 2 | `unavailable` | An approved input, previous save, safe slot, complete process result or required state observation was unavailable. |

## Hosted proof

The **Prebuilt Player save upgrade** workflow runs its synthetic command contracts on Windows. Native proof requires a separate approved beta artifact named `prebuilt-save-upgrade`, with `save-upgrade.json` at the transfer root. The native job checks out the verifier at the receiving commit and a second copy of `TestProjects/UnityMCPTests` at the admitted producer commit. The producer commit must contain the candidate fixture project and its Git history must retain the previous fixture commit. The verifier's native MCP package tree must match the candidate build's recorded package commit.

The repository variables are `VERIFICATION_SAVE_UPGRADE_ARTIFACT_ID`, `VERIFICATION_SAVE_UPGRADE_RUN_ID`, `VERIFICATION_SAVE_UPGRADE_PRODUCER_SHA`, `VERIFICATION_SAVE_UPGRADE_ARCHIVE_SHA256`, and `VERIFICATION_SAVE_UPGRADE_MANIFEST_SHA256`. All missing pins produce an `unavailable` admission record on push. A required dispatch fails on missing pins. For a reviewed negative control, dispatch with `expected_result: migration_failed`; the hosted job still requires native restart evidence. The artifact upload excludes the save slot.

The [test-project fixture](../TestProjects/UnityMCPTests/Verification/README.md#previous-version-save-fixture) defines one migration and its negative controls.
