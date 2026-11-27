# Blind Unity edits after clarifying the Editor target

Package `6e403d15f` completed 12 of 12 edits in core and 11 of 12 in full. All 272 MCP tool calls succeeded. One full-catalog attempt to read a skill document failed. The other 44 skill reads returned the exact packaged bytes. [Case and call ledger](editor_target_confirmation_results.json)

| Package | Catalog | Completed | Failed MCP tools / calls | Failed skill reads / attempts | Failed resources / reads, including skills | Failed host invocations / invocations |
|---|---|---:|---:|---:|---:|---:|
| `41703d7e9` | Core | 12/12 | 2/121 | 0/23 | 0/44 | 2/165 |
| `41703d7e9` | Full | 12/12 | 2/113 | 0/20 | 1/37 | 3/152 |
| `5405b3f59` | Core | 11/12 | 1/110 | 0/21 | 0/41 | 1/153 |
| `5405b3f59` | Full | 11/12 | 1/137 | 0/22 | 0/36 | 1/173 |
| `6e403d15f` | Core | 12/12 | 0/131 | 0/23 | 0/43 | 0/175 |
| `6e403d15f` | Full | 11/12 | 0/141 | 1/22 | 1/36 | 1/177 |

The two agents that had previously stopped after previewing the Russian tooltip both saved it on this package. Core selected `Opens the handover log` from the original, checked that literal during preparation, and read back the exact saved value. Full sent `Opens the handover log.` in both its preview and write, then accurately reported the extra period. The English phrase is unquoted at the end of a sentence in the Russian original. The predeclared expected value excludes the period. The independent Editor and disk reads both detected the difference. The label, other elements and protected bytes stayed intact. [Previous measurement](source_evidence_confirmation.md)

The full-catalog Japanese scene edit reached the requested saved state. Its first skill read sent a host-local `file://` path to the MCP server and received a resource-not-found error. The actor then obtained the published skill address and read it successfully. Every public request received a reply. Separately, the host logged six routing errors in core and seven in full before sending any public request. Ten lacked the host reader's server argument and three used an unrecognized connection name. The strict zero-error check fails. [Original package measurement](catalog_discovery_confirmation.md)

Each revision used the same twelve original requests in ten languages, one fresh agent session per request and catalog, and the same installed CLI 0.172.0 with `gpt-6.2-sol` at medium effort. Each session received only its original UTF-8 request. The isolated Unity 6000.0.69f1 Editor exposed 61 tools in core and 91 in full. Both SDK controls saved and read back the exact tooltip with a client working directory distinct from the selected Editor project. All 130 shared tool preferences matched after the Editor exited.

After this cohort the server guidance began identifying the published guide URI, explaining host connection selection on resource pages, and directing the caller to resolve unquoted values in context. The literal preparation contract preserves the caller's chosen text, including intentional punctuation. The measurements above apply to `6e403d15f`. The later package is used for the separate [Korean request](korean_edit_request.json).

```sh
python tools/experiments/verify_heartbeat_edit_confirmation.py tools/experiments/editor_target_confirmation_results.json
```

## Korean supplement on package `553e660c9`

The agent received one new Korean request in a fresh session per catalog. It asked for a Korean tooltip with a question mark, an edit to the original file, and preservation of the visible label and other elements. The request and expected value were committed before either submission. Each agent saved the exact text and read the file back. Independent checks verified the Editor state, the saved tooltip, all other source bytes and the asset metadata. Both agents accurately reported that they had not exercised a live pointer hover. [Original Korean request](korean_edit_request.json) · [Supplement and language coverage](korean_edit_confirmation_results.json)

| Catalog | Completed | Failed MCP tools / calls | Failed skill reads / attempts | Failed resources / reads, including skills | Host routing errors before dispatch |
|---|---:|---:|---:|---:|---:|
| Core | 1/1 | 0/11 | 0/2 | 0/3 | 1 |
| Full | 1/1 | 0/12 | 0/2 | 0/3 | 0 |

All 29 public requests received successful replies. The core host rejected one additional read attempt before contacting the server because the host wrapper lacked its server argument. The strict check includes this diagnostic. The owned Editor exited and the same 130 shared preferences matched. The language index now covers eleven languages across the primary study and this supplement. The two Korean turns used the later package and have their own denominators.

```sh
python tools/experiments/verify_edit_language_supplement.py
```
