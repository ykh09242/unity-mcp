# Command continuation across Editor reloads

An Editor domain reload suspends communication; it is not a user cancellation.
When both peers negotiate `command_resume_v1`, the original MCP caller keeps its
request identity and deadline while the same Unity process reconnects. Public
requests that have not entered their callback retain their scheduler position.
The server rechecks the project, principal, Editor process, permissions and tool
availability before continuing.

## Execution states

| Recorded state | Recovery behavior |
| --- | --- |
| Queued, handler not entered | Re-admit the same request after validation. |
| Executing in the current domain | Observe that invocation; do not invoke again. |
| Executing in a lost domain | Report an unknown outcome; inspect its job or resulting state. |
| Completed, result retained | Return the saved business response without invoking the handler. |
| Completed, result too large to retain | Deliver the initial full result; a later recovery reports `completed_result_not_retained`. |
| Explicitly canceled | Preserve cancellation; never restore the queued invocation. |
| Deadline expired | Stop waiting and prevent a new admission; reload does not renew the deadline. |
| Missing, corrupt or unavailable checkpoint | Refuse automatic execution. |

The Editor writes the execution fence before entering the handler and the terminal
checkpoint before publishing its response. A live claim also prevents simultaneous
deliveries of the same invocation from scheduling two handlers. A lost connection
retires its queued admission but does not write a user-cancellation tombstone.
The original sender must explicitly resume the request; restoring the transport
journal does not schedule commands by itself.

Cancellation stops the sender's continuation loop. When the original Editor is
reachable, it also records a cancellation tombstone and requests cooperative
cancellation of live work. Cancellation cannot undo effects that already occurred,
and a disconnected peer cannot acknowledge a cancellation it has not received.
Actual handler settlement still controls native ownership and lease release.

## Revalidation and limits

Arguments are preserved, not regenerated from new defaults. An invocation ID cannot
be reused with different arguments or a different deadline. Cross-domain requests
with process-local object references are refused rather than rebound to a new
object. Existing stale object handles remain stale. Arbitrary code and custom
handlers cannot automatically cross a domain boundary when their dependencies
cannot be validated. A batch that already entered its handler is not replayed.
Component assignments with ambiguous numeric object references also require a
fresh request after reload; ordinary numeric distances and positions remain valid.

The journal is local to the Editor process and uses `SessionState`. It stores
fingerprints, phases and bounded results, not the original command arguments.
It retains at most 128 entries, a 2 MiB snapshot and 8 KiB per cached result.
Unexpired execution fences are never evicted to make room. Capacity exhaustion
refuses new journal admissions; oversized completed results retain a no-replay
marker. An Editor restart changes the journal identity. The server's pending
requests are not a persistent queue across an MCP server restart.

Older peers keep the existing conservative behavior: a possibly dispatched
mutation whose response is lost has an unknown outcome and is not automatically
retried. The existing `native_arbitration_v1` proof is unchanged.
Reviewed read and control operations retain their existing observation paths
without consuming mutation-journal entries. Frequent job polling therefore cannot
fill the journal reserved for commands that may change state.

## Accepted asynchronous jobs

A tool response containing a job ID is an acknowledgment of that job. Recover by
the same ID; resubmitting the original tool would create another job.

Queued built-in builds keep declarative checkpoints for frozen options, scenes,
output paths, profile identity and validated preparation. Batch checkpoints retain
their child cursor and completed children. Only proven uninvoked work can be
reconstructed. Platform/backend preparation is separated from native invocation,
and stale plans or profiles fail revalidation. An invoked build with a lost
completion observer retains its existing recovery fence.

Queued test runs retain their exact filter options and original initialization
deadline. A persisted admission phase distinguishes waiting for Test Runner access
from calling native execution. Reload resumes the former under the same job ID;
the latter uses the existing run GUID and callback recovery. Explicit cancellation
is persisted before it is acknowledged. An incomplete persistence transition blocks
automatic continuation instead of guessing that the job is safe to restart.

These checkpoints do not make arbitrary started side effects replayable. An
interrupted child inside a running batch, arbitrary code, or native work with lost
ownership requires inspection. Readiness and idle flags cannot establish that a
previous side effect did not happen.

## Transport contract

WebSocket registration and authenticated stdio handshakes advertise
`resume_generation`, a domain-stable Editor-process identity, with
`command_resume_v1`. Each request carries a separate `resume` object containing
`version`, `invocation_id`, `editor_session_id`, `deadline_unix_ms` and `action`.
Actions are `new`, `resume`, `status` and `cancel`. A missing `resume` checkpoint
cannot create new work.

The response wraps the existing native-arbitration envelope in
`{command_resume: {version, invocation_id, editor_session_id, state}, payload}`.
Stored business responses receive the current attempt's arbitration nonce and
domain binding; old wire proofs are never reused. Waiting receipts are validated
against both envelopes. A handler-supplied lookalike response is not a transport
authorization to retry.
