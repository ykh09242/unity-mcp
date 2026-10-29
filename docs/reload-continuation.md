# Command continuation across Editor reloads

An Editor domain reload suspends communication; it is not a user cancellation.
When both peers negotiate `command_resume_v1`, the original MCP caller keeps its
request identity and deadline while the same Unity process reconnects. Public
requests that have not entered their callback retain their scheduler position.
The server rechecks the project, principal, Editor process, permissions and tool
availability before continuing.

Rebinding a public queue entry also retains its original domain boundary. Peers
negotiate `command_resume_origin_v1` before a previously queued callback can send
its first native command after reload. The Editor applies the same reference and
dependency checks used for an existing journal continuation. A peer that lacks
this capability cannot safely continue that cross-domain queue entry.

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
This includes component properties inside asset modifications and supported
`entityID` reference forms, as well as canonical tools and focused capability aliases.

Execution receipts follow the original invocation across a validated reconnect;
they cannot authorize a different invocation or a replacement Editor process.
An invalid transport proof after dispatch leaves an unknown outcome. Inspect the
existing job or resulting state before requesting another mutation.

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

Build continuation rechecks foreign native ownership before preparation and before
native invocation. Excluding the current build's own reservation never excludes
another test, compilation, package mutation, or lost build owner. Profile builds
retain their effective scene source and AssetBundle manifest option. Batch
cancellation and report metadata survive restoration under the original ID.
Active build capacity is separate from bounded terminal history. Compact terminal
IDs retain their original recovery deadline; history exhaustion is reported before
accepting another build, without evicting live or uncertain work.

Queued test runs retain their exact filter options and original initialization
deadline. A persisted admission phase distinguishes waiting for Test Runner access
from calling native execution. Reload resumes the former under the same job ID;
the latter uses the existing run GUID and callback recovery. Explicit cancellation
is persisted before it is acknowledged. An incomplete persistence transition blocks
automatic continuation instead of guessing that the job is safe to restart.

When native test control is unavailable, elapsed time alone cannot prove that a
run ended. Restoration retains the job and an unknown owner until correlated
completion evidence is available. A queued admission that never reached native
execution can still expire or be explicitly canceled without inventing an owner.

Package jobs distinguish observed installed state from the result of a particular
Package Manager request. Losing a request handle leaves an unknown outcome even
if the package already matches the desired state. List/search query IDs and bounded
completed results remain observable after reload. Lost requests are not replayed.

Queued navigation bakes can resume when their saved, clean scene target, object
identity, configuration and backend still match the original reservation. They
retain the same job ID and queue deadline and wait for native admission. Unsaved,
dirty, changed or missing targets require a fresh explicit request. A bake that
entered native execution is never automatically repeated.

Asset generation keeps supported pre-submission intent in bounded Editor-session
storage. The disk job journal does not store prompts or credentials. Reload can
reconstruct an unsubmitted request only while its original session intent exists
and its inputs, configuration, output and current tool authorization still match.
Credentials are resolved again at submission. Secret-bearing or unsupported remote
inputs are not retained for continuation and report a precise refusal instead.
Submission, explicit cancellation, expiry and terminal completion discard the
session intent. A submitting or uncertain request is never automatically resubmitted.

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

With `command_resume_origin_v1`, a rebound public callback also sends
`origin_generation` inside `resume`, including on its preflight requests. Its
presence requires cross-domain semantic validation even for a new journal entry.
The journal binds it to the invocation so subsequent attempts cannot remove or
replace it to weaken that validation.

The response wraps the existing native-arbitration envelope in
`{command_resume: {version, invocation_id, editor_session_id, state}, payload}`.
Stored business responses receive the current attempt's arbitration nonce and
domain binding; old wire proofs are never reused. Waiting receipts are validated
against both envelopes. A handler-supplied lookalike response is not a transport
authorization to retry.
