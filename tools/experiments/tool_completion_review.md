# Public tool contracts and task completion

The baseline is `0e57b46ffc778d7b6ca614b29974a6cce0303d09`. The catalog census covers the shipped server, including the optional execution-receipt control. It records each tool's public schema and describes every selector on both supported MCP generations. Dynamic project tools depend on the connected project. The prior Unity project advertised 31 tools on core and 90 on full. An absent tool can depend on the Editor preference, consent, installed packages or project binding. The inventory alone does not establish the cause. [Reproducible census](tool_catalog_audit.py) · [Prior native inventories and original outcomes](live_agent_followup_results.json)

## Unhinted Unity outcomes

| Matched first attempts | Core baseline | Core shipping | Full baseline | Full shipping |
| --- | ---: | ---: | ---: | ---: |
| 71 original tasks in 17 languages | 59/71 | 57/71 | 63/71 | 64/71 |
| Additional eight component tasks in English | 8/8 | 8/8 | 8/8 | 8/8 |

The installed shipping revision `e3c0c6450` completed 121/142 original requests (85.21%), compared with 122/142 (85.92%) at baseline. Full gained two terrain reads. Three requests completed at baseline ended without a final answer after model-stream disconnects on shipping. One of them applied the requested collider change before the stream ended. Six shipping sessions ended without an answer in total. All 142 first attempts count toward completion. The operator's terrain setting stayed disabled. [Per-case outcomes and failure analysis](catalog_outcome_comparison.md)

Shipping issued 1,572 public calls compared with 1,512 at baseline. Unsuccessful replies fell from 128 to 59, including a drop from 36 to 16 for `search_unity_tools`, from 17 to zero for `manage_tools.describe`, and from 16 to zero for `manage_audio.read_import_settings`. An earlier measurement of `d1ba9475b`, before the startup guidance fix, recorded 123/142 completed requests and 62 unsuccessful replies. [Shipping wire evidence](catalog_outcome_shipping_results.json) · [Earlier measurement](catalog_outcome_comparison_results.json)

All eight additional component tasks reached the requested state in each catalog at baseline and on shipping. Core used 62 public calls compared with 90 at baseline. Full used 46 compared with 45. [Additional panel and reproducible measurements](tool_outcome_panel.md)

## Public contracts

| Contract check | Baseline | After |
| --- | ---: | ---: |
| Static tools / named groups | 134 / 17 | 134 / 17 |
| Named actions / tools without an action selector | 816 / 35 | 816 / 35 |
| Bindings described per protocol | 851 | 851 |
| Selected incomplete calls admitted by the action schema and refused by the handler | 19/19 | 0/19 |
| Forwarded operations missing a documented observation limit | 6 | 0 |

The input probes cover eight Input System operations, six Terrain operations, three reflection operations and two scene lifecycle operations. The checks use the actual public descriptions and Python handlers. The observation limits describe the scope of six forwarded scene operations. [Action inputs and schema checks](../../Server/tests/test_action_input_contracts.py) · [Forwarded capabilities](../../Server/tests/test_action_catalog.py)

In the previous 142 agent turns, 31 search calls failed after mixing search modes or omitting the original from an exact family lookup. An SDK replay against the original tool inventories returned the operation pages directly in two cases. For the other 29, the server returned valid retry arguments. All three retries with required evidence kept their constraints and found observation candidates. The replay had seven fewer resources than the original Editor and measured individual requests. A separate set of 15 rejected schema descriptions in the original turns supplied `include_schema=true`. The same bound describe now succeeds with or without that flag on both MCP generations. [SDK tests](../../Server/tests/test_agent_family_catalog.py) · [Public describe checks](../../Server/tests/test_functional_tool_catalog.py)

The AudioImporter fixture ran against Unity 6000.0.69f1. The same 18 Edit Mode checks produced 17 passes at baseline and 18 after the first fix. The failing read used the documented `iOS` name. Extending the fixture to reuse returned platform selectors exposed failures on WSA and Switch. The same 20 checks then produced 18 passes on the first fix and 20 on the shipped version. The native importer confirmed the override after apply and after clear, with unrelated settings preserved. [Native tests](../../TestProjects/UnityMCPTests/Assets/Tests/EditMode/Tools/ManageAudioDiagnosticsTests.cs)

## Distributed instructions

The review covered the 18 files under `.claude/skills` and seven under `unity-mcp-skill`. It corrected 20 of the 25 files. The Editor installer and manual orchestrator copies match across all seven files. The checked workflows include 84 JSON blocks, of which 80 are tool calls validated against both SDK catalogs, eight CLI invocations, and Play Scenario and suite definitions. These checks cover the shape of the documented calls. [Skill documentation checks](../../tools/tests/test_skill_documentation.py) · [Default catalog examples](../../Server/tests/test_functional_tool_catalog.py)

The installable package pins the reviewed skill source at `4e7f06304fb470b139e5436e4a9754cd782f7c71` and the server containing its bundle at `ab47f002e230367100dcb10c92747f0f89f066b7`. The server serves the immutable texts on the `skill://unity-mcp-orchestrator/` resource paths. The updated instructions cover current resource advertisement, fixed inventories, exact component identities, prefab creation and inspection, ProBuilder topology revisions, compile job identity and the Player assurance profiles. The example checks enumerate the distributed directories and compare every documented tool request to the public input schema for each protocol. [Pin record](../../release-source-inputs.json) · [Protocol and bundle checks](../../Server/tests/http/test_mcp_skills.py) · [SDK example checks](../tests/test_tool_reference_examples.py)

Run the catalog audit with the repository's existing server development environment. It enumerates through the public SDK on an in-memory transport and performs no native edits.

```sh
python tools/experiments/tool_catalog_audit.py --output catalog.json
```
