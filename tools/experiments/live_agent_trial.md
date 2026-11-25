# Unhinted agent outcomes in Unity

## Observed completion

On 25 November 2026, fresh Codex CLI 0.172.0 sessions using `gpt-6.2-sol` at medium reasoning received 71 original requests on each catalog. All 142 turns were delivered and independently assessed in Unity 6000.0.69f1. Each profile included 59 previously published originals in 11 languages and 12 newly sealed originals in six further languages. The two catalogs exposed 31 and 90 tools, respectively, and each exposed 32 resources and 12 resource templates. The evaluated product revision was `3973b243680eede8639343694edcb075c060b3e5`.

| Panel | Core completed | Full completed |
| --- | ---: | ---: |
| Existing originals | 43/59 (72.9%) | 47/59 (79.7%) |
| New originals | 7/12 (58.3%) | 9/12 (75.0%) |
| All originals | 50/71 (70.4%) | 56/71 (78.9%) |
| Reads | 33/43 (76.7%) | 37/43 (86.0%) |
| Edits | 17/28 (60.7%) | 19/28 (67.9%) |

Both catalogs completed 49 of the same originals. Seven completed only on full, one only on core, and 14 failed on both. These paired observations cover one turn per condition with one actor model and one editor configuration. Each new language contributed a camera read and a collider edit, which limits what can be inferred about performance across languages.

| Task with an incomplete outcome | Core completed | Full completed | Observed failure point |
| --- | ---: | ---: | --- |
| Restore a terrain cell | 0/6 | 0/6 | Every affected cell remained a hole. All 12 actors received native diagnostics showing `manage_terrain` disabled. |
| Read terrain cells | 0/6 | 4/6 | Eight answers omitted the requested cell classifications. Four full-catalog actors obtained the classifications with terrain-collider raycasts. |
| Make GateSensor a trigger and preserve other state | 1/6 | 3/6 | The trigger switched on in all 12 editors. In eight, the formerly empty shared physics material reference changed. An independent control reproduced this effect by reading the instantiating material getter. |
| List installed packages | 0/2 | 0/2 | Each answer reported a subset of the 45 installed packages. Both Japanese originals also requested versions, which their answers left unverified. |
| Read an installed package's registry details | 0/2 | 0/2 | The official version records were present. Two actors obtained the correct installed version through other Unity operations. None provided the requested registry information. |

These five tasks account for all 21 core and 15 full failures. Every other task completed on both catalogs, including the current scene, scene activation, scene listing, menu path, NavMeshAgent reads, audio importer inheritance, saved binding identifiers and checksums, input map creation, and the new camera read. In the call traces, 47 core and 35 full sessions used a public search action at least once. Discovery was optional. Agents chose their own routes, including direct tools and reads of Unity resources.

The [case results](live_agent_results.json) include the exact original, actual answer, ordered public calls, recorded failures, native target state and changed fields, adjudication and supporting evidence hashes. Failure stages record the first unmet grading criterion. A native diagnostic records a condition actually observed by that actor. For example, some terrain reads completed through a different operation even with the terrain manager disabled. Unity's diagnostics and graded outcomes have separate fields in the data.

## Prospective protocol

Freeze the server and Unity package at the tree of `3973b243680eede8639343694edcb075c060b3e5`. Keep all 59 distinct originals from `agent_family_evaluation.cases()`, including its previous holdout. Add the 12 independently authored originals in `live_agent_originals.json` (SHA-256 `cd8e8fd0edcf63a2b9a295066a2c0cc20ee6a53204fb925ac1f53d4f1d0905b9`). Run every original once per catalog, for 71 scheduled outcomes on core and 71 on full. A profile pair shares its fixture seed. Counterbalance profile order. Preserve failed and unavailable cases in the published record.

Start a fresh authenticated Codex CLI session with `gpt-6.2-sol` and medium reasoning for each case. Deliver the exact UTF-8 original as its entire user message. The caller receives the installed client's normal MCP discovery and the production server's instructions, public tools and resources. Its only task integration is the Unity MCP server for the selected profile and disposable editor. Keep the shell, other integrations and external search outside this experiment so the actor cannot read the experiment, answer key or operator control files. Supply no tool name, search setting, family list, example, answer format or follow-up to the actor. Accept direct calls and alternative tools.

