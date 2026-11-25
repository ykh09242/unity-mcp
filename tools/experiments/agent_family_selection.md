# Agent selection from public tool families

## Evaluation fixed before selection

Keep the original request verbatim. The calling agent reads the public family directory, identifies the families relevant to the requested outcome, then compares every public binding in its selected families. It chooses an exact tool operation or readable resource, or abstains. Discovery must not dispatch an operation, activate tools, load search weights or access the external network.

The independent [36 originals](agent_family_holdout.json) cover Latvian, Slovak, Urdu, Telugu, Catalan and Norwegian Bokmål. Each language has one read and one contrasting change in each of three families excluded from development of the new selector. The fixture author verified actual availability in both core and full. The fixture SHA-256 is `8526a396abfeb0b4b1e32a2e17b8d1726604ecbd8aa9aeb7763c5474e4d806c8`. Preserve the corpus and freeze the implementation before replay. Prior encoder pretraining is outside this split. The agent-led path uses no encoder.

The existing panel comprises all six read-scope recall tasks in Korean and Japanese, the two activation tasks in Japanese and Spanish, the five original non-English menu-declaration requests in the blind tool fixture, and the two previously published short Korean active-scene questions. Each of the 23 existing and 36 new originals runs on both profiles.

Fresh agents of the same model see opaque case IDs, original requests and the actual public directory in its returned order. Each original's core and full cases go to different agents. Save the family choices before revealing operation pages. Fetch every page of every selected family through the same public MCP tool with the same original. Give agents the bindings and declared contracts in catalog order. Record their first choices, including abstentions, without showing expected answers or scoring feedback. Report family coverage, correct first choices and first choices of a prespecified opposite operation for each panel and profile. A missed family, unavailable desired route or failed choice remains in the denominator. These observations measure tool selection, not successful Unity execution.

## Observed selections

Four fresh `gpt-6.2-sol` callers used medium reasoning. Family coverage counts an original when at least one family the caller actually opened contained an accepted binding. First choice counts the one exact binding the caller picked from the offered pages. The catalog was frozen at `8573ff875`. All public packets delivered to the callers replayed with identical digests after removal of both original encoders and all project-fitted weights at `3291afe6b`. The replay checked inventory before and after and blocked business dispatch and ranking. [Per-case choices and packet digests](agent_family_results.json)

| Originals | Profile | Cases | Family covered | Correct first | Opposite first | Abstained |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| Existing | Core | 23 | 22 | 20 | 0 | 0 |
| Existing | Full | 23 | 20 | 18 | 0 | 0 |
| Held out | Core | 36 | 36 | 36 | 0 | 0 |
| Held out | Full | 36 | 36 | 36 | 0 | 0 |

All five original menu-declaration requests and all four scene-activation requests received an accepted first choice on both profiles. The six held-out languages each had six correct choices per profile across audio, input actions and terrain. The sample covers one agent model, six new languages and three new families.

The eight strict misses stayed in the result. The two full-profile short Korean active-scene questions selected `inspect_editor_state`, which exposes active-scene fields but was outside the sealed oracle for the dedicated scene operation. Four requests for a NavMeshAgent inventory selected `find_gameobjects`. Two requests for a selected NavMeshAgent's settings selected the editor selection resource. Four of the eight misses occurred with an accepted binding already present in an opened family. The opposite metric counts only the specific contrary operations identified before the study.

## Compatibility of raw-only callers

The previous pinned server was `0dd225434976c87ce96c892b04b177386e154ff7`. The same 59 originals were replayed with just `query` and `limit=3` on its installed wheel and on the model-free server. Every expected operation was advertised on both profiles. For existing originals, the old server placed an accepted representative in the first three 15/23 times on core and 14/23 on full. On the held-out panel it did so 1/36 times on each profile. The model-free literal matcher recorded zero top-three hits on all four cells. Name and keyword lookup remain available. Agents use `selection="agent"` for cross-language intent.

## Replay

From the repository root, use the pinned server's development environment. The verification command sends the same originals and reopens every selected family. It checks the first-choice record, offered bindings and full catalog packet digests. The raw command also runs against the archived wheel when using its installed Python environment.

```sh
python tools/experiments/agent_family_evaluation.py verify .work/agent-replay --result tools/experiments/agent_family_results.json
python tools/experiments/agent_family_evaluation.py raw .work/agent-replay --result .work/agent-replay/raw.json
```
