# Command and operation progress

Unity MCP can report optional progress for an active `batch_execute` request. A client
requests MCP progress through the request's standard progress token; no additional
tool argument is needed. The Unity connection must also negotiate
`command_progress_v1`.

## What the count means

The notification's `progress` is the number of batch entries that have settled into
the result list. Its `total` is the original admitted batch size. Successful,
failed, and disabled entries count when attempted. Entries marked `not_run` after
fail-fast termination do not count. A fail-fast batch can therefore finish with
progress below its total.

Progress is not an elapsed-time estimate or a prediction of remaining work. A
single long-running batch entry may produce no notification until it settles.
Validation-only requests and tools that return a background job handle do not
claim this batch progress producer.

Notifications are optional and may be coalesced or dropped. The final tool result
remains authoritative and retains its existing format. A missing final progress
notification does not indicate that the batch failed.

## Lifetime and compatibility

Reporting belongs to the original foreground request and its admitted Unity
command. It stops when that request returns, is cancelled, loses its authority,
or reaches its original deadline. A receipt lookup, replay, reconnect, or detached
job does not acquire the original request's reporter.

Closing progress does not prove that already-started Unity work stopped. Existing
cancellation, execution-receipt, unknown-outcome, and no-automatic-resend rules still
apply. Progress does not provide rollback or exactly-once execution.

Clients without a progress token and peers without the negotiated capability use
the ordinary command/result path. Legacy authentication does not enable progress.
MCP progress tokens stay on the Python side and are never included in Unity
commands.

## Transport behavior

WebSocket progress is associated with the existing command ID and the exact
authenticated connection, session, and generation. A notification cannot complete
the command or attach a new observer to old work.

For authenticated stdio, an opted-in request sends one bounded
`command_progress_begin` frame immediately before the unchanged command frame,
under the same connection lock. The association is consumed once. It shares the
original frame-read budget; it does not start another read timeout. Repeated,
malformed, or incomplete associations close that connection.

Optional stdio responses use an `MCPG` payload prefix inside the existing length
frame. Progress and final results share one serialized writer. For an accepted
progress association, both use the original command deadline. The ordinary
no-prelude command retains its existing timeout-response behavior. A partial frame
closes the affected connection and follows the existing unknown-outcome handling;
it does not trigger a command resend.

## Bounds

Each reporter keeps one latest update and at most one in-flight notification.
Notifications are limited to ten per second. A notification send has a 200 ms
ceiling, further limited by the original command deadline.

Admission is limited to 256 active reporters. Accepted tokens are strings of at
most 256 UTF-8 bytes or integers within the signed 53-bit range; unsupported tokens
disable optional reporting without rejecting the tool call. Native progress
messages are bounded to 512 bytes and 256 incoming optional frames per command.
Counts must increase and match the original admitted total, from 1 through 100.

These bounds limit reporting overhead. They do not extend command execution
deadlines or change batch admission limits.

## Long asset operation observations

Asset generation/import status responses add `progress_detail` from the existing
job owner. This is a pull snapshot and does not borrow the foreground MCP reporter
or send notifications after the starting request has returned. Closing a client
has no subscription to retain; the next explicit status request reads the latest
primitive snapshot. Older clients can ignore this additive field.

The phase is one of `queued`, `preparing`, `submitting`, `waiting_provider`,
`downloading`, `importing`, or `reconnecting`. `phase_elapsed_seconds` measures
local time in that phase. Downloading exposes `completed` bytes staged and hashed,
`total` only when the HTTP content length is known, and `unit="bytes"`. Counts are
monotonic within `phase_sequence`; entering another phase or another physical
transfer increments that sequence and resets its counts. Values use 64-bit counters
and preserve the existing 8 GiB download and one-hour download limits.

Preparation, provider processing, and indivisible Unity import calls have unknown
work totals. The historical `progress` field remains compatible but represents
provider evidence, not an overall completion estimate. The status message no longer
formats it as an overall percentage. No estimated completion time is produced.

`cancellation_requested` is intent. `active` describes this telemetry cell's
lifetime, not proof that all native/provider resources have stopped. The ordinary
job state, recovery evidence and owner cancellation rules remain authoritative.
Finalization/reload closes the cell so late workers cannot update it. Progress is
not persisted as an execution checkpoint: restored terminal/legacy jobs have null
`progress_detail`, and safely resumed jobs start a new session-local observation.
A snapshot does not authorize resume, retry, or import.

Each admitted job owns one small cell containing only primitives and a lock. The
existing 64 KiB stream loop updates it without per-chunk allocation or event
queues; JSON is created only for a requested status. Existing job retention bounds
apply. The native regression fixture measures 200,000 counter updates separately
from actual staged-download tests, using `GC.Alloc` instrumentation. Timing is
advisory; assertions cover zero update allocations, monotonic large counters,
unknown totals, late-worker closure, and identical download results with and
without observation. This does not claim measured multi-gigabyte network speed.
