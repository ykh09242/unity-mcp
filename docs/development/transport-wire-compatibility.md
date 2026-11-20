# Transport wire compatibility

The hosted managed check exercises the negotiated command-resume v1 contract in four pairs. Each Python revision sends commands to each C# revision, consumes the resulting native admission and resume receipts, and reports its interpretation. The stable Editor comes from the release tag used by the stable-workflow upgrade check. The stable server comes from the release package's `mcpServerSource` commit, which can differ from the tag. The candidate package and server both come from the candidate commit.

Run from a checkout with the stable tag, its pinned server commit, the candidate commit, the locked Server environment and .NET SDK 11. The fixture uses the SDK's Newtonsoft.Json assembly. The output directory can be new or empty of wire evidence.

```sh
uv run --project Server --locked python tools/check_transport_wire.py --output .tmp/transport-wire
```

The Python peer uses `CommandResumeInvocation` and `NativeArbitrationRequest` to serialize request bindings. C# loads the stdio command with `TransportJson.DeserializeCommand`, runs the real journal and emits both stdio and WebSocket responses with `TransportCommandResponse`. Python compares the stdio decode with the WebSocket result model and validates the negotiated envelopes with the selected version's actual readers. The fixture supplies an in-memory journal backing store, a controlled clock, a transport delivery boundary and a write-counting handler. Licensed Editor execution and real socket integration have separate coverage.

| Case | Required observation |
| --- | --- |
| Required request action omitted or null | Native refuses the request. Python rejects the unbound receipt. |
| Unrecognized request action | Native returns a bound unavailable receipt. Python asks the caller to inspect state before retrying. |
| Receipt state omitted, null or unrecognized | Python rejects the receipt. The original invocation has no additional write. |
| Completion reply lost after the write | The sender has no terminal observation until reconnect. Resuming the original identity returns the retained result, including the null field and date-shaped string. |
| Running request canceled before its handler settles | A late handler result can supply facts. The journal and sender still report canceled. |

`comparison.json` records the package and server commits, fixture hashes, each pair's observations and machine-readable differences from the stable/stable pair. A changed human-readable error message alone does not fail the comparison. A failed expected outcome or a semantic difference returns a nonzero status.

## Bounded lifecycle comparison

For the candidate pair, traces are enumerated by increasing length through six events. The first event is a request or cancellation. Each trace permits up to two request deliveries, two handler-start attempts, one cancellation, one disconnection and one completion. A completion requires an earlier start attempt. A request following disconnection opens the next delivery generation. Responses addressed to an older generation are discarded.

After every prefix, a separate status observation checks the actual Python interpretation and native journal against the expected state, retry eligibility and handler write count. Status observations use the same invocation identity and leave the active Python sender's knowledge intact. A second handler-start attempt must never produce a second write. The original sender must preserve any terminal state it observed even when another completion arrives.

The first model divergence saves its event prefix and observed difference in `reproducer.json`. Breadth-first order makes this the shortest failing trace within the documented event bounds. Replay checks the recorded source commits and writes evidence to a fresh destination.

```sh
uv run --project Server --locked python tools/check_transport_wire.py --replay .tmp/transport-wire/reproducer.json --output .tmp/transport-wire-replay
```
