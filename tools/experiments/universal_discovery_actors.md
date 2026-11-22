# Tool selection by actors in six held-out languages

In the first run on November 23, 2026, twelve fresh actors answered read-only questions about a running Unity Editor. All twelve obtained live evidence matching the independent fixture, and eleven returned an answer that met the request on manual review. The strict automated scorer accepted eight answers. Each actor ran once. The measured server source was [a0ad6c762](https://github.com/ykh09242/unity-mcp/commit/a0ad6c76283a9f4c476f03621968217874b35ac4).

| Role | Completed | Suitable first action | Suitable first business tool | Matching live evidence | Strict grounded answer | Grounded answer after manual review |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Main | 6/6 | 4/6 | 6/6 | 6/6 | 3/6 | 5/6 |
| Subagent | 6/6 | 2/6 | 2/6 | 6/6 | 5/6 | 6/6 |
| **All** | **12/12** | **6/12** | **8/12** | **12/12** | **8/12** | **11/12** |

The first action includes actor-issued MCP discovery. The first business tool is the first operation after excluding discovery. Both measures skip recognized instruction-file and working-directory reads. Other shell calls retain the label `shell`. A suitable first action or business tool is a registered menu inventory, an error-filtered Console read, or a camera inventory, according to the task. A grounded answer also requires a successful actor-owned read that matches the native fixture. Every fraction uses all completed actors in its row.

## Individual outcomes

| Language | Task | Role | First action | First business tool | Automatic answer | Manual review | Answer language evidence |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Chinese | Menu | Main | `list_menu_declarations` | `list_menu_declarations` | Correct | Met request | Menu path |
| Chinese | Menu | Subagent | Shell | Shell | Correct | Met request | Menu path |
| Vietnamese | Console | Main | `read_console.get` | `read_console.get` | Correct | Met request | Verbatim messages |
| Vietnamese | Console | Subagent | `read_console.get` | `read_console.get` | Correct | Met request | Verbatim messages |
| Portuguese | Camera | Main | `search_unity_tools` | `inspect_scene_cameras` | Incorrect | Met request | Portuguese |
| Portuguese | Camera | Subagent | Shell | Shell | Correct | Met request | Name and number |
| German | Menu | Main | `list_menu_declarations` | `list_menu_declarations` | Correct | Met request | Menu path |
| German | Menu | Subagent | `list_menu_declarations` | `list_menu_declarations` | Correct | Met request | Menu path |
| Finnish | Console | Main | `read_console.get` | `read_console.get` | Needs review | Extra stack frames | Messages and call stacks |
| Finnish | Console | Subagent | Shell | Shell | Correct | Met request | Verbatim messages |
| Thai | Camera | Main | `search_unity_tools` | `inspect_scene_cameras` | Needs review | Met request | Thai |
| Thai | Camera | Subagent | Shell | Shell | Needs review | Met request | Thai |

Both Portuguese and both Thai camera actors named the unique highest-depth camera and gave its exact value, 73.75. The Portuguese main used a localized label and decimal comma. The Thai actors used affirmative Thai sentences. Manual review checked those original answers against the complete live camera inventory. The Finnish main copied both error messages and appended a stack trace to each, so it missed the verbatim output requirement. Three answers contained identifiable natural-language wording and all three used the requested language. The other nine contained menu paths, log records, stack frames, or a name and number. Those outputs do not establish prose-language adherence.

## Search and state preservation

Three actors called an MCP catalog search, once each. The Portuguese main and both Thai actors supplied their complete original prompt to `search_unity_tools`, together with `observation_source=editor_live` and the required value `unityCameras`. All three omitted `catalog_query`. All three responses reported `matching=observation` and ranked the camera inspection tool and camera resource together first. The count of actor-issued free-text-only searches was zero. A separate unstructured preflight on the same archived source returned `matching=hybrid` and the pinned LaBSE model identifier through both `search_unity_tools` and `manage_tools.search`.

The guard recorded zero blocked Unity mutation requests and zero menu-execution attempts. The four menu answers contained only the complete path. Each actor's before-and-after native snapshots matched for project and Editor identity, menu declaration, Frame Debugger window count, Play mode, complete ordinary-camera inventory, errors and warnings. Three Console reads initially returned errors and two camera subagents encountered host approval blocks on read-only loaded-scene requests. All five actors subsequently obtained the requested evidence.

Host envelopes showed a tool-list expression before the first Unity call in all twelve cases. The first projected host operation was a shell call in all twelve, including ten recognized instruction reads. Three actors had a shell command of unclassified purpose before their task read. The trace retained shell classifications and MCP operations. Its state-preservation claim applies to the sampled Editor state.

## Study design and limits

Six previously held-out locales were assigned one read task each before prompt delivery. Per locale, the runner supplied the same sealed request to the main actor and the delegating parent. The parent was instructed to forward it verbatim to a fresh native child. The retained delivery digests attest to the main and parent prompts. The child's own tool receipts, parent link and final answer supplied the subagent score. The model was `gpt-6.2-sol` at medium reasoning effort with a read-only sandbox. Each session verified the archived source and target Unity 6000.0.69f1 fixture. The two MCP namespaces were the scoped Unity server and the local CodeGraph competitor. The fixture supplied an actual menu declaration, two unique errors mixed with a warning, and three cameras with a unique depth maximum. [Benchmark method](blind_tool_selection.md)

Each run has one selected observation per language and role. The task varies with the language, so the rows do not estimate comparative language performance.

## Replay on the final runtime

The full twelve-cell schedule was repeated on the same fixture using fresh actors and parent sessions. All twelve role-specific delivery digests matched the first run. Both unstructured search entry points reported `matching=hybrid` with the combined LaBSE encoder and BGE reranker in the live preflight. The final source was [46f9f64b5](https://github.com/ykh09242/unity-mcp/commit/46f9f64b5845ff50a3117b0808d30108943c296b).

| Role | Completed | Suitable first action | Suitable first business tool | Matching live evidence | Strict grounded answer | Grounded answer after manual review |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Main | 6/6 | 4/6 | 6/6 | 6/6 | 5/6 | 6/6 |
| Subagent | 6/6 | 3/6 | 3/6 | 6/6 | 6/6 | 6/6 |
| **All** | **12/12** | **7/12** | **9/12** | **12/12** | **11/12** | **12/12** |

The first pass produced 11 complete actor receipts from 12 attempts. The original German child returned the correct path after a live read, but the host failed to capture its terminal event. One fresh same-cell recovery supplied a complete receipt. Both attempts were retained, giving 13 attempts for the 12 selected cells.

The suitable business tool came first in three of four menu cells, all four Console cells and two of four camera cells. All four menu answers contained only the path. All four Console answers contained the requested error messages, although three initial reads returned errors before successful reads. All four camera answers matched the independent name and depth. The strict parser rejected the Portuguese main's correct localized label and decimal comma. That answer was the only one with identifiable natural-language wording, and it used Portuguese. The other eleven used literals or terse technical values, including a Thai main answer with the field label `Depth`.

Five selected actors searched the MCP catalog, the Portuguese and Thai pairs and the German child. Each sent its complete original prompt with structured observation constraints and no `catalog_query`. All five received the suitable observation inventory at rank one. Zero selected actors issued an unstructured free-text-only search or attempted menu execution. The guarded relay recorded no blocked Unity mutation request. The final native state matched all 48 selected before-and-after snapshots across the two runs.

The replay reuses the original questions. Differences in first-tool selection and answers are descriptive. These actor observations do not isolate the effect of the reranker.
