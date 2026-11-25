# Repeating the unhinted Unity trial after native tool fixes

## Observed completion

On 25 November 2026, each of the same 71 originals in 17 languages was delivered once per catalog to a fresh agent connected to Unity 6000.0.69f1. All 142 turns produced an answer and a native observation. Completion rose by 9 cases on core and 7 on full. A completion required both a grounded answer and preservation of unrelated state. [Per-case observations and calls](live_agent_followup_results.json)

| Requests per catalog | Core before | Core after | Full before | Full after |
| --- | ---: | ---: | ---: | ---: |
| All 71 originals | 50/71 (70.42%) | 59/71 (83.10%) | 56/71 (78.87%) | 63/71 (88.73%) |
| Existing originals | 43/59 | 47/59 | 47/59 | 51/59 |
| New originals from the first trial | 7/12 | 12/12 | 9/12 | 12/12 |
| Reads | 33/43 | 37/43 | 37/43 | 41/43 |
| Edits | 17/28 | 22/28 | 19/28 | 22/28 |

| Request | Core before → after | Full before → after | Evidence from the repeat |
| --- | ---: | ---: | --- |
| Set the existing collider to trigger and preserve its material | 1/6 → 6/6 | 3/6 → 6/6 | All twelve triggers switched on. The shared material and other protected state were preserved. |
| List installed packages | 0/2 → 2/2 | 0/2 → 2/2 | All four agents used the inventory resource. All 45 package IDs and versions matched Unity. |
| Read installed package registry details | 0/2 → 2/2 | 0/2 → 2/2 | All four agents read the installed record. Their answers identified the official source and version-specific metadata. |
| Classify terrain cells | 0/6 → 0/6 | 4/6 → 4/6 | Four full-catalog agents inferred all nine requested cells correctly using terrain collider raycasts, including boundary probes. The other eight answers omitted the classifications. |
| Restore one terrain cell | 0/6 → 0/6 | 0/6 → 0/6 | All twelve target cells remained holes. No agent dispatched the native terrain tool. Eleven received its disabled diagnostic. The remaining actor saw a partial native diagnostic and a server report that the tool was unavailable. |

The 20 failures consisted of eight incomplete terrain reads and twelve terrain edits with no target change. Across the two catalogs, 59 originals completed on both, four only on full and eight on neither. Compared with the first run, core gained nine completions and lost zero. Full gained eight and lost one. The Norwegian terrain read now classified all three cells. The Telugu terrain read that previously succeeded located the object but left the four cells unclassified. No other case switched from success to failure. Each original had one observation per catalog and revision with one model and one operator configuration.

## Matched conditions

The [first trial](live_agent_trial.md) measured product revision `3973b243680eede8639343694edcb075c060b3e5`. Its [published observations](live_agent_results.json) were committed at `b859c1fb79ec1f227eca9dfa2946aeba0b57d2a0`. The repeat uses the product at `fe9fb9a70e15c86351f3f83de11308f6ee6f0d6f`. Initial fixture checks at `f4bb74a7a` found a configured registry being reported for local packages and an empty author field. Both were corrected before any scored actor session.

Run the same 59 existing originals and 12 new multilingual originals from that study on each catalog. Preserve every case ID, query, fixture seed, profile order, six-minute turn limit, scoring rubric and state-preservation requirement. Give a fresh Codex CLI 0.172.0 session using `gpt-6.2-sol` at medium reasoning only the original request. The actor has ordinary access to the selected Unity MCP server with the same client approval policy. It can choose any public route. Read the real editor before and after each turn and independently adjudicate answers against its facts and the actor's recorded calls.

Use the corrected collider observer from the first study in the regular disposable editors and the same two official cached registry records in the separate registry editor. Assign all four installed-package cases to the registry editor and the other 138 cases to the regular editors. Keep the local Unity version, package inventories and caller binaries fixed. Preserve every scheduled outcome, including agent failures and unavailable capabilities.

Across corresponding fixtures, all 22 project settings files, the editor host DLL and all 44 non-product package lock entries matched. The product entry changed in the manifest and lockfile. Unity allocates fresh collider instance IDs and imported audio asset GUIDs in each editor. The audio preservation digest includes the clip GUID. Each edit was assessed against the starting state from its own turn.

The host already stores `manage_terrain` as disabled. The operator requested that all shared preferences remain intact. The normal agent trial retains that setting on both catalogs. Run the public native terrain handler separately in an isolated test project, record its observed reads and guarded writes, and keep those control results outside the 142 agent outcomes. One local Cinemachine preflight read returned `no_unity_session` before a reconnect succeeded. Both attempts were recorded before actor delivery.

## What changed in the editor

Reading a 3D collider's reflected `material` property can attach an instance to the collider. The previous study independently reproduced this change after eight otherwise successful trigger edits. Component inspection now obtains this value from `sharedMaterial` for every subclass inheriting the Unity getter. The live single-component and all-components resource reads both preserved a null reference and the rest of the observed state. Native tests also exercised a preassigned material with default and explicit getter evaluation. [Unity 6.0 collider material reference](https://docs.unity.com/en-us/engine/6000.0/script-reference/unityengine/collider/material)

The earlier package interface required operator consent even for inventory and metadata reads. Both catalogs now expose `mcpforunity://project/packages` for every registered ID and installed version, and `mcpforunity://project/packages/{package_name}` for one installed record. They use the selected editor's local package inventory. Unity can supply a matching registry for a package installed from another source, so the response reports a registry only for registry installations. The live registry fixture returned all 45 IDs and versions. Input System 1.14.2 and Cinemachine 2.6.11 matched independently verified official cached records. Their local-install counterparts reported their respective installation sources and an empty registry. Package mutation still uses its existing consent path. [Unity 6.0 package registry reference](https://docs.unity.com/en-us/engine/6000.0/script-reference/unityeditor/packagemanager/packageinfo/registry)

The terrain schema now describes cell coordinates, matrix order, hole-mask polarity and the scope of write revisions. Native hole observations identify both Boolean values explicitly. The named diagnostic explains where the operator can review a disabled tool. In the isolated editor, a 3-by-3 read could observe a hole but its revision could not authorize a 1-by-1 write. A read of the exact cell supplied the revision that restored its surface. The test compared every other mask cell with its starting value. All nine isolated EditMode checks passed across terrain, colliders and package origin.

Both measured catalogs retained their complete named tool inventories. Removing the two new package resource URIs from each measured catalog reproduces the original inventory digest. The search implementation, language handling, prompt set, model and scoring code were held fixed. Native controls and per-turn receipts are identified separately in the [matched observations](live_agent_followup_results.json).

## Reproducing the comparison

Verify the checked-in cases, paired transitions, original prompt hashes, fixture assignments and measured catalog memberships offline in the repository Python environment.

```console
python tools/experiments/live_agent_comparison.py verify
```

For a live repeat, follow the [fixture setup](live_editor_fixture/README.md) with `LIVE_AGENT_PRODUCT_REVISION` set to the full candidate revision above. Use four regular editors with the corrected collider observer and a separate editor with the two official cached registry records. Run `live_agent_trial.py run` with the matching `--root`, `--lanes` and scheduled `--case` IDs for each fixture. The original trial's [delivery and adjudication protocol](live_agent_trial.md#prospective-protocol) also applies here. After grading, the publisher rebuilds the public file from the private receipts and isolated EditMode result.

```console
python tools/experiments/live_agent_comparison.py publish --root REGULAR_RUN --registry-root REGISTRY_RUN --native-run NATIVE_TEST_RUN
```
