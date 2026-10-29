# Prefab authoring and Editor lifecycle recovery

## Inspect prefab changes before saving

Use `prefab_contents` with `action="modify_contents"` and `preview_only=true` to
apply a proposed edit to isolated contents, compare serialized fields, and unload
without calling `SaveAsPrefabAsset`. For example:

```json
{
  "action": "modify_contents",
  "prefab_path": "Assets/UI/Panel.prefab",
  "preview_only": true,
  "component_properties": {
    "RectTransform": {"m_AnchoredPosition": {"x": 12, "y": 34}}
  }
}
```

`data.saved` is false. `data.preview.changes` compares the imported source asset
with the modified contents. It includes observed load/edit side effects, such as
layout-driven positions, even when only a component reference was requested.
Paths use `$` for the root, child sibling indices, component type/occurrence and
the exact serialized property path. Internal references use those stable paths;
external asset references use GUID and local file ID, not load-local instance IDs.

Inspect `complete`, `truncated`, `unsupported_before` and `unsupported_after`.
Unsupported property types and oversized strings are explicitly excluded from
coverage. Managed references are not traversed. Previews bound objects, property
visits, retained snapshot bytes and output size. Native loading and serialized
accessors remain synchronous Unity operations. Very small float changes are
reported without a rounding tolerance.

This preview does not issue an automatic refresh, generate an apply token, or
predict save-time callbacks. Custom lifecycle callbacks may have external side
effects even without an MCP save. A later edit reloads the contents and may differ.
Review the final asset diff after saving. To save the same request, omit
`preview_only` or set it to false. The existing `preview_modify_contents` /
`apply_preview` pair remains the separate, restricted root-transform workflow
with a one-attempt, source/dependency-checked token.

Serialized Vector2 fields such as `m_AnchorMin` and `m_AnchoredPosition` accept an
object containing exactly finite numeric `x` and `y` values. Child `.x` / `.y`
writes continue to work. Invalid vectors are rejected without saving the prefab.

## Recover from a connection change

The Editor process, project identity and connection session have different
lifetimes. Assembly reloads and reconnects can replace a connection while the
same Editor and server remain open. Operations retain a generation fence so an
admitted edit cannot silently continue against a replacement connection.

For `operation_target_changed`, read `mcpforunity://instances`, confirm the
intended project, and select its current instance. `data.dispatch=not_dispatched`
means the tool was refused before its callback started. Once the callback has
started, earlier steps may have executed: `dispatch=unknown` and
`hint=inspect_state_before_retry` require checking the target before retrying.
The server does not automatically replay mutations. Reviewed `get_test_job` and
`get_compile_job` observations can continue across a connection generation change
for the same project and authenticated principal. Test detail flags, page size,
cursor and job ID remain unchanged. The server rechecks authorization and native
admission, and discards shared snapshots from the old connection generation.

Use the existing job ID after reload; do not start tests or compilation again just
because a status query failed. A positive `get_test_job.wait_timeout` keeps polling
through recognized temporary reload/connection refusals within that original
budget. Permission failures, unknown jobs and uncertain dispatch outcomes are
returned without automatic replay. On expiry, a last observed status is preserved
with `data.observation.status="unavailable"` and `last_known_status=true`; if no
status was received, the response says `job_status="unknown"`. Neither means the
tests failed. Poll the same ID after reconnection, preserving any result cursor.

## Observation budgets for large projects

HTTP `editor/state` uses its own main-thread RPC without an additional ping. Its
default observation budget is **30 seconds**, separate from the **2-second** ping
budget. Set the server environment variable `UNITY_MCP_STATE_TIMEOUT` to a finite
number of seconds to adjust it (0 disables waiting, maximum 300; invalid/nonfinite
values fall back to 30). This changes HTTP state observations only. The existing
stdio command budget remains unchanged. Cached ordinary state can still return
earlier; authoritative reads require a fresh RPC.

