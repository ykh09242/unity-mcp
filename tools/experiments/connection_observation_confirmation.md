# Connection observation confirmation

Package `0324c39b7` pins server `c49fca264` and skill `d5a080284`. An ordinary Codex CLI session received each of the 13 sealed original requests in 11 languages once per catalog. The prompts, actor, reasoning effort and grading criteria match the previous run. Completion requires the requested Unity state, preservation of protected data and an accurate final answer backed by an observation. [Previous run](preview_continuation_confirmation.md) · [Primary evidence](connection_observation_confirmation_results.json) · [Korean supplement](korean_connection_observation_confirmation_results.json)

| Package | Catalog | Completed | Failed tools / calls | Failed skill reads / attempts | Failed native resource reads / attempts | Pre-dispatch router records | Host approval rejections |
|---|---|---:|---:|---:|---:|---:|---:|
| `71efe67fe` | Core | 13/13 | 1/150 | 1/22 | 1/9 | 0 | 1 |
| `71efe67fe` | Full | 12/13 | 2/152 | 0/22 | 0/9 | 6 | 0 |
| `0324c39b7` | Core | 13/13 | 1/146 | 0/20 | 0/0 | 0 | 0 |
| `0324c39b7` | Full | 13/13 | 2/147 | 0/21 | 0/0 | 0 | 0 |

All 26 independently observed Unity outcomes and final answers met the task criteria. Both Russian actors saved the requested tooltip. On Full the agent prepared the guarded write, applied it and verified the saved source. Both Korean actors saved the exact requested tooltip, including its question mark, and preserved the visible label, other elements and asset metadata. Both Turkish actors changed the trigger and preserved the original collider dimensions and unsaved scene state. The two isolated Editors closed with all 130 shared preferences matching their starting values.

Three requests to `read_unity_resource` failed. German Core requested the URI for the connection's Editor list. French Full and Vietnamese Full requested the URI for the current Editor state, respectively without and with an explicit Editor. The server advertised these observations through dedicated tools and native resources, but their providers had not registered for generic tool reads. Each agent recovered through the dedicated tool. The host recorded the same three rejected calls. Every public request received a response, and all 41 successful skill reads matched the packaged bytes. Tool-based skill reads are included in the tool denominator.

Independent SDK controls used both protocol modes and catalogs with the same product. They compared native and tool-based guide and component observations, applied the Russian and Korean guarded source edits, checked stale-source and wrong-Editor rejection, and verified scene apply, replay rejection and undo. Core listed 62 tools and Full listed 92 in the isolated project.

The public replays accept these results. The combined `--require-perfect` check exits with status 1 for the three failed calls.

```sh
python tools/experiments/verify_heartbeat_edit_confirmation.py tools/experiments/connection_observation_confirmation_results.json
python tools/experiments/verify_edit_language_supplement.py tools/experiments/korean_connection_observation_confirmation_results.json --include-primary
```
