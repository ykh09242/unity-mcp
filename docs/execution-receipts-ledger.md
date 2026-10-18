# Optional execution receipts

Execution receipts recover observed public invocation status after response loss. They
are separate from Unity jobs, wait Tasks and scene input continuations. Completed means
the public response was observed, including a failure or job-start handle. It does not
mean a Unity job finished. UNKNOWN must never cause an automatic resend. There is no
atomic transaction with Unity and no exactly-once execution guarantee.

## Enablement and request

Receipts are disabled unless the operator sets a dedicated private
`UNITY_MCP_EXECUTION_RECEIPTS_DIR` and `UNITY_MCP_EXECUTION_RECEIPTS_PRIVATE_ROOT=1`.
The latter asserts the private inherited Windows ACL boundary. POSIX ownership and
private modes are also checked. The directory has a stable store ID and OS single-writer
lock; do not share it with another server, user or domain-job journal. Reviewed versions
are FastMCP 4.1.0 and MCP/mcp_types 2.3.0. Explicit enablement refuses other versions.

`get_execution_receipt(action="describe")` reports authenticated availability and store
identity without Unity. A protected ordinary MCP call supplies:

```json
{
  "_meta": {
    "io.github.ykh09242.unity-mcp/execution": {
      "version": 1,
      "store_id": "0123456789abcdef0123456789abcdef",
      "project_hash": "0123456789abcdef",
      "idempotency_key": "create-object-request-0001",
      "retain_result": false
    }
  }
}
```

The current store ID and exact complete project hash are mandatory and grant no authority.
The optional case-sensitive ASCII key matches `[A-Za-z0-9_.:-]{16,128}`. A keyless request
cannot be recovered if its first receipt ID is lost. Unknown fields/types, unsupported
versions and envelopes above 1 KiB refuse protected execution; there is no silent fallback.

The first MCP result preserves content, structured output schema and `isError`, adding
fresh vendor receipt metadata. An exact duplicate with available retained output
rehydrates public typed content, structured content, error flag and application metadata
without invoking callbacks or the original handler again. Otherwise it is a tool error
with receipt metadata and safe pending/unknown/unavailable reason, never a fake success.

Local `/api/command` and `/api/context-command` accept sibling `execution` options and
return sibling `execution_receipt` beside the ordinary body, preserving HTTP status.
Protected envelopes require a complete explicit selector and reject unknown fields.
Retention normalizes a REST result to `{http_status, body}` as structured content.
Both routes use the same coordinator and existing launch-token authentication.

`get_execution_receipt(action="get", ...)` takes store ID, full project hash, exactly one
receipt ID/key and optional `include_result=true`. It returns a stable flat receipt schema;
normalized `content`, `structuredContent`, `isError` and application `_meta` are an optional
nested `result`. The public unavailable-reason field is `result_unavailable_reason`.
There are no lists, arbitrary paths, polling workers, cancellation or command execution.
Local REST exposes the same control operation through either command route, returning
its public MCP result envelope. Authorized lookup/replay works without a Unity target.
Retrying the original MCP tool still requires ordinary SDK tool registration/discovery;
the independent control tool remains available for historical lookup.

## Identity, permissions and worker ownership

Keys belong to `(store ID, principal kind, authenticated stable owner, project hash)`.
Remote ownership uses the current credential lease's stable user ID; local ownership
uses the private installation. Never use a raw API key, session ID or clientInfo as owner.

Fingerprint exact bounded SDK-parsed arguments after existing callback-free structural
and requester checks. Do not run validators, default factories, providers or handlers
merely to deduplicate. Omitted and explicit defaults or coercion-equivalent values may
conflict. Sort object keys recursively; preserve arrays, code points and numeric types.
Hash the public name, exact surface/route, immutable options and supplied FastMCP component
version. Exclude only the separately bound top-level MCP Unity selector; REST native
parameters are not stripped. REST includes the validated supplied semantic envelope,
especially its original expected_project.root. Changed route/root/version conflicts before
live selection; never reconstruct historical root identity from a new Unity registration.
Only digests/lengths, not raw parameters/keys/root context, are persisted as identity.
SHA-256 collision resistance is an engineering assumption.

