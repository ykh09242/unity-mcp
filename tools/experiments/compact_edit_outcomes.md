# First-turn Unity edits with a fixed tool inventory

Five of six archived edit requests reached their native targets only with the full catalog. The missing core bindings were `asset_labels` for two requests, `prefab_contents` for one, and `ui_source` for two. In the sixth case the client already had `manage_components` and changed the live Canvas in both profiles. In one failed label edit, tool selection on the server succeeded, but the client still lacked the public binding for the rest of that turn. Searching again could describe the operation without changing the Unity asset. [Archived case receipts](agent_outcome_holdout_confirmation.json)

The local core profile now starts with the groups containing the server's registered focused capabilities, including their companion tools for reading and verification. The agent receives the original public names and schemas on its first list. The selected Editor's tool availability, host filters and operation permissions still govern calls. Explicit `--tool-groups core` selects only the core group and server helpers. [Discovery contract](../../docs/task-tool-discovery.md)

## Experiment

The paired run used the same six archived originals and six new originals in both catalogs, once on the baseline and once on the candidate. The originals, order and expected edits were sealed before the product change. Each of the 48 slots used one original message, a fresh ordinary client session and an isolated Unity Editor fixture reset to its specified initial state. Real native observations checked the target, persistence where requested, and unrelated state. Completion also required an accurate final answer. A failed `tools/call` reply is counted even if the agent later completed the edit. Resource errors are reported separately.

Baseline package `02cf9924b` uses server `e221c9366`. Candidate package `46cee3cd3` uses server `d14f29070` and skill `bd207b749`. The benchmark used the installed client and Unity 6000.0.69f1. Tool discovery in the package runs locally, and the calling agent interprets the original message.

## Original edits

| Run | Core completed | Full completed | Failed tool calls, core / full | Failed resource reads, core / full |
|---|---:|---:|---:|---:|
| Archived six | 1/6 | 6/6 | 9 / 3 | 0 / 0 |
| Baseline repeat | 1/6 | 4/6 | 5 / 0 | 0 / 0 |
| Candidate | 6/6 | 5/6 | 3 / 4 | 0 / 0 |

In the baseline repeat, full reached five native targets. One correctly edited asset was reported at the wrong destination in the final answer. A different actor added a period to the requested tooltip text. On the candidate all six core actors performed their edits through the intended MCP bindings. The full tooltip edit again saved an extra period. These were separate first attempts on the same originals.

## New multilingual edits

The original panel used German, Russian, Vietnamese and French. The six new requests used Swedish, Italian, Japanese, Polish, Spanish and Turkish. Each was submitted unchanged in both catalogs and both revisions. The per-case record contains the original UTF-8 hashes and the independently observed before and after values. [Paired results](compact_edit_results.json)

| Run | Core completed | Full completed | Failed tool calls, core / full | Failed resource reads, core / full |
|---|---:|---:|---:|---:|
| Baseline | 1/6 | 4/6 | 6 / 11 | 0 / 2 |
| Candidate | 4/6 | 4/6 | 9 / 9 | 1 / 1 |

Both candidate catalogs missed the same two edits.

- The material tool updated the loaded material's render queue from 2011 to 2457. Both agents reported a save. A separate read-only copy of the selected material before and after each turn showed identical serialized bytes with queue 2011. The paired observer had recorded the live value on both revisions. Only the candidate has the additional disk copies. Every material slot stayed outside the completed count. [Material operation](../../MCPForUnity/Editor/Tools/MaterialRenderState.cs)
- The Spanish request asked to turn off `preloadAudioData`. The public audio import settings did not expose that field. The native observer found it still enabled in all four slots. [Audio import settings](../../MCPForUnity/Editor/Tools/AudioInspection.cs)

Two baseline slots illustrate why observing a changed value alone is insufficient. One full actor used a host file edit to reach the target. One core actor received 48 tool names while its commissioned list had 31. Both remain in the denominator and neither earned a completion. All 24 candidate first listings matched their commissioned catalog.

## Completion, calls and inventory size

| Metric across the 12 originals per profile | Baseline core | Candidate core | Baseline full | Candidate full |
|---|---:|---:|---:|---:|
| Strict completions | 2/12 | 10/12 | 8/12 | 9/12 |
| Failed tool replies / tool calls | 11/153 | 12/89 | 11/121 | 13/91 |
| Failed resource replies / reads | 0/70 | 1/62 | 2/62 | 1/66 |
| Unanswered tool or resource requests | 0 | 0 | 0 | 0 |
| Host error records | 15 | 30 | 20 | 25 |
| Commissioned tool names | 31 | 61 | 91 | 91 |
| Bytes of initial tool descriptors | 101,735 | 194,137 | 368,137 | 368,137 |

The core actors completed eight more requests and issued 64 fewer tool calls. Failed tool replies increased by one in core and two in full. Host error records can describe the same unsuccessful public call. Descriptor sizes use a compact, sorted UTF-8 JSON array from an independent SDK connection with the same profile. Each commissioned catalog also contained 35 resources and 13 templates.

Each slot used the installed Codex CLI 0.172.0 with `gpt-6.2-sol` at medium reasoning effort over stdio. The client owned the conversation and contacted its hosted model. Both disposable Editors inherited the same 130 shared Unity tool settings, with the same private UI opt-in. The before and after preference digests matched, and both owned Editors closed. These figures describe first attempts by this host on the 12 tested originals. [Conditions, controls and per-call evidence](compact_edit_results.json)

From the repository root, run the offline verifier to check the public record and its historical comparison. The optional perfect-outcome gate returns exit code 1 for this candidate.

```sh
python tools/experiments/verify_compact_edit.py
python tools/experiments/verify_compact_edit.py --require-perfect
```
