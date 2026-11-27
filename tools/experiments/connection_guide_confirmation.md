# Matched source edits with same-connection skill reads

Package `25f87f770` ran the 12 frozen requests and the Korean supplement on both catalogs. Each fresh agent received one original request. The 26 attempts cover 11 languages. The two isolated Unity Editors used the same package, server, skill bundle, and ordinary agent executable. The shared preference fingerprints matched after each Editor exited. [Primary results](connection_guide_confirmation_results.json) · [Korean results](korean_connection_confirmation_results.json)

| Catalog | Completed | Correct Unity target | Failed MCP tools / calls | Failed skill reads / attempts | Host routing errors before dispatch |
|---|---:|---:|---:|---:|---:|
| Core | 11/13 | 12/13 | 2/155 | 1/20 | 0 |
| Full | 13/13 | 13/13 | 2/180 | 0/22 | 1 |

Both Korean agents saved the exact quoted tooltip, including its question mark. Independent Editor and disk observations confirmed the visible label, other source bytes and asset metadata were preserved. The core final linked the asset relative to the agent's temporary working directory. The file existed in the separate Editor project. Under the established destination-accuracy rule the verdict was `incomplete_answer`. The full agent displayed the asset path as code and completed the request. Neither final claimed a live hover observation. The previously observed missing host connection selector did not recur in these two turns. [Frozen request](korean_edit_request.json) · [Previous Korean measurement](editor_target_confirmation.md)

After reading back the saved file, the Korean core agent searched for live hover observation tools. It passed a search object containing both text and selection in the `query` field. The Editor routing middleware expected a string and rejected the call before the search handler ran. The actor's next flat request succeeded. The initial failure remains in the count. The other core failure was a Polish skill read with an undeclared document selector. In full, a French hierarchy read omitted its required root and a Turkish scene edit supplied the public component property name where the allowlist required a serialized field name. Those three agents subsequently completed their edits. The Russian core edit saved one extra sentence-ending period. The full edit saved the intended unquoted value.

Tool totals include same-connection document reads. Document totals include both tool and resource reads. Successful document reads were checked against the packaged bytes. A host routing error counts separately when no corresponding public request was sent.

```sh
python tools/experiments/verify_heartbeat_edit_confirmation.py tools/experiments/connection_guide_confirmation_results.json
python tools/experiments/verify_edit_language_supplement.py tools/experiments/korean_connection_confirmation_results.json --include-primary
```
