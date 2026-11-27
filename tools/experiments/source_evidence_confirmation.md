# Blind Unity edits with saved-source verification

Package `5405b3f59` completed 11 of 12 requests in each catalog. Both agents completed the previously failing label-removal workflow without a rejected call. Each used a fresh source-file SHA-256 observation before and after the change. The asset file bytes matched. The same observation was used in the full catalog for metadata-only changes to a second asset and an audio importer. [Case ledger](source_evidence_confirmation_results.json) · [Previous study](catalog_discovery_confirmation.md)

| Catalog | Completed | Failed tools / calls | Failed skill reads / attempts | Failed resources / reads, including skills | Failed host invocations / invocations |
|---|---:|---:|---:|---:|---:|
| Core | 11/12 | 1/110 | 0/21 | 0/41 | 1/153 |
| Full | 11/12 | 1/137 | 0/22 | 0/36 | 1/173 |

Each missing edit involved the same Russian request to save a tooltip in the UXML source. Both agents previewed the correct change. Neither sent an apply request, and the independent disk and Editor observations found the original tooltip. Both final answers reported that the file had not changed. The full agent also stated that saving required access to its working folder or reconnecting the Editor there. The successful control on the selected Editor established that this prerequisite was unnecessary.

The rejected MCP calls were catalog lookups that supplied both an exact tool and an operation. They occurred during a prefab rename in core and an audio importer edit in full. Both edits subsequently completed. The host also logged four argument-routing errors per catalog before dispatch. The private verifier checks these diagnostics separately from public MCP replies.

The commissioned profiles, actor, twelve originals in ten languages, isolated Unity version and observations match the preceding study. All 24 slots ran once. The owned Editor exited and all 130 shared tool preferences matched. The checks on both shipping catalogs confirmed that an oversized request returned the bounded page, that unscoped catalog text used search, and that the new digest matched independent disk bytes before and after a label edit.

```sh
python tools/experiments/verify_heartbeat_edit_confirmation.py tools/experiments/source_evidence_confirmation_results.json
```
