# Matched Unity edits on the synchronized Editor catalog

Both catalogs completed all 12 original requests on package `79fc892df`. The zero-failure target was missed in both catalogs. Eight turns recovered from one rejected MCP request each. Every public request received a reply. [Per-case results](editor_catalog_confirmation_results.json) · [Previous measurement](heartbeat_edit_confirmation.md)

| Catalog | Completed, previous → current | Failed tools/call | Failed resources/read | Failed host tool invocations |
|---|---:|---:|---:|---:|
| Core | 10/12 → 12/12 | 1/98 → 1/119 | 0/34 → 3/42 | 1/133 → 4/161 |
| Full | 12/12 → 12/12 | 2/121 → 1/104 | 1/44 → 3/35 | 3/172 → 4/141 |

The actor's own first complete tools list matched the commissioned 61 or 91 tools in every run. The core shipment-label turn received its expected catalog. The core tooltip turn reported the correct saved file. All 24 final answers agreed with the native outcome, the requested save state and the protected values. The host recorded ten diagnostic events per catalog, including three routing errors per catalog before a public request was sent. Those diagnostics can overlap with a failed invocation.

## Rejected requests

| Call | Core | Full | What happened |
|---|---:|---:|---|
| Search the tool catalog | 1 | 0 | The actor placed a complete argument object inside the string field `query`. The server's startup example used a positional object. The actor recovered with flat arguments. |
| Inspect objects in the active scene | 0 | 1 | The actor selected the active scene and requested projection fields. The scoped search rejected the omitted predicate before dispatch to Unity. |
| Read a skill document | 3 | 3 | Five reads supplied client installation paths and one supplied an unpublished skill URI. The packaged root was listed as `SKILL.md` with an empty description. |

## Measurement

Each slot ran once in a fresh installed Codex CLI 0.172.0 session with `gpt-6.2-sol` at medium effort. The only user input was the original UTF-8 request. The same twelve requests in ten languages, alternating catalog order, 360-second limit, observer and grading rules were frozen before submission. The Unity 6000.0.69f1 project used server `bf92d33d3` and packaged skill `dad211b92`. The archive check covered every file in both pinned source trees. Independent Editor controls checked all twelve edits and protected neighbors. Additional disk controls covered the UXML, material and audio importer cases. After the owned Editor exited, all 130 shared tool preferences matched their initial values.

The verifier checks the public ledger and predecessor. With the private evidence directory it also replays the wire observations, actor turns, native and saved results. The strict gate exits with status 1 for this run.

```sh
python tools/experiments/verify_heartbeat_edit_confirmation.py tools/experiments/editor_catalog_confirmation_results.json
python tools/experiments/verify_heartbeat_edit_confirmation.py tools/experiments/editor_catalog_confirmation_results.json --require-perfect
```
