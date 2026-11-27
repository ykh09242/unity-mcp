# Eleven-language Unity edit confirmation

Package `01bbbba2e` pins server `8561d39d6` and skill `efcc61162`. Each agent received one original request in a fresh session. The two catalogs each ran the same 13 requests. Every request had one submission. The installed client, model, reasoning effort, sealed inputs, independent Editor observations, disk controls and completion rubric matched the [preceding 26 attempts](connection_guide_confirmation.md).

| Catalog | Completed | Failed MCP tools / calls | Failed skill reads / attempts | Host routing errors before dispatch |
|---|---:|---:|---:|---:|
| Core | 13/13 | 2/176 | 1/25 | 0 |
| Full | 13/13 | 2/173 | 0/22 | 0 |

All 26 requests reached the exact native target and passed the final-answer and preservation checks. The core Russian prefab agent first sent a host-local skill file URI to the server. The server rejected it. The next same-connection skill read succeeded. The core Japanese scene agent twice requested an undeclared resource-reading action to read a Canvas component. Its full-catalog counterpart twice supplied a diagnostic-only depth option while reading a UI hierarchy. Both agents subsequently read the requested evidence and saved the scene. The strict zero-failure check fails on these five public replies. There were zero missing replies. Both isolated Editors exited and all 130 shared preference values matched their starting fingerprints. [Primary results](structured_search_confirmation_results.json) · [Korean results](korean_search_confirmation_results.json)

## Korean source edit

Both catalogs saved the requested Korean tooltip, including its final question mark. Editor and disk observations confirmed that the visible label, neighboring elements, other source bytes and asset metadata were preserved. Both agents displayed the Editor-relative asset path as inline code. The core agent explicitly scoped its observation to the saved file. The full agent described its source comparison. [Frozen request](korean_edit_request.json)

| Package | Catalog | Completed | Correct saved edit | Failed MCP tools / calls | Failed skill reads / attempts | Host routing errors before dispatch |
|---|---|---:|---:|---:|---:|---:|
| `25f87f770` | Core | 0/1 | 1/1 | 1/6 | 0/0 | 0 |
| `25f87f770` | Full | 1/1 | 1/1 | 0/5 | 0/0 | 0 |
| `01bbbba2e` | Core | 1/1 | 1/1 | 0/11 | 0/2 | 0 |
| `01bbbba2e` | Full | 1/1 | 1/1 | 0/4 | 0/0 | 0 |

The first core agent had already edited the correct file when it submitted a structured search to find a live hover observation. The previous routing layer rejected the object during string validation, before invoking search. Its final also linked the file against a different client working directory. The final package accepts a complete search object through the same read-only handler as a flat search and tells the agent to present Editor paths as code until the shared root is verified. The two tested Korean actors used different tool sequences. The successful native result and first-attempt call counts come from their receipts.

Separately, a real SDK client replayed the previously rejected payload against the pinned server and both isolated Editor catalogs. Structured and flat queries returned equal results through `search_unity_tools`, `manage_tools(search)` and unscoped `manage_tools(catalog)` with an explicit Editor. The middleware validates the canonical search fields while the receipt store binds the submitted arguments. Document read totals include both published read surfaces. A document read through a tool also contributes to the tool total.

```sh
python tools/experiments/verify_heartbeat_edit_confirmation.py tools/experiments/structured_search_confirmation_results.json
python tools/experiments/verify_edit_language_supplement.py tools/experiments/korean_search_confirmation_results.json --include-primary
```
