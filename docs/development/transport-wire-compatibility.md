# Transport wire compatibility

The [hosted managed check](../../.github/workflows/transport-wire-compatibility.yml) exercises exact JSON value preservation and the negotiated command-resume v1 contract in four pairs. Each Python revision sends commands to each C# revision, consumes native results, and reports its interpretation. The stable Editor comes from the release tag used by the stable-workflow upgrade check. The stable server comes from the release package's `mcpServerSource` commit, which can differ from the tag. The candidate package and server both come from the candidate commit.

Run from a checkout with the stable tag, its pinned server commit, the candidate commit, the locked Server environment and .NET SDK 11. The fixture uses the SDK's Newtonsoft.Json assembly. The output directory can be new or empty of wire evidence.

```sh
uv run --project Server --locked python tools/check_transport_wire.py --output .tmp/transport-wire
```

## Exact values in both directions

Each pair starts two independent round trips. The [Python fixture](../../tools/tests/fixtures/transport_wire/wire_preservation.py) sends file IDs just outside the exact IEEE-754 integer range. The [C# fixture](../../tools/tests/fixtures/transport_wire/WirePeer.cs) separately originates a result with the two signed 64-bit endpoints. Both payloads include paired Hangul asset paths in NFC and NFD, plus one reference with explicit JSON null and one with the optional member absent. Python sends the decoded C# result back as command parameters, which C# reads again.

Python writes WebSocket requests with the production [command envelope and encoder](../../Server/src/transport/payload_processing.py). C# uses [the transport JSON parser](../../MCPForUnity/Editor/Services/Transport/TransportJson.cs) and the same parameter access used by [`WebSocketTransportClient`](../../MCPForUnity/Editor/Services/Transport/Transports/WebSocketTransportClient.cs). C# sends results through [`TransportCommandResponse`](../../MCPForUnity/Editor/Services/Transport/TransportCommandResponse.cs) on both result codecs. Python reads them with [`PluginHub` and `CommandResultMessage`](../../Server/src/transport/plugin_hub.py) and the [bounded stdio decoder](../../Server/src/transport/legacy/unity_connection.py). The round trip uses the WebSocket command and result codecs. The stdio result check observes the same independent input through the stdio codec. Licensed Editor execution and real socket integration have separate coverage.

All four pairs must preserve these values. These cases use the shared JSON protocol and require no opt-in capability. A missing optional member is an input case. A missing required codec, rejected frame or changed value fails the check. Capability-specific behavior belongs to cases that can verify actual negotiation.

In the workflow's `transport-wire-compatibility` artifact, `comparison.json` records the selected commits, fixture hashes and raw frames under each pair's `preservation` receipt. On a value mismatch it records the first failed boundary, the first differing JSON Pointer and the expected and observed JSON values. `<missing>` denotes an absent field. Unicode escapes in the differing values make composed and decomposed code points visible. A decoder that rejects a frame records its error and retains the supplied frame.

## Command continuation

The Python peer uses `CommandResumeInvocation` and `NativeArbitrationRequest` to serialize request bindings. C# loads the stdio command with `TransportJson.DeserializeCommand`, runs the real journal and emits both stdio and WebSocket responses with `TransportCommandResponse`. Python compares the stdio decode with the WebSocket result model and validates the negotiated envelopes with the selected version's actual readers. The fixture supplies an in-memory journal backing store, a controlled clock, a transport delivery boundary and a write-counting handler.

| Case | Required observation |
| --- | --- |
| Required request action omitted or null | Native refuses the request. Python rejects the unbound receipt. |
| Unrecognized request action | Native returns a bound unavailable receipt. Python asks the caller to inspect state before retrying. |
| Receipt state omitted, null or unrecognized | Python rejects the receipt. The original invocation has no additional write. |
| Completion reply lost after the write | The sender has no terminal observation until reconnect. Resuming the original identity returns the retained result, including the null field and date-shaped string. |
| Running request canceled before its handler settles | A late handler result can supply facts. The journal and sender still report canceled. |

The comparison also records each pair's continuation observations and machine-readable differences from the stable/stable pair. A changed human-readable error message alone does not fail the comparison. A failed expected outcome or a semantic difference returns a nonzero status.

## Bounded lifecycle comparison

For the candidate pair, traces are enumerated by increasing length through six events. The first event is a request or cancellation. Each trace permits up to two request deliveries, two handler-start attempts, one cancellation, one disconnection and one completion. A completion requires an earlier start attempt. A request following disconnection opens the next delivery generation. Responses addressed to an older generation are discarded.

After every prefix, a separate status observation checks the actual Python interpretation and native journal against the expected state, retry eligibility and handler write count. Status observations use the same invocation identity and leave the active Python sender's knowledge intact. A second handler-start attempt must never produce a second write. The original sender must preserve any terminal state it observed even when another completion arrives.

The first model divergence saves its event prefix and observed difference in `reproducer.json`. Breadth-first order makes this the shortest failing trace within the documented event bounds. Replay checks the recorded source commits and writes evidence to a fresh destination.

```sh
uv run --project Server --locked python tools/check_transport_wire.py --replay .tmp/transport-wire/reproducer.json --output .tmp/transport-wire-replay
```
