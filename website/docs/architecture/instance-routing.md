---
id: instance-routing
slug: /architecture/instance-routing
title: Unity Instance Routing
sidebar_label: Instance Routing
description: Request targets, legacy defaults, local discovery, hosted ownership, and failure boundaries.
---

# Unity Instance Routing

Routing must identify the intended Editor before dispatching an operation. The historical wrong-project report [upstream #1023](https://github.com/CoplayDev/unity-mcp/issues/1023) motivates this boundary; current fork behavior is described below. Task-level examples live in [Multi-Instance Routing](../guides/multi-instance.md).

## Identity and discovery

Read `mcpforunity://instances` and use the returned `Name@hash`. Path-derived hashes are not portable: moving a project or switching transport can change its identifier. Do not synthesize IDs or assume the same hash length across transports.

Stdio discovers Unity status/port registry files and probes candidates through the legacy bridge. Discovery caches include empty results and tolerate mixed timestamp formats/disappearing files. HTTP uses authenticated live plugin registrations. Sockets awaiting registration also consume admission capacity; each socket registers once, and reconnects replace the same owner's project session rather than accepting repeated registration on one socket.

## Selection order

1. An explicit tool `unity_instance` or resource `_meta.unity_instance` applies to the current request.
2. A stateful legacy session may use its retained active-instance default. Sessionless requests cannot retain this state.
3. Local operation may infer a single connected Editor. Ambiguous local selection fails and asks for a target. Hosted operation never uses this automatic fallback.

Explicit targets and authenticated identities are request-scoped and not serialized into shared session state. Persistent defaults are keyed by the legacy session, never a caller-supplied `client_id`, a user-global selection or a process-global fallback. A per-call override must not change the session default.

Exact hashes are preferred before prefix matching. Ambiguous selectors fail; stdio's port shorthand is not an HTTP project selector. On reconnect, preserve the requested target rather than silently using another connected Editor.

## Hosted ownership

API-key validation supplies the request's user identity. The selected plugin must belong to that user, and discovery/catalog visibility is filtered accordingly. Built-in invocation must also respect the selected plugin's enabled catalog. Registration or session group actions cannot grant access to another tenant's tools or bypass Unity-disabled operations.

Hosted calls never fall back to the host-local stdio pool, external asset scanning, script recovery or focus helpers. Local REST routes are disabled in hosted mode. [Remote Server Auth](../guides/remote-server-auth.md) documents deployment and validator requirements.

## Lifetime and failure boundaries

Registration, pending commands, payloads and retained results have per-session/user/global budgets. Heartbeat failure/disconnect cancels pending work and releases owned resources. Stale socket/message callbacks must not overwrite replacement connection state.

A target-resolution error before dispatch means that operation was not sent to Unity. After dispatch, a transport timeout or lost response is not evidence of cancellation/rollback. Inspect state or poll the returned job ID before repeating a write; there is no general exactly-once guarantee.

Local HTTP CLI requests are stateless and require `--instance` or `UNITY_MCP_INSTANCE` when a target is needed. `instance set` explains this instead of reporting false persistence. See [CLI](../guides/cli.md).

## Implementation and verification

Python selection/dispatch lives under `Server/src/transport/`; tool/resource wrappers resolve the request context before sending. The Unity transport owns the actual Editor connection and command execution. Unit/fixture tests, compile matrices, and native Editor runs establish different kinds of evidence; see [Testing](../contributing/testing.md). No historical load-test number is a performance guarantee for this release.
