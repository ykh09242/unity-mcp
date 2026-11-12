# Transport lifecycle diagnostics and recovery

Read `mcpforunity://editor/state` (`get_editor_state`) to inspect `command_ownership`
and `command_resume`. These bounded fields refresh with Editor observations and
remain available through the existing state-push cache. Updating them does not
rebuild the expensive Editor activity snapshot. Observation never releases an
owner or replays a command.

## Final WebSocket delivery

A queued command has a two-second final delivery budget, starting when its final
response is ready. The send cancellation token is separate from the connection's
generation token, so an expired or canceled command can still report its outcome
without writing to a replacement connection. Waiting for the socket writer and
each physical frame consume this budget. Synchronous serialization is not
preempted, and no serialization or send task is abandoned.

The original request deadline still governs execution admission and response
waiting. A final delivery timeout does not release an unfinished handler or its
cancellation callbacks. FIFO and native mutation ownership remain held until those
actual owners settle. Once they settle, an unavailable writer cannot retain the
command queue indefinitely. A missing result remains an uncertain delivery; never
replay a mutation just because its response was not received.

## Retained native owners

`command_ownership` includes the generation, read/write counts, and up to 16 oldest
owners. Each owner reports a bounded trusted built-in tool name, mode, monotonic
age in milliseconds, phase (`handler` or `cancellation_settlement`), and whether
cancellation was requested. Custom or unrecognized handlers use
`custom_or_unknown`; payloads, command IDs, paths and exception messages are not
included. `owners_omitted` counts additional owners.

`wait_for_owner_settlement` means the handler or cancellation callback still owns
its lease. Inspect the named tool and its own job/status diagnostics where
available. These fields do not authorize forced lease release or unknown-outcome
replay. A canceled request can legitimately retain ownership while cleanup runs.

## Resume journal health

`command_resume.state` is `available` or `unavailable`. Failure reasons are fixed
categories: `storage_read_failed`, `snapshot_invalid`,
`snapshot_serialization_failed`, `snapshot_size_limit`, or
`persistence_write_failed`. Exceptions and stored command content are not exposed.
Unavailable command replies also include `command_resume_health`.

When `recovery_allowed` is true, restore the storage condition first, then select
**Window > Unity MCP (ykh09242) > Recovery > Retry Command Resume Persistence**.
This explicitly retries persistence of the complete validated in-memory state.
It preserves every entry, original deadline, session identity, and active owner.
It does not prune, clear, reread storage, release leases, or schedule any work.
Recovery succeeds only after the write succeeds. New admission does not retry
recovery automatically.

When the initial snapshot cannot be read or validated, `recovery_allowed` is false
and `recovery_action` is `inspect_storage_without_reset`. An empty or partially
parsed snapshot cannot prove which duplicate fences exist. The recovery command
therefore refuses to overwrite storage. Preserve the evidence and investigate the
storage failure; do not clear SessionState or replay an uncertain command.

## Capacity and sustained workloads

The journal retains at most 128 records and a 2 MiB snapshot. Completing a mutation
does not immediately free its duplicate fence. Only an expired record whose
runtime owner has released can be reclaimed during a subsequent admission.

`capacity` reports `occupied`, `active`, `terminal`, `unexpired`,
`expired_active`, `reclaimable`, `available`, `last_persisted_bytes`, and both
limits. `earliest_reclaim_unix_ms` and `retry_after_ms` consider only released
records. They never promise when an active handler will settle.

At saturation, `wait_for_expiry` identifies an expiring released fence;
`wait_for_owner_settlement` means every retained record still has an owner.
`available` includes expired released entries eligible for the next admission.
Use deadlines appropriate to the actual operation and pace new mutations using
these diagnostics. Reusing identities or deleting live fences is not capacity
management. The limit remains 128: the regression workload verifies three bursts
of 160 commands, each admitting 128 and refusing 32, then recovering at expiry
without losing completed results or admitting duplicate execution.
