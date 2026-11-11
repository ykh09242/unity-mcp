# Shared connections and request ownership

This matrix describes Unity MCP 2.4.1, FastMCP 4.1.8, MCP Python 2.3.0 and
Codex CLI 0.167.1. Codex protocol research is pinned to
[source commit 0a94398fa60c4fac0e7659e4213a0a36e613dd46](https://github.com/openai/codex/tree/0a94398fa60c4fac0e7659e4213a0a36e613dd46).
The MCP client connection and the server-to-Unity native transport are separate
boundaries. Sharing one does not establish the capabilities of the other.

| Contract | Modern MCP 2026-07-28 | Stateful legacy MCP 2025-06-18 | Native Unity transport |
| --- | --- | --- | --- |
| Connection sharing | Codex can pool compatible local stdio servers when `mcp_connection_sharing` is enabled. HTTP clients access an external server independently. | Codex stdio connections stay private in this fixture. HTTP can serve multiple independent MCP sessions. | Multiple server processes coordinate supported work at the Editor through negotiated `native_arbitration_v1`; an MCP client feature cannot enable native arbitration. |
| Editor selection | Pass `unity_instance` in each tool request's arguments. Resource requests use `_meta.unity_instance`; tool metadata does not select an Editor. `set_active_instance` refuses persistent selection. | `set_active_instance` stores an MCP-session default. Explicit per-request overrides preserve that default. Session state is not Codex-thread state. | Selection resolves the project and current transport generation before dispatch. |
| Isolation and authority | Routing is request-local. Retained handles belong to the authenticated principal and resolved project. Host `threadId` metadata creates no additional authorization boundary. | Retained handles also belong to the originating MCP session. Multiple users of one session would share its sticky selection. | HTTP binds the authenticated principal, project and plugin socket/generation. Local stdio uses authenticated listener/session identity. Reconnects revalidate ownership. |
| Idempotence and continuation | Connection pooling does not make tool calls idempotent. Existing execution receipts and handle IDs retain their own contracts. | Session persistence does not authorize replay of possibly dispatched mutations. | `command_resume_v1` binds an original invocation, arguments, Editor-process identity and deadline. Queued work can continue after validation; started work is observed, never blindly replayed. Resume requires arbitration. Clock/origin extensions require resume. |
| Cancellation and progress | The actual Codex fixture proves deadline-triggered cancellation and same-process recovery. This app-server direct-call API has no explicit per-call cancel method. | The fixture proves the same deadline behavior on private stdio and external HTTP. | Cancellation is cooperative and does not undo side effects. `command_progress_v1` reports supported foreground batch progress; a wire attempt does not own the logical reporter lifetime. Actual settlement controls ownership release. |
| Notifications | The client harness retains complete app-server notifications and correlates `thread/closed` to an owner. This proves lifecycle observation, not every host notification UI. | Notifications retain their MCP-session context; the same lifecycle harness observes thread unloads. | Progress is bound to the exact command and generation. Detached or canceled sources cannot report into replacement requests. |
| Authentication | Normal production authentication and permissions remain in force. Two fixture chats use one owned local authority; host metadata is observational. | Stateful handshakes do not replace production authentication. | Native capability advertisement grants no execution permission. Older unauthenticated local compatibility paths do not gain authenticated capabilities. |

A local sole-Editor automatic selection is made anew for a modern request. It is
not a private per-agent default. Agents selecting different Editors must include
the intended `unity_instance` on every tool call. The host must expose request
metadata to select a project on resource requests. No fixture claim implies that
the Codex resource RPC supports every MCP metadata field.

## Actual Codex execution matrix

`tools/check_codex_interop.py` runs five cases, each in a fresh app-server with an
owned temporary `CODEX_HOME`. It uses ephemeral threads, a loopback offline model
provider, file-only credential stores and no model turns or user credentials.
The production CLI, registry, routing, authentication and response middleware run
unchanged. Test-only tools expose process/request observations and bounded payloads.

| Case | Client configuration | Required observation |
| --- | --- | --- |
| Legacy private stdio | Sharing false, ordinary modern feature false, protocol marker absent | Two different thread IDs and server process identities; both use 2025-06-18. |
| Modern private stdio | Sharing false, ordinary modern feature true, `CODEX_MCP_PROTOCOL_VERSION=2026-07-28` | Two different process identities; both use 2026-07-28. |
| Modern pooled stdio | Sharing explicitly enabled by RPC, ordinary modern feature false, protocol marker absent | Two threads use the same PID and per-process startup UUID; both use 2026-07-28. |
| Legacy external HTTP | Sharing false, ordinary modern feature false | Two independent sessions access one authenticated external process using 2025-06-18. Cross-session handles remain unavailable. |
| Modern external HTTP | Sharing false, ordinary modern feature true | Two threads access one authenticated external process using 2026-07-28. Common-domain handles remain accessible. This is not Codex stdio pooling. |

Each case overlaps A's slow tool with B's successful call. B must finish before A's
configured deadline. A must receive a JSON-RPC deadline error, and the fixture must
observe its correlated cancellation and handler cleanup before any owner unload.
Both owners then recover with unchanged process identities. Codex must replace a
forged `threadId` with the actual host thread ID while preserving the separate test
marker. These metadata assertions do not grant project authority.

Distinct retained payloads prevent identical-content deduplication from hiding a
release bug. In pooled stdio and modern HTTP, B reads A's handle before and after
A unloads, verifies every byte's SHA256, releases it, observes `response_not_found`,
and still reads and releases B's independent handle. In private processes and
legacy HTTP, the cross-owner handle is unavailable; each owner releases its own.
After `thread/unsubscribe` and matching `thread/closed`, B continues on its original
process. After both owners unload, a new stdio owner must use a new startup identity;
the externally owned HTTP process remains alive. Additional child processes appear
in the full inventory/reload run's process-start observations; pooling is established
by the identities returned by A and B's actual tool calls.

Basic Codex pooling does not require the optional
`extensions["codex/connection-lease"]` protocol. The fixture checks the server's
actual advertisements and rejects that extension because this server does not
implement its open/release methods. No manual lease call substitutes for host
ownership. See the pinned [upstream sharing tests](https://github.com/openai/codex/blob/0a94398fa60c4fac0e7659e4213a0a36e613dd46/codex-rs/app-server/tests/suite/v2/mcp_connection_sharing.rs).

## Negotiated native diagnostics

The project capability resource reports `server_advertised` separately from
`negotiated`. Its allowlist includes arbitration, resume, resume-clock,
resume-origin and progress alongside the existing state/result/cancel capabilities.
HTTP reports the accepted current plugin registration, after generation and
prerequisite checks. Unknown peer flags are not published.

Stdio diagnostics read only an already-selected, authenticated session whose
connection, listener, project metadata and generation still match. They use
nonblocking locks and perform no discovery, handshake, reconnect or socket I/O.
A busy, missing, retired or inconsistent session reports `negotiated: null` and
unknown status. An inspected authenticated peer that negotiated no supported flags
reports `negotiated: []`. This passive metadata snapshot does not prove that a
remote socket is still reachable; normal command processing owns liveness checks.

## Evidence boundaries

The Windows 2026-11-11 actual-client run covers the five cases above with Codex
0.167.1. Receipts record the exact version and checkout commit, flags, observed
protocols, process identities, complete lifecycle notifications, cancellation and
handle results. Local modified-source evidence is accompanied by a scoped diff and
file hashes; a checkout SHA alone does not identify uncommitted edits.

`tests/http/test_request_routing_state.py` separately exercises concurrent explicit
Editor routing through the official Python client and controlled server inventory.
`tests/test_response_delivery.py` exercises principal/project/session/permission
authority and expiry. `tests/http/test_native_capability_diagnostics.py` exercises
real WebSocket registration and passive stdio snapshot contracts. These are server
contract checks, not proof of actual Codex chats dispatching to two real Editors.

Native arbitration/resume/progress behavior is documented in
[reload continuation](../reload-continuation.md),
[native coordination](../NATIVE_JOB_COORDINATION.md), and existing transport tests.
This task launches no Unity Editor and establishes no new native execution, desktop
sidebar/UI behavior, model-agent behavior, Linux/macOS run or hours-long soak result.
