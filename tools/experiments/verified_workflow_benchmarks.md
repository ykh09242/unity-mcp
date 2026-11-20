# Verified workflow benchmarks

## Discovery

Run the same deterministic actor against two Server source directories
with the locked Server Python environment.

```sh
PYTHONPATH=/baseline/Server/src python tools/experiments/discovery_observations.py \
  --source BASELINE_REVISION --output /private/discovery-baseline.json
PYTHONPATH=Server/src python tools/experiments/discovery_observations.py \
  --source CANDIDATE_REVISION --output /private/discovery-candidate.json
```

The actor sends the original English or Korean wording and the same structured
evidence request to MCP search. It follows a permitted reader and checks the
reply against the fixture. The tools-only client uses only tools. The
resource-capable client can read the saved graph template URI. The active
scene comes from a fresh Editor snapshot or active-scene reader. The existing
scenario receipt includes a matching job identity, a terminal result and
separate click and state steps. An older catalog falls back to a lexical
lookup when it rejects the scenario evidence selector.

Counts include lookups and reads up to the first verified result. One lookup
and one read form the replay budget. The server and SDK are real. The Editor,
saved contents, scenario result and read grant are controlled fixtures.
Model selection, language interpretation and native effects require separate
measurements. The [blind selection runner](blind_tool_selection.md) requires
an authenticated host to run live model trials.

In 12 replay pairs against baseline `dae3cf82de328` and the candidate
worktree, every pair reached the expected evidence source. The saved and
current tasks used two calls on each arm. The scenario task used three
calls on the baseline and two on the candidate in all four locale and host
combinations. The observation trials recorded zero write attempts.

## First native scene read

Follow [Install and Connect](../../website/docs/getting-started/install.md).
For a cold trial, start with a fresh disposable project and record whether
the package archive, Editor Library and Python installer cache already
exist. Open a known saved active scene. For a warm trial, reuse the project,
package, server archive and cache and restart the selected Editor and server.

```sh
python tools/experiments/first_scene_read.py --arm warm --cli /installed/unity-mcp \
  --port 8080 --instance "Project@0123456789abcdef" --project /work/Project \
  --scene Assets/Scenes/Fixture.unity --output /private/warm.json
```

The measured time includes doctor discovery and a separate project-guarded
native scene read. To include setup wall time, supply `--setup-command-file`
with a trusted JSON array containing the executable and arguments for a fixture
installer. That command should return once the selected Editor and server are
ready. Without that option the timer starts after setup. Record manual setup
stages when using the installation UI. Use a fresh output file per sample.

Use a disposable project copy for the mismatch control. Set its package pin
to a different immutable server revision from the server you launch. Wait for
the target to connect and run with `--arm mismatched-pin`. The scorer requires
two different observed commits and the doctor's `source_mismatch` and
`restart_client_server` diagnosis. Source metadata from an editable checkout
can be absent. This leaves its pin comparison unresolved despite a verified
scene.

On Windows with Unity `6000.0.69f1`, one sample per arm used package
`2.5.0` embedded from `9e6b8d4afc83` and server `2.5.0` installed
from archive `7b9140dd72c9`. Unity's shared package cache was present.

| Arm | Archive install or cache resolution | Wall time from setup start | Observation |
| --- | ---: | ---: | --- |
| Cold project, empty Python installer cache | 45.684 s | 121.780 s | Matching source and loaded `Assets/DiscoveryInstall.unity`. |
| Warm project, reused caches, both processes restarted | 1.862 s | 15.917 s | Matching source and the same loaded scene. |
| Warm project, package pin switched to `69f299ac63e5` | 1.568 s | 15.674 s | Existing server archive differed. Doctor returned `source_mismatch` and `restart_client_server`. |

The cold time starts with the local package archive and includes its
extraction, the Python archive install, the first Editor import and the
native read. Interactive steps and a remote Unity package fetch were
outside that sample. The mismatch control stops at the doctor diagnosis.

## Paired native comparisons

Use matching Unity 6 Editor versions and independently prepared copies of each
fixture. The official arm uses the `unity mcp` stdio server connected to a
project with the Pipeline package. Use an exact `--project-path` and a fresh
temporary client configuration. Disable shell access in the MCP-only arm.
For shell-enabled trials, grant the same scope to both actors and let the
official actor use `unity command`. [Unity CLI migration and setup](https://docs.unity.com/en-us/unity-cli/replace-mcp-server-unity-cli)

Freeze the task wording, model, effort, host version, grants, fixture inputs
and independent observer before each pair. Alternate product order across
repetitions. Measure wall time from dispatch through the actor's answer.
Count MCP and shell attempts and duplicate writes, including effects detected
by the observer. Capture token counts when the host supplies them.

Collect a private JSON array. Each pair includes `task` (`asset-move`,
`blocked-click` or `interrupted-edit`), `mode` (`mcp-only` or
`shell-enabled`), `repetition`, `fork` and `official`. Use
`status: "unavailable"` with a `reason` for an unavailable arm. It leaves
that pair outside both score denominators.
Fixture run IDs and independent artifact digests must be unique across the
complete experiment. Reusing either removes every affected pair from scoring.

Each completed arm supplies these fields.

- `status: "completed"`, `execution: "native"`, `unity_version`,
  `product_identity`, `model`, `effort` and `host`. The official arm also
  supplies `unity_cli` and `pipeline_package`.
- A unique `fixture_run_id` and SHA-256 strings for `fixture_digest`,
  `permission_digest`, `prompt_digest` and `observer_digest`. These digests
  must match across the pair.
- `mcp_calls`, `shell_calls`, `duplicate_writes`, `elapsed_seconds`,
  `actor_claimed_success` and `tokens` when measured.
- `independent` with the matching `fixture_run_id`, `coverage: "complete"`,
  the distinct `artifact_digest` and the `facts` below. Retain the original
  evidence artifacts. The run observer associates them with the actual
  fixture, Player or Editor before recording the facts.

| Task | Facts |
| --- | --- |
| Asset move | `guid_before`, `guid_after`, `original_ready`, `moved_old_key_ready`, `repaired_key_ready`, `witness_environment`. Observe each matching standalone build. |
| Blocked click | `input_mode`, `blocked_target_first`, `repaired_target_first`, `blocked_transitions`, `repaired_transitions`. Use the same screen position in both configurations. |
| Interrupted edit | `interruption_observed`, `created_guid`, `registered_guid`, `assets`, `entries`, `creation_effects`, `registration_effects`. Read the asset and registration independently after reconnect. |

```sh
python tools/experiments/paired_unity_workflows.py /private/paired-runs.json \
  --output /private/paired-report.json
```

The report includes per-mode sample counts, successes, false passes, duplicate
writes, calls and wall time. Native evidence determines success.
