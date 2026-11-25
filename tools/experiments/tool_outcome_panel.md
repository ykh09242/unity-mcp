# Additional native Unity outcomes

Both catalogs completed all eight requests on both revisions. Core used 25 fewer public calls on the final candidate. Its search calls fell from 39 to 13. Full used three more public calls. These are counts from one fresh model session per request, product and catalog.

| Catalog | Completed at `0e57b46ff` | Completed at `d1ba9475b` | Public calls before → after | Failed public calls or missing replies before → after |
| --- | ---: | ---: | ---: | ---: |
| Core | 8/8 | 8/8 | 90 → 65 | 6 → 0 |
| Full | 8/8 | 8/8 | 45 → 48 | 2 → 1 |

The [eight original requests](tool_outcome_originals.json) cover scene lighting, renderer materials and shaders, audio sources, particles, animation controllers, and rigid bodies. Six ask for existing values. Two request a change to one existing component. Each original is delivered once to a fresh agent on each catalog for each product revision. The English-language sample contributes 16 observations per revision alongside the [earlier multilingual study](live_agent_followup.md).

The [matched results](tool_outcome_comparison.json) retain the original requests, actual answers, ordered public calls, supporting values, independent before/after facts and private receipt hashes. Call indices are zero-based. Resource identifiers use `{id}` for each editor-allocated object ID. Elapsed time includes host and editor work. The two runs use different numbers of concurrent editors.

An intermediate candidate at `55b25a74a` received three prompts before a further permission-path change required a new revision. Two sessions finished and the third reached its six-minute limit after 27 public calls. Their receipts remain in the private preliminary run. The matched comparison starts again from the first prompt on the final product revision.

## Measurement

Requests and seeds were fixed before changing the product. Each actor receives the original as its only user message and can choose any advertised operation or resource. Both products use Codex CLI 0.172.0 with `gpt-6.2-sol` and medium reasoning, the six-minute limit and the [same client configuration](live_agent_trial.md#prospective-protocol). Both use Unity 6000.0.69f1, identical private observer and host DLLs, the same 22 project settings files and 44 non-product package lock entries. Every profile pair starts from the same independently measured target facts. The server and Unity package sources are extracted separately from the named Git commit.

A read requires a correct and supported answer plus preservation of measured state. An edit requires the requested native change, an accurate answer and preservation of other measured state. The observer checks the active scene, selection, play state, serialized scene components, project configuration and project assets. For the two edits it excludes the requested serialized property from the protected scene data and permits saving the target scene. The edit can complete in the open editor. The original requests do not specify a save. The grader binds each answer review and its selected public responses to that attempt's complete raw evidence.

## Replay

Use the existing [offline prerequisites](live_editor_fixture/README.md#offline-prerequisites). Set `LIVE_AGENT_TRIAL_DIR` to a fresh dedicated directory and `LIVE_AGENT_PRODUCT_REVISION` to a full commit. Run these commands in the repository's existing Python environment. Choose the same lane count throughout a run.

```powershell
python tools/experiments/tool_outcome_trial.py setup --lanes 4
python tools/experiments/tool_outcome_trial.py preflight --lanes 4
python tools/experiments/tool_outcome_trial.py run --lanes 4
python tools/experiments/tool_outcome_trial.py stop --lanes 4
```

To replay the candidate, point the two environment variables at its separate directory and revision. Pass `--observer BASELINE/bin/OutcomeWorld.dll` to `setup`. The recorded candidate used one lane. Independently review the final answer and successful public results in each private `receipts/<id>/row.json`, considering the exact prompt, native facts, extra assertions and alternative routes. In each run's `reviews.json`, key the review by case ID and record its `fingerprint` from `tool_outcome_report.py`, Boolean `answer_accurate` and zero-based supporting call indices in `evidence`. Generate the paired publication from both private runs.

```console
python tools/experiments/tool_outcome_comparison.py --baseline BASELINE --candidate CANDIDATE --output matched.json
```