An unavailable state response retains the transport reason/phase and includes
`data.observation.editor_health="unknown"` with `next_action="retry_editor_state"`.
It does not establish Editor failure, compiler failure or readiness.

For slow imports/reloads, use `refresh_unity(timeout_seconds=900)` or a suitable
explicit duration (default 300, maximum 86400 seconds). This is a complete refresh
wait budget, not proof of compilation success. Existing mutation readiness waits
use `UNITY_MCP_EDITOR_READY_TIMEOUT` (default 300 seconds). For a test job that is
already running, `get_test_job(wait_timeout=300)` or longer controls the observation
wait, not test execution. A caller/client may impose a shorter deadline. Recovery
and individual polls never renew the caller's elapsed budget. A very long reload
can outlast any finite request; continue with the same job ID afterwards.

## Wait for Play Mode exit before reading Console

A pending stop response acknowledges the transition, not its completion. Read
`mcpforunity://editor/state` until `data.editor.play_mode.is_changing` is false
and `data.advice.ready_for_tools` is true. Then read Console. Pre-dispatch Console
readiness failures include `dispatch=not_dispatched` and
`next_action=wait_for_editor_ready`; their deadline is not restarted. An unanswered
readiness probe alone cannot identify the cause of a busy Editor.

Likewise, `refresh_unity.success=true` acknowledges the refresh/compile request.
Check `data.compilation.outcome`, readiness, and native coordination separately.
`failed` or `unknown` is not compiler success; `source_revision_confirmed` is a
separate source-to-assembly attribution result.

## Distinguish idle flags from native readiness

`editor/state.data.native_coordination` observes the same native ownership barriers
used to admit ordinary tool calls. `blocked`, `reason`, `retryable` and
`recovery_action` remain meaningful even when `is_compiling`, `is_updating` and
`tests.is_running` are all false. Main-thread state reads observe changes before
returning cached content, including when background Editor ticks are infrequent.
Read-only package queries retain their command-specific restrictions; they do not
make every ordinary scene or Console read unavailable.

- `native_job_active` is a transient ownership barrier. Wait for the current native
  work to finish without starting it again. A caller's wait deadline does not cancel
  a native request or discard its pipeline identity. Late matching compilation
  events and recovered test observations can still release ownership safely.
- `recovery_required` means native ownership cannot safely be released from the
  available evidence. Advice reports `ready_for_tools=false`, no automatic retry
  interval, and a concrete recovery action. Capability discovery and doctor's
  readiness check retain the same diagnostics. A verified connection alone does
  not establish readiness.
- `refresh_unity` and CLI `editor refresh` stop waiting when recovery is observed.
  They return `recovery_required`, `retryable=false` and `timeout=false`, preserving
  the acknowledged refresh/available compile evidence. They do not replay refresh
  or restart Unity automatically.

Optional `native_coordination.recovery` identifies the `source`, `phase`, `reason`,
`mode` and explanatory `message`, with `job_id` and `generation` when available.
`recoverable` means the retained observer can still reconcile completion;
`unknown` asks for inspection without asserting that a restart is necessary.
Only `restart_required` recommends `inspect_native_work_then_restart_editor`.
For example, an unfinished compilation checkpoint whose pipeline context was lost
across reload, a build whose completion observer disappeared, or a Package Manager
request whose handle is gone cannot be released using idle flags alone. The message
explains that missing evidence. Legacy peers without these details still remain
blocked but ask for `inspect_native_work`, rather than inferring a restart cause.

A temporary test snapshot failure does not erase a retained run GUID. A later
coherent observation of that same run restores tracking. A retired test run with
its callback owner retained waits for terminal completion; losing attribution to
the run remains a separate recovery condition. Focusing the Editor or observing
an assembly reload is not itself evidence that a restart is required.

