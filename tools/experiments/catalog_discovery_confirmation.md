# Blind Unity edits on package 41703d7e9

Both catalogs completed all twelve original requests. The installed agent successfully read all 43 packaged skill documents it requested. Five other MCP requests failed during the two turns about removing a label while preserving the source file. [Case ledger](catalog_discovery_confirmation_results.json) · [Previous study](editor_catalog_confirmation.md)

| Catalog | Completed | Failed tools / calls | Failed skill reads / attempts | Failed resources / reads, including skills | Failed host invocations / invocations |
|---|---:|---:|---:|---:|---:|
| Core | 12/12 | 2/121 | 0/23 | 0/44 | 2/165 |
| Full | 12/12 | 2/113 | 0/20 | 1/37 | 3/152 |

On the preceding package, each catalog completed 12/12. Core recorded 1/119 failed tool calls and 3/23 failed skill reads. Full recorded 1/104 and 3/20 respectively. On the present package, no public request went unanswered. Host stderr included four pre-dispatch routing errors in core and five in full. The strict verifier also counts host diagnostics, which can overlap with failures in the table.

The core agent requested a search page of 25 and sent free text to the catalog action. The server rejected both. The full agent tried an unpublished asset URI, requested an unsupported catalog selector and page size, and tried reading a text asset with the UXML/USS reader. In both turns the asset information provided a modification timestamp. The final edits preserved the original bytes. The specific failures occurred while the agents sought stronger evidence for that claim.

Each slot used one fresh session of the installed CLI 0.172.0 with `gpt-6.2-sol` at medium effort. Only the original UTF-8 request was submitted. The twelve originals span ten languages. Core and full exposed 61 and 91 tools respectively. Unity 6000.0.69f1 loaded the archived package, server `a394fcb08` and skill `a359961d0`. Independent checks covered native state, protected neighbors, saved fields and the final answers. Each successful packaged document read matched its manifest hash. The owned Editor exited and all 130 shared tool preferences matched their initial values.

```sh
python tools/experiments/verify_heartbeat_edit_confirmation.py tools/experiments/catalog_discovery_confirmation_results.json
```
