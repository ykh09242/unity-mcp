# Lifecycle sample over real MCP stdio

This small native check reuses a previously generated lifecycle sample. It exercises MCP initialization, tool discovery/activation, exact instance targeting, project resource reads, scenario run/status/cancel and report history through the actual Python MCP server and authenticated Editor loopback bridge.

The bootstrap uses the existing StdioBridgeHost.StartOwned QA seam and the same public synthetic fixture value as TransportEditorIntegrationTests. It does not publish a user credential or touch the Windows credential store, ordinary discovery files or EditorPrefs. The product MCP handlers, HMAC protocol, socket framing, native dispatcher and scenario engine execute normally. Fixture adapters supply only the owned instance metadata and synthetic credential lookup.

## Run

Close the sample Editor first. Use the existing installed Python environment and Editor, and choose a new evidence directory:

~~~powershell
& 'Server/.venv/Scripts/python.exe' -B tools/play_scenario_mcp_sample.py --project '.tmp/CS-20261011-lifecycle-sample-final/project' --editor 'C:\Program Files\Unity\Hub\Editor\6000.0.69f1\Editor\Unity.exe' --output '.tmp/lifecycle-transport-01'
~~~

The sample must already contain its generated scenes and saved scenarios. This command never recreates it. It adds this dedicated test source and stable .meta file to Assets/Tests/Editor; an existing differing fixture is refused. The test namespace is separate from the original four lifecycle tests.

The command owns the Editor process and MCP server subprocess, suppresses normal bridge autostart and telemetry, opens only an ephemeral 127.0.0.1 endpoint, and bounds both query count and process lifetimes. A project/info resource response must identify the exact sample before mutation. An unknown instance must fail rather than fall back to another Editor.

Expected sequence:

| Saved scenario | Repetitions | Expected status |
| --- | ---: | --- |
| sample-normal | 2 | succeeded |
| sample-cancel | 1 | cancelled after an actual MCP cancel call during Game ready |
| sample-retained | 1 | failed / resource_assertion_failed; one SO, subscription and stream retained |
| sample-release | 1 | succeeded; explicit release |
| sample-normal | 1 | succeeded; clean reentry |

The native test additionally requires Menu restoration, no event listeners or retained owners and exact tracker baseline restoration. The six resource checks must include five clean checks and one deliberate negative control.

## Evidence and limits

- initialize.json, tools.json and project-info.json record actual MCP responses.
- calls.json preserves bounded tools/call requests and SDK replies, including cancellation and report history.
- Each terminal report must match the requested scenario and fresh execution nonce. It is copied from the actual Editor persistence path after comparison with the MCP response. Report history must contain exactly one matching complete object for each new job. verification.json records their hashes.
- results.xml requires the native transport body to pass. editor-process/launch.json records the owned PID, exit and termination outcome.
- request.json records the execution nonce, explicit target, command and test source hashes. Old evidence and ordinary sample reports remain intact.

This verifies real MCP stdio -> native loopback execution with a fixture bootstrap. It does not validate normal credential publication/discovery, external client setup, local HTTP/CLI REST, WebSocket transport, Main, Player resource assertions, OS input or screenshots. The original in-process sample tests remain separate evidence. Ordinary license-free CI runs the harness unit tests and reference compilation, not this installed-Editor test.