Compilation cancellation or an internal compiler task failure can omit Unity's
public finish event. When the original Bee task remains identifiable, its terminal
state is recorded before reload and the reservation releases after native quiescence.
The compile job remains `unknown` with `compilation_canceled` or
`compilation_faulted`; recovery does not imply compiler success or repeat the request.
An older checkpoint that already lost its context and completion evidence cannot
be repaired from idle flags or an unrelated later compilation.

Inspect the active Test Runner, build, import or compilation work and save relevant
logs before deciding to restart the Editor. An idle sample does not prove that an
interrupted native operation released its resources. Likewise, multiple candidate
compile cycles remain ambiguous; readiness does not turn `unknown` into a successful
or source-confirmed compilation.

`tests.last_run` retains the observed test result and total/passed/failed/skipped
counts when a completion callback supplies them. Duplicate cleanup preserves that
result and completion time. A new run clears the prior summary; without an observed
result (including interruption or loss of managed state), the summary remains
unknown rather than borrowing old counts. Use `get_test_job` with its existing ID
for retained job history and detailed results.

## Tools interrupted by an Editor reload

A reload can interrupt the managed response while native work has already started.
When the transport reports an unknown outcome, inspect the existing job or resulting
state after reconnection; do not repeat a mutation solely because its response was
lost. Supported job observations keep the original job ID and wait deadline.

Connection shutdown retires queued WebSocket commands before cancellation callbacks
finish. Work that already entered its handler retains its normal completion and
native-ownership rules. With negotiated command continuation, queued requests keep
their identity and deadline and can be re-admitted after validation. Completed
requests return retained results; started requests whose outcome is unknown are
never replayed. Explicit cancellation is a separate terminal state.

Built-in queued builds and test admissions retain declarative checkpoints under
their original job IDs. Native invocation is fenced before it starts, so a reload
cannot cause an invoked build or test to run again. An interrupted invoked build
remains uncertain and keeps its native fence. See
[command continuation](reload-continuation.md) for the state rules, validation,
transport compatibility and bounded-result behavior.

Repeated reloads preserve a pending stdio bridge resume even when the bridge has not
restarted between passes. An explicit stop or transport switch still clears that
intent. Connection recovery does not replay a possibly started tool operation.

## Recursive runtime data during hot reload

Unity hot reload serializes eligible private fields and auto-property backing
fields even without `[SerializeField]`. A runtime scratch list that points into
a recursive data model should use `[System.NonSerialized]` when it does not need
to survive reload. Do not change persisted data to `[SerializeReference]` merely
to hide a warning from a transient cache. See the
[Unity 6000.0 serialization documentation](https://docs.unity3d.com/6000.0/Documentation/Manual/script-serialization-how-unity-uses.html).

The separate "Objects are trying to be loaded during a domain backup" message
does not identify its caller when Unity supplies no stack. Check the reload log
and serialization callbacks before attributing it to MCP or to background focus.

## Understand missing Console locations

Unprojected structured Console entries (`json`/`detailed`, including tail entries)
include `diagnostics` alongside the original fields. Explicit `fields` projections
and plain messages retain their existing shape. Source values come directly from
Unity's `LogEntry.file`, `line` and message/UTF-16 stack boundary; see the
[Unity 6000.0 source](https://raw.githubusercontent.com/Unity-Technologies/UnityCsReference/6000.0/Editor/Mono/LogEntries.bindings.cs).

- `location`: `available`, `partial`, or `not_provided_by_unity`.
- `stack_trace`: `available`, `not_requested`, `not_provided_by_unity`, or
  `unavailable`. The last value means the compatibility heuristic found no frames
  without a valid native boundary; it cannot establish absence in Unity.
- `stack_trace_source`: `unity_utf16_boundary` or `frame_heuristic`.

A whitespace-only stack counts as missing diagnostics, while the original
`stackTrace` value is preserved. For `not_requested`, request
`include_stacktrace=true`. When Unity provides no source, MCP cannot reconstruct
the caller; inspect Editor.log and the surrounding reload activity. This metadata
does not change Unity's global stack-trace logging settings or suppress errors.
