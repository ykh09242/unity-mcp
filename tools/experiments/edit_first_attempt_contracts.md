# First-attempt edit failures and their regression checks

The last matched run finished 23 of 24 Unity edits. Five tool calls and two
resource reads failed. Five edits recovered during the same turn. The one
unfinished edit received successful replies throughout. The published ledger
was checked again against the original requests, host events and saved assets.
[Matched study](compact_edit_confirmation.md) · [Case ledger](compact_edit_confirmation_results.json)

| Prior hosted measurement | Default (core) | Full |
| --- | ---: | ---: |
| Edits completed | 12/12 | 11/12 |
| Rejected tool calls | 3/78 | 2/85 |
| Failed resource reads | 2/44 | 0/39 |

## Causes and corrections

- The full catalog saved an extra period in a tooltip. The agent selected the
  sentence-ending period as part of the value, then confirmed its own edit on
  readback. The server's initialize instructions and both skill copies now
  direct clients to compare the preview and saved field with the intended span
  in the original request. An agent can use delimiters, neighboring text and
  sentence structure or ask about a genuinely ambiguous value. The server
  accepts intentional punctuation and does not infer the intended substring.
- In one core rename, three consecutive preparations failed schema validation.
  The outer assertion was an object, then its items used the wrong property
  names, then its path was a scalar. The first tool list and the selected
  operation now share a valid example that pairs the original query with an
  array of assertions. The selected tool's actual draft stays independently
  runnable. Both SDK protocol modes exercised a top-level field and a nested
  field.
- Two full-catalog calls lost their explicitly selected Editor before an edit
  was dispatched. Both target discovery and the complete inventory could return
  empty in the same observation window. The contemporaneous records identify
  an empty inventory but do not establish why the Editor did not respond.
  After a previously observed v2 Editor vanishes from both lookups, stdio waits
  up to one 50 ms polling interval within the request deadline and probes that
  exact target once. The returning listener must agree with its status file.
  Loopback TCP tests cover recovery, a permanently missing target, a conflicting
  listener, cancellation and expiry. The extra probe sends no edit command.
- Two core reads used a host-local file path and an unregistered skill URI.
  The catalog now lists skill files actually advertised on that MCP connection
  and supplies an exact read call. A selected Editor is preserved in the read
  metadata. Tests follow the returned call through the SDK and an authenticated
  HTTP server, check its bytes and exercise an inventory with hidden and
  read-restricted files.

## Isolated Unity readback after the fix

An SDK client drove Unity 6000.0.69f1 using package `05f77b719`, server
`077edd3e7` and bundled skill `5875fd66f`. It opened the advertised
`ui_source` operation from each live catalog and supplied the intended source
span. One control used the original tooltip request and expected
`Opens the handover log`. The other explicitly requested `Ready.`. Each began
with a fresh fixture. For each value, the SDK tried a mismatching draft,
prepared a preview, saved with the preview's source hash and read the file again.

| SDK control on a real Editor | Default (core) | Full |
| --- | ---: | ---: |
| Correct saved edits, checked in Unity and on disk | 2/2 | 2/2 |
| Unexpected tool rejections | 0/18 calls | 0/18 calls |
| Deliberate mismatch checks refused during preparation | 2 | 2 |
| Published skill reads matching the package bytes | 1/1 | 1/1 |

Preview and rejected preparations preserved the original file and its metadata.
Each successful write changed only the intended tooltip. The independent Unity
observation, disk bytes and public readback agreed on all four values. The owned
Editor exited and all 130 shared tool preferences matched their initial hashes.
The local SDK receipt SHA-256 is
`faa81d3d6488937c4ef688d502eb9ee5c490647c7d7d1a5d8cb130cc57bb137b`.

These controls test the public contracts with a caller-supplied interpretation.
The hosted completion rates above still refer to the preceding 24 original-only
turns. Measuring unassisted interpretation on this package requires fresh turns.
