# Action contract and resource read confirmation

Package `fb6f96ef3` pins server `ec717c632` and skill `27438f9a6`. Each catalog received the same 13 sealed requests across 11 languages. Each of the 26 first attempts used a fresh session with the same installed Codex CLI, model and reasoning effort as the preceding study. Independent Unity observations checked the requested state and protected state. Disk comparisons checked the relevant saved values and neighboring bytes. [Preceding study](structured_search_confirmation.md) · [Primary results](action_contract_confirmation_results.json) · [Korean results](korean_action_contract_confirmation_results.json)

| Package | Catalog | Native target | Completed | Failed tools / calls | Failed wire resource reads / attempts | Failed skill reads / attempts | Host router records before dispatch |
|---|---|---:|---:|---:|---:|---:|---:|
| `01bbbba2e` | Core | 13/13 | 13/13 | 2/176 | 1/14 | 1/25 | 0 |
| `01bbbba2e` | Full | 13/13 | 13/13 | 2/173 | 0/9 | 0/22 | 0 |
| `fb6f96ef3` | Core | 12/13 | 12/13 | 0/155 | 0/15 | 0/26 | 2 |
| `fb6f96ef3` | Full | 13/13 | 13/13 | 0/150 | 0/13 | 0/20 | 1 |

All 24 primary edits reached their native targets and passed the final-answer and preservation checks. Both Japanese Canvas edits saved the exact value and preserved the reference Canvas and hierarchy. Full read the Canvas component through `manage_tools(action="read_resource")`, and Core used the wire resource read. Document reads through tools also contribute to tool totals. Each of the 46 successful skill reads matched the pinned bundle bytes. Every public request received a reply.

The combined perfect-outcome check still fails. Three host stderr records report that the router could not parse arguments because the `server` field was missing. The French Core turn logged two identical lines at 08:57:31.628979 UTC. It later obtained the hierarchy and Canvas evidence and completed the edit. The Turkish Full turn logged one line at 09:16:44.783288 UTC, before its first public call. It later read the collider through the tool connection and left the scene open with the requested unsaved edit. The records identify only the missing field. The number of distinct French invocations and any same-resource recovery remain unknown. The Turkish Full answer gave accurate facts in Korean. The existing rubric scores the edit, constraints, evidence and factual accuracy.

## Korean source edit

Core stopped after a successful preview and reported it could not save because its filesystem was read-only. Its UI source calls consisted of the preview and source reads. Native and disk checks found the original tooltip unchanged. Full saved and read back the exact Korean tooltip, including its question mark. The visible label, other source bytes, asset metadata and open scene were preserved. Full scoped its answer to the saved source and said it had skipped a live hover test. [Frozen Korean request](korean_edit_request.json) · [Korean results](korean_action_contract_confirmation_results.json)

Separate SDK controls on both catalogs successfully performed and read back a saved UI source edit from a different client working directory. They also matched the component read through the tool connection to the native MCP resource and checked agreement among action schemas returned by initial listing, description and preparation. Both isolated Editors exited with all 130 shared preference values matching their starting fingerprints.

## Replay

The public replays exit successfully. Add `--require-perfect` to the combined replay to check the outcome target. It exits with status 1 for this cohort.

```sh
python tools/experiments/verify_heartbeat_edit_confirmation.py tools/experiments/action_contract_confirmation_results.json
python tools/experiments/verify_edit_language_supplement.py tools/experiments/korean_action_contract_confirmation_results.json --include-primary
```