A new invocation repeats captured session/user/socket/generation and complete selected
plugin/requester public policy checks after durable admission, before SDK callbacks and
handler entry. Every canonical native operation commits its server-classified permission
floor union before send. Existing lease/catalog/project/generation guards run again after
journal I/O. Historical reads check original stored floor, current authority before/after
I/O and before delivery. Weaker/retired credentials for the same owner cannot retrieve
output. Unknown policy/schema versions refuse; schema 2 is the initial exposed format.

Intent is committed before returning the single execution capability. Restarted pending
intent becomes UNKNOWN. Only proven pre-handler, pre-native rejection is not_dispatched.
Cancellation, lost observations and CORE-01 outcome_unknown never become completed success.
SDK exception formatting/masking stays outside the coordinator; unobserved formatted
exceptions are not retained. Late results cannot upgrade a stopped wrapper or rebind it.

Executor selection/dispatch, shared flights, regex/scanner work, sends and journal I/O
keep key/owner/storage/input charges through actual worker completion. Wrapper cancellation
and expiry do not release them. The short ownership lock never spans file I/O. HTTP
journal work runs in owned threads outside the Hub lock, adopting possibly published
intent before cancellation. Shared-flight joiners do not inherit creator-native provenance.
Native protected dispatch requires authenticated generation evidence; unversioned legacy
Unity sockets refuse at that boundary. Reconnect resync does not inherit the receipt.

At most eight bounded recent native attempts retain labeled HTTP UUID versus stdio attempt
ID, exact session/generation, flags and allowlisted causes; the attempt count saturates.
The send boundary is observed without yielding and flushed later. Crash before that flush
still means UNKNOWN: an absent recorded flag never proves no command was sent. No raw
exception, parameter, request context, credential or transport object is execution evidence.
Wait-Task interception forwards receipts onto ordinary invocation; scene asks and MRTR
continuation legs refuse before handler effects. Ordinary explicit scene paths remain.

## Bounds, expiry and privacy

Status/key TTL is fixed two hours from admission; result TTL is ten minutes from observation,
capped by status expiry. Reads/duplicates never renew either. Unexpired keys are never
evicted. Active expired keys stay pinned until all actual owners exit. Uncertain I/O
poisons protected admission until exclusive reopen/reconciliation.

Metadata is at most 8 KiB per record and 16 MiB including staging; records are capped at
1024 globally, 256 per owner and 128 per owner/project. Active calls are capped at 256/32/16
at those scopes. Results are at most 1 MiB each, with 64/16/8 MiB global/owner/project quotas
including two worst-case current+stage slots for active retention. One invocation has at
most 64 owned worker capabilities. Execution only shortens existing deadlines and is capped
at one hour; noncooperative actual cleanup keeps its charges instead of pretending to stop.

Argument identity is bounded by 4 MiB, 65,536 nodes and depth 64. Decode, normalization,
encoding and detached result copies reserve conservative working space before allocation.
Existing ResponseOwner lifetimes retain delivery charges through actual HTTP handoff or
retained stdio flush. Aggregate receipt working space uses the existing 256 MiB response
retained-space ceiling, independently of disk quotas. Oversize retained output keeps truthful
status with result_unavailable_reason=result_too_large.

Result retention defaults off. Arbitrary application output may contain sensitive content,
including application credentials. Opting in stores exact bounded normalized application
output without generic secret detection or silent redaction. Excluded material is private
server credentials/context/transport data and reserved receipt/FastMCP/MCP protocol metadata;
SDK protocol/server metadata is regenerated. Public HTTP status is part of the REST result.

Storage follows existing file-fsync/atomic-replace behavior, with containing-directory fsync
on POSIX. Windows relies on the explicit private inherited ACL boundary and existing Windows
file/replace durability. Normal process loss and OS/power loss have different guarantees.
