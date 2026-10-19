# Foreground command progress

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