Prepare and measure the real editor outside the agent's conversation. Verify the native project identity, prepare equivalent starting state for each pair, and snapshot it again after the actor finishes. Compare the returned answer with the facts the original requests. A read requires actual supporting evidence, an accurate answer and preservation of the measured state. A change requires the requested native change and preservation of unrelated measured state. A tool returning success alone is insufficient. A generic editor inspection can satisfy an active-scene question if its result grounds the correct answer. Preserve the open window state for menu queries and the unaffected neighbors for terrain edits.

Record lookup, chosen operations, recorded errors, the answer, before/after evidence and elapsed time per case. Keep private host paths, editor control files and full transport records outside Git. Any human or independent semantic adjudication must be bound to the original answer and its supporting observations. Report scheduled, delivered, verifiable, completed, incomplete and unresolved counts per profile and panel. Distinguish an unavailable fixture, client or provider from an observed agent error. Record original attempts if infrastructure is retried. The limit is six minutes for one actor turn; a timed-out attempt with observed actions retains its evidence. No model or catalog changes are part of this experiment.

During commissioning, unattended Codex with approvals set to `never` prevented requested changes before they reached the editor. Keep those diagnostic receipts separately. Start both formal profiles again using the client's automatic approval reviewer (`--approve-for-me`) within an empty temporary workspace, with shell access disabled. The reviewer and Unity's own permission checks remain active. A denied request in the formal run counts as an observed obstacle, and an actor's report of a denial is distinct from a recorded client error.

## Collider observer correction

The frozen first observer in `c86775954` hashed the requested trigger setting inside Unity's wrapped component serialization. An independent native control that only set the trigger changed that digest, even with the original shared physics material preserved. A save alone left the digest unchanged. Reading the instantiating material getter separately demonstrated that a real material assignment was also possible.

Before collecting corrected results, designate all 12 trigger cases (six originals on both catalogs) for fresh sessions with a corrected observer. Preserve the original attempts and keep the other 130 first formal cases. Use the same prompts, seeds, model, client permissions, production server and package. Exclude only the requested trigger property from the protected state and observe the shared material without calling the instantiating getter. Check the revised observer against a legal toggle, a save and a deliberate collateral change. Report the measured target transition separately from satisfying all preservation requirements.

## Registry prerequisite correction

Both installed-package originals request registry details. The initial editor held the correct package versions from local files and reported a placeholder offline registry; it could verify installation but could not establish a registry record. Preserve all four initial attempts as missing this prerequisite and keep them outside the count of observed agent failures. Before any replacement actor runs, designate both originals and both profiles for fresh sessions with an actual registry-backed installation of the same cached packages and an independently verified matching registry record. Keep the original prompts and seeds, published tools, reviewer policy and scoring criteria. After this second correction, the formal cohort comprises 126 first observations, 12 corrected collider observations and four registry cases; every original remains in its profile denominator.

Both replacements resolved against the official Unity registry from locally cached version records and tarballs with matching integrity checks. The 43 other package lock entries matched the original fixture. The report retains identifiers, disposition and hashes for the twelve first collider observations and four first package observations.

## Reproduction

Verify the published results against the sealed originals in the repository's existing Python server environment. This reads the checked-in data locally.

```console
python tools/experiments/live_agent_report.py verify
```

For a live replay, start with the [native fixture setup and controls](live_editor_fixture/README.md). Use a signed-in Codex CLI and the installed production server. In the same environment, generate the paired schedule, run the preflight, and deliver the originals to the prepared editors.

```powershell
python tools/experiments/live_agent_trial.py manifest --root $env:LIVE_AGENT_TRIAL_DIR
python tools/experiments/live_editor_fixture/preflight.py 0 1 2 3
python tools/experiments/live_agent_trial.py run --root $env:LIVE_AGENT_TRIAL_DIR --lanes 4
```

Each private case directory under `receipts-reviewed` contains the delivered turn, wire receipts and before/after state. Use the English rubrics and the evidence-bound review packet in `live_agent_outcomes.py` to adjudicate substantive answers independently. For the historical two corrections, use `--case` to select every affected original/profile pair and run it in the corresponding isolated fixture. The publisher reads the case reviews and native control evidence before verifying the cohort. Full actor sessions require access to the configured model service. Package restoration used existing cached files.
