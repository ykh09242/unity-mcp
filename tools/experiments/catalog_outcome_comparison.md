# Repeating the 71 original requests after the catalog changes

A later run repeats these originals with isolated terrain settings and tests additional languages and tool families. [Host-neutral release evaluation](agent_outcome_validation.md)

## Final shipping package

The shipping package `e3c0c6450b39cf8c5523dfa8bec22f25991f559d` completed **121/142** original requests, compared with **122/142** at baseline and **123/142** on the intermediate package. The shipping package pins server `5f0466871529c7d209133cb342f357c505c6b87b`, which updates the startup instructions for clients with fixed tool inventories. Each of the 142 fresh actor sessions started once with the original prompt, seed, model, six-minute limit and catalog. All produced independent native snapshots before and after the turn. The run retained every first attempt and 136 final answers.

| Catalog | Baseline passes | Intermediate passes | Shipping passes | Shipping reads | Shipping edits | Graded failures | Incomplete | Unobserved |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Core | 59/71 | 58/71 | 57/71 | 36/43 | 21/28 | 9 | 4 | 1 |
| Full | 63/71 | 65/71 | 64/71 | 43/43 | 21/28 | 6 | 1 | 0 |

Two baseline failures became shipping passes. Full correctly classified the Slovak and Telugu 2-by-2 terrain regions using collider probes, retaining both intermediate gains. Three baseline passes became incomplete. Core returned no final answer for the Finnish camera field of view or Latvian audio edit, and the audio override remained enabled. Full applied the Greek collider edit with protected state preserved but returned no final answer. Every baseline pass that returned a shipping final answer passed again.

Three other baseline failures changed classification. Core timed out on the Latvian and Telugu terrain reads. The core Urdu terrain edit timed out before any public call, so its action was unobserved and its target remained a hole. All six timeouts logged a model-stream disconnect between 3.174 and 108.045 seconds after actor startup. All 15 graded failures were terrain requests. Full completed six of six terrain reads and core completed zero. Every terrain edit target remained a hole. All 129 native tool preferences matched after the run, with `manage_terrain` disabled throughout the fixture checks.

| Public calls / unsuccessful replies | Baseline | Intermediate | Shipping |
| --- | ---: | ---: | ---: |
| Core | 718 / 57 | 731 / 21 | 725 / 19 |
| Full | 794 / 71 | 838 / 41 | 847 / 40 |

Every public call received a recorded reply in all three cohorts. On the shipping package, all 35 `manage_tools.describe` and 53 `manage_audio.read_import_settings` calls succeeded. See the [shipping per-case evidence](catalog_outcome_shipping_results.json).

```console
python tools/experiments/catalog_outcome_comparison.py --shipping
```

## Intermediate package

Across the two catalogs, verified completion moved from **122/142 to 123/142**. Full gained two terrain reads. Core lost one collider edit when the model stream disconnected before the edit. Every first attempt produced native before-and-after snapshots. Of the 142 turns, 140 returned final answers and two reached the six-minute limit.

| Primary outcome | Core before | Core after | Full before | Full after |
| --- | ---: | ---: | ---: | ---: |
| Completed requests | 59/71 (83.10%) | 58/71 (81.69%) | 63/71 (88.73%) | 65/71 (91.55%) |
| Reads completed | 37/43 | 37/43 | 41/43 | 43/43 |
| Edits completed | 22/28 | 21/28 | 22/28 | 22/28 |
| Graded failures | 12 | 12 | 8 | 5 |
| Incomplete turns | 0 | 1 | 0 | 1 |

The full-catalog agents correctly classified the Slovak and Telugu 2-by-2 terrain regions using collider probes at cell centers and boundaries. This brought full to six verified terrain reads out of six. Core left all six terrain reads unclassified. All twelve terrain edit targets remained holes. Seventeen completed answers failed the rubric, each on a terrain request with a recorded `native_disabled` diagnostic. The 129 persisted tool-enable preferences had identical hashes before and after the run. Each original had one primary observation per catalog and revision.

