# Held-out native Unity workflow study

Compare a pinned fork and the official Unity CLI MCP with two independently
supplied Unity 6 projects. The scorer requires an actual connected official
provider, a host call trace and project-specific evidence from a native observer
for each matched actor pair. This repository supplies the study tooling.
Official-arm results require a provider-run study.

## Prepare before exposing tasks to actors

Use two clean, full-history Git checkouts with the same exact Unity 6 Editor
version. For each of the three [native workflows](verified_workflow_benchmarks.md#paired-native-comparisons),
select an actual asset, UI target or registration in each project. Prepare
the initial conditions, explicit actor permissions and an independent observer
for that project. Keep the observer, calibration data and allocation manifest
outside actor access. A blank project with a copy of the tuning fixtures does
not establish transfer to an unfamiliar project.

The custodian inventories every project and native receipt used for tuning or
rehearsal, including earlier names and imported or squashed copies. Produce an
identity record for a clean version of each tuning project with this command.

```sh
python tools/experiments/heldout_unity_workflows.py identify /private/tuning-project
```

Keep a private JSON specification with the following fields. Paths resolve
relative to the specification. Use nonsecret files for digests.

| Field | Contents |
| --- | --- |
| `model`, `effort`, `host` | Exact model snapshot, reasoning effort and host/version shared by both arms. |
| `products.fork` | Object with an immutable `product_identity`, including the installed package/server revision. |
| `products.official` | Object with `product_identity`, actual `unity_cli` version and `pipeline_package` version. |
| `permissions` | Object mapping `mcp-only` and `shell-enabled` to files containing their exact effective grants. Disable shell in the former. Grant equivalent workspace access in the latter. |
| `training` | Object with `inventory_complete: true`, `projects` (array of identity records) and `used_evidence` (array of historical session IDs, fixture run IDs, native capture IDs and SHA-256 digests of captures). Empty arrays mean no additional known entries. |
| `projects` | Two objects with `path`, custodian declaration `excluded_from_tuning: true` and `fixtures`. |
| `projects[].catalogs` | For each permission mode, map `fork` and `official` to files containing a JSON array of MCP tool names actually advertised after applying the product-specific filters. The scorer pins each arm's catalog and verifies the independently captured list at dispatch. |
| `projects[].fixtures` | Object keyed by `asset-move`, `blocked-click`, `interrupted-edit`. Each value has `initial_state` (path to canonical native starting-state export), `observer` (path to a self-contained observer or immutable bundle including the observation rubric) and `context` with English `en` and Korean `ko` project-specific instructions. |
| `repetitions` | Positive integer, default 1. Each repetition plans 24 matched pairs across the two projects, three workflows, two languages and two permission modes. |

Localize the same target names, target conditions and permitted changes in each
prompt. Describe the user's desired result. Omit product names, tool routes,
answers, native checks and performance hypotheses. Freeze an observer rubric
for each task defining the first useful *returned observation*, such as a
GUID and lookup state for the named asset, the first raycast hit at the agreed
coordinate, or an independently readable registration for the intended GUID.
An invocation, an acknowledgement or a search result merely naming a tool
does not establish that observation.

The native initial-state JSON must contain `project_guid`, `project_revision`,
`unity_version`, `task` and a nonempty `facts` object covering the starting
scene and the task's relevant targets and conditions. Use the same byte-stable
export on every fresh run. Archive the permission definitions and observer
bundle alongside the specification.

```sh
python tools/experiments/heldout_unity_workflows.py prepare /private/spec.json /private/new-study
```

Retain the printed manifest SHA-256 outside the study directory. Preparation
pins committed project revisions, Unity versions, Git root commits, Assets
trees, Unity product GUIDs, prompts, grants, observer and initial-state bytes.
Shared ancestry, matching content trees or matching product GUIDs disqualify
a candidate. The repository's own lineage and checked-in test project Assets
trees are automatically excluded. A reauthored copy with rewritten identities
requires the custodian's provenance check. A clean source alone cannot establish
the live scene, Play Mode, cache or package state.

## Dispatch and collect native evidence

Run the private manifest's cases in the recorded arm order. Each allocation has
two random IDs, `id` for the original actor and `replay_id` for the teammate.
Use fresh isolated conversations, Unity instances and disposable clean project
copies. The provider applies only the pinned product installation to each
checkout, checks its native connection and records the loaded project identity.
For the official arm, launch the actual `unity mcp` process with the exact
project path and installed Pipeline package. Unity identifies this server as
the supported replacement for the in-Editor assistant's MCP server. Record
the installed CLI and connected Pipeline versions. Confirm the tool-group
filters and Editor compatibility for those exact pins.
[Unity's MCP migration guide](https://docs.unity.com/en-us/unity-cli/replace-mcp-server-unity-cli)
and [CLI release notes](https://docs.unity.com/en-us/unity-cli/release-notes/2026/november)
and [Pipeline registry metadata](https://packages.unity.com/com.unity.pipeline).

Freeze a common capability policy for both arms in each mode. Map each
product's tool names and group filters to that policy. Inspect the full
paginated MCP tool listing after configuration and store the names in the
catalog files. Verify dynamic discovery, generic execution tools, resource
readers, filesystem access and any permitted shell launcher against the same
grants. Keep the run outside the comparison if the provider cannot enforce
equivalent grants. In shell-enabled mode the actor may use `unity command`
under the same host policy as the fork. For every run, export the normalized starting
state independently and compare its bytes with the frozen initial-state file.
Include relevant asset identity, scene and target state in that export.

Deliver only the case's `prompt` and ordinary granted tools to the actor.
The trusted broker records dispatches, replies, the final success claim and
exact handoff. Count attempts including failed, pending, search, resource,
and permitted shell calls. Exclude operator setup and independent observer
calls. The teammate gets the original task and the original handoff on a
fresh copy of the same revision, settings and verified starting state.
Use a different person with a fresh isolated host session and record whether
their attempted reproduction reached the native outcome.

The broker and evaluator produce one private UTF-8 JSON bundle per trial.
Keep native captures, exports, source logs and protocol transcripts in
operator-controlled storage. The CLI verifies correspondence and checksums.
The trusted collector and the independent observer establish provenance.
An actor-generated JSON report cannot serve as either collector.

| Bundle field | Producer and contract |
| --- | --- |
| `catalog` | Broker's complete list of advertised MCP tool names for this project, product and mode at dispatch. Put its sorted JSON array digest in `host.catalog_digest`. |
| `host` | Trusted broker. Record `trial_id`, `session_id`, `fixture_run_id`, pseudonymous `operator_id`, `execution: "native"`, `coverage: "complete"`, `host`, `model`, `effort`, `product_identity`, `project_revision`, `product_guid`, `unity_version`, `fixture_digest`, `permission_digest`, `observer_digest`, `prompt_digest`, `handoff_digest`, Boolean `actor_claimed_success`, nonnegative `elapsed_seconds` and optional `tokens`. Official runs additionally record actual `unity_cli`, `pipeline_package`, `provider_command: "unity mcp"` and `connected_pipeline`. Use observed values from the installed process, resolved grants, dispatch and native setup. |
| `native_capture` | Exact UTF-8 JSON text from the independent project observer. Include `trial_id`, `fixture_run_id`, `product_guid`, `project_revision`, `unity_version`, `product_identity`, `fixture_digest`, `observer_digest`, unique native `capture_id`, `coverage: "complete"`, observed `duplicate_writes` and the [task's native facts](verified_workflow_benchmarks.md#paired-native-comparisons). Official captures also bind the connected `unity_cli`, `pipeline_package` and `provider_session_id` to the host session. Observer facts come from the independent Editor/Player, including actual Player observations for the asset move. |
| `calls` | Trusted ordered broker trace. Each row contains `kind` (`mcp` or `shell`), `dispatch` and `return` event ordinals. Use one contiguous, zero-based event sequence for all dispatches and returns together. A returned call has its actual JSON `reply`. A pending call has `return: null` and no reply. |
| `review` | Independent evaluator. Include `observer_id`, `trial_id`, `coverage: "complete"`, `artifact_digest` of the exact native capture text, `trace_digest` of the calls array and `valid_observations`. Review all replies against the frozen rubric and native truth. For each valid reply record its `return` ordinal and `reply_digest`. An empty array records a complete trace with no valid observation. |
| `handoff` | Exact handoff text emitted by the original actor or actually delivered to the teammate, as recorded by the broker. An empty handoff is represented by the empty string. |

Text digests use SHA-256 over UTF-8 bytes. Digests of JSON values use the
module's `json_digest` convention, Python JSON with sorted keys, compact
separators, UTF-8 and preserved Unicode. The prompt and handoff digests cover
the exact text, without an added newline. Preserve separate IDs for every
session, fixture and native capture. Keep credential material and raw user
records outside these artifacts.

## Admit and score

```sh
python tools/experiments/heldout_unity_workflows.py admit /private/new-study ACTOR_ID /private/actor.json --pin MANIFEST_SHA256
python tools/experiments/heldout_unity_workflows.py admit /private/new-study REPLAY_ID /private/teammate.json --pin MANIFEST_SHA256
python tools/experiments/heldout_unity_workflows.py score /private/new-study --pin MANIFEST_SHA256 --output /private/report.json
```

Admit each actor before its teammate. Use a fresh output path for each report.
When a provider, permission, project or native observer is unavailable, leave
the slot empty and retain the operator's reason in the private study log.
The report preserves all planned pairs and excludes unmatched or reused
evidence. Each permission mode reports matched actor successes and false
success claims with denominators. The first-observation median counts every
attempt sent by the time the earliest accepted reply arrived, including
overlapping dispatches. Its sample size and the count with no observation
are explicit. Teammate reproduction uses its own matched denominator, the
original handoff and the same native success predicate. Pair rows retain
project index, task, language and repetition for audit. With no scored
official/fork pair, arm metrics are null. The report records its manifest pin
and matched actor/replay coverage per project. The `complete` flag requires
both stages for every planned pair.

## Three-arm held-out comparison

Set `"design": "three-arm"` in a fresh specification and use the same
`prepare`, `admit` and `score` commands above. Existing two-arm manifests
keep their original study design. The three-arm design allocates 24 matched
triplets per repetition, with a fresh actor and teammate for each arm (144
slots in total). Within each project, complete blocks of six cases use all
six arm orders once, shuffled before dispatch.

| Additional preparation input | Contract |
| --- | --- |
| `reader_wheel` | Path to the standalone `unity-mcp-evidence` wheel actually distributed for this study. Archive the wheel and retain its SHA-256 through installation. |
| `operators` | Separate `actor` and `replay` lists of unique pseudonymous participant IDs. Each panel has 12 participants per phase per repetition. Supply one panel to rotate each person through all six project/task combinations, two panels for three combinations per person, or six panels to use every participant once. |
| `reader_tool` | Exact name of the broker's restricted read-only MCP adapter for this wheel. In MCP-only mode it may invoke the standalone reader with allowlisted arguments over the same admitted inputs. In shell-enabled mode expose the wheel's CLI under the existing shell grant. |
| `projects[].catalogs[mode]["official-reader"]` | Export of the actual catalog for the official provider with the reader available. The MCP-only catalog adds exactly `reader_tool` to the official catalog. The shell-enabled catalog matches the official catalog. |
| `projects[].fixtures[task].raw_inputs` | Nonempty object mapping neutral labels to original evidence files supplied to every actor and teammate in this case. Preparation hashes the files and records labels and digests. |

Use the same pinned CLI and Pipeline for `official` and `official-reader`.
Preparation shuffles each operator panel, assigns distinct people within
each project/task (across languages, modes, arms and repetitions), and
counterbalances arm assignments across new tasks. Dispatch the
`actor_operator` and `replay_operator` named in the allocation. Select
adjudicators independently from both participant rosters. For a matched
case, each adjudicator may review only one arm, including its actor and
replay. Admission and scoring check the recorded identities against the
operator schedule and other receipts for that case.
Only the reader arm gets the standalone wheel. Run the fork arm against the
pinned fork installation. Recreate the same clean project, grants, raw
bytes, distribution mechanism, model snapshot, effort, prompt and observer
on each dispatch. Under MCP-only grants give all arms the same read-only
access to the original raw files. Keep observer-only records out of that
packet. Restrict the reader adapter to the same files and disable arbitrary
shell, network and additional writes. Keep missing activations unfilled when
the broker cannot enforce these controls. Preparation and admission reject
actor files whose raw digests match any project's observer bundle or the
custodian's prior evidence inventory, including renamed copies.

Before each reader-arm dispatch, have the broker run the following command
with the interpreter actually serving the wheel. It compares the installed
Python sources, package metadata and console entry point against the wheel.

```sh
/path/to/reader/python tools/experiments/heldout_unity_workflows.py probe-reader /path/to/reader.whl TRIAL_ID HOST_SESSION_ID
```

In every host bundle record `raw_inputs` (the observed label-to-digest map
of bytes delivered to this session), and `reader_probe` (that command's JSON
for the reader arm, JSON null for the other arms).
Record provider connection fields for **both** official arms,
including the native capture's `provider_session_id`. Each broker trace
event additionally records numeric `dispatch_seconds` and, when returned,
`return_seconds` from the monotonic instant of prompt delivery. All events
in an invocation share the same clock origin. Include time before the
first dispatch and before the first independently accepted reply.

The report retains all planned case triplets, with missing or reused slots
and their reasons. It computes false successes per matched actor triplet
and teammate reproduction per matched replay triplet, overall, by project
index and by permission mode. First-observation medians use the same
triplets for all arms. `paired_seconds_minus_official_median` takes the
median of within-case time differences. Read the matched, unmatched,
no-observation and planned counts alongside these rates. An absent third
arm leaves the comparison unscored. Only an independently observed
provider-run study can establish a comparative result.

To examine native official-provider files independently, use the
[official session evidence importer](../../docs/development/official-unity-evidence.md).