## Public calls

| Calls recorded in the primary turns | Core before | Core after | Full before | Full after |
| --- | ---: | ---: | ---: | ---: |
| Public tool calls and resource reads | 718 | 731 | 794 | 838 |
| Unsuccessful replies | 57 | 21 | 71 | 41 |
| Calls without a recorded reply | 0 | 0 | 0 | 0 |

| Operation, unsuccessful replies / calls | Core before | Core after | Full before | Full after |
| --- | ---: | ---: | ---: | ---: |
| `search_unity_tools` | 15/229 | 0/174 | 21/212 | 9/154 |
| `manage_tools.describe` | 10/45 | 0/19 | 7/37 | 0/18 |
| `manage_audio.read_import_settings` | 8/40 | 0/27 | 8/37 | 0/31 |
| `manage_input_actions.read` | 0/18 | 2/20 | 0/18 | 5/23 |

Five InputAction reads used the unsupported keyword `asset_path` for the correct file. One omitted the required `path`. One used a valid path with an explicit instance that the server temporarily could not locate. All seven subsequently succeeded using the existing `path` contract. Full also issued 120 `query_physics.raycast_all` calls, up from 74. An unsuccessful reply can be followed by a successful task, and multiple successful calls can still leave the requested change undone.

## Transport sensitivity checks

Two primary turns ended without an answer and logged a model-stream disconnect well before the deadline. Each received one fresh diagnostic turn using the same prompt, seed, catalog and candidate in a separate disposable Editor. These results are reported alongside the fixed 142-turn comparison.

| Original | First stream warning | Primary outcome | Separate replay |
| --- | ---: | --- | --- |
| Core Finnish collider edit | 22.4 s | Timed out after two public calls. The trigger stayed off. | Passed. The existing collider became a trigger and all protected state was preserved. |
| Full Telugu terrain edit | 38.1 s | Timed out after four public calls. The target stayed a hole. | Returned an answer. The native terrain tool was disabled and the requested cell stayed a hole. |

## Matched conditions

The [previous study](live_agent_followup.md) measured product `fe9fb9a70e15c86351f3f83de11308f6ee6f0d6f`. This repeat measured `d1ba9475b5b29016f3744969a5c149673d3cd21d` on 25 November 2026. Each original in 17 languages retained its UTF-8 prompt, fixture seed, catalog order and six-minute limit. Every case used a fresh Codex CLI 0.172.0 session with `gpt-6.2-sol` at medium reasoning. The four registered-package requests used a dedicated offline registry Editor. Four regular disposable Editors handled the other 138 requests. Each agent worked against a single selected Unity 6000.0.69f1 project. The frozen rubric checks the answer, its supporting public calls, the requested native effect where applicable, and preservation of unrelated state. [Original protocol](live_agent_trial.md#prospective-protocol)

Both catalogs retained the same public memberships, with 31 tools on core, 90 on full, and 33 resources and 13 templates on each. Every fixture matched the earlier 22 project settings and 44 non-product package lock entries. The corrected collider observer and the registered-package observer matched their respective earlier fixtures. Unity allocates collider IDs and imported audio asset GUIDs per Editor, so preservation uses each turn's own starting state. The cached whole Cinemachine registry index had changed. Its complete official 2.6.11 version entry, package bytes and installed metadata matched the earlier fixture. Input System's cached index matched exactly. Seven commissioning turns on a superseded product revision were retained separately before the final product was pinned.

The [redacted results](catalog_outcome_comparison_results.json) identify each first attempt and supporting wire replies by digest. They include observed state changes, failure stages, native capability diagnostics, fixture fingerprints and separately identified transport replays. The [previous per-case results](live_agent_followup_results.json) contain the originals and baseline observations. Run the repository verifier to check case identities, paired transitions, call totals and replay separation.

```console
python tools/experiments/catalog_outcome_comparison.py
```
