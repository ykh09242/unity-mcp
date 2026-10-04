---
id: transports
slug: /architecture/transports
title: Transport Modes
sidebar_label: Transport Modes
description: Separate client-server transport, Unity bridge transport, authentication, and MCP session behavior.
---

# Transport Modes

The MCP client connects to a Python server; that server connects to Unity. Transport choice does not by itself determine whether the MCP protocol is stateful.

| Mode | Client to Python | Python to Unity | Typical use |
|---|---|---|---|
| Local HTTP | Shared HTTP MCP endpoint | Unity plugin WebSocket | Multiple clients/Editors on one trusted host. |
| Stdio | Dedicated process over stdin/stdout | Legacy Unity TCP bridge | Clients configured for stdio; one bridge client at a time. |
| Hosted HTTP | HTTPS MCP endpoint through a proxy | Authenticated WSS plugin connection | Operator-managed user-isolated service. |

## Local HTTP

The endpoint is normally `http://localhost:8080/mcp`; Unity connects to `/hub/plugin`. All local control traffic needs the per-launch `X-Unity-MCP-Token`. Start the server before configuring clients, and reconfigure/reconnect HTTP MCP clients after restarting it. Only `GET /health` is public.

Multiple MCP clients can share the server, but transient targets and identities are scoped to requests. Stateful legacy connections can retain session defaults; sessionless MCP 2 requests cannot. `client_id` is not an isolation key. Shared access also does not provide transaction isolation for simultaneous writes to one Editor.

Use [manual configuration](../getting-started/clients.md#manual-client-configuration) instead of copying an unauthenticated URL-only example. Keep the local listener on loopback. Editor LAN-bind/insecure-remote opt-ins are deliberate exceptions, not settings needed for normal setup.

## Stdio

Each MCP client launches `mcp-for-unity` from the installed package's immutable `mcpServerSource`. No local HTTP token header is involved. The legacy Unity bridge permits one client at a time; concurrent stdio processes targeting that bridge can replace a connection. Prefer shared HTTP for concurrent agents.

Selectors can include the legacy port shorthand; do not carry a port selector into HTTP. The package's Claude Desktop configurator chooses stdio regardless of global transport settings. This describes package behavior, not the vendor's complete current capabilities.

## Hosted HTTP

Keep the Python backend private behind actual HTTPS/WSS termination. Hosted mode requires an HTTPS key validator and the explicit `--http-behind-tls-proxy` assertion. This flag does not enable TLS. Every protected request authenticates before parsing; public exceptions are `GET /health` and `GET /api/auth/login-url`.

Plugin registrations, catalogs and routing are scoped to authenticated users. Hosted clients must explicitly target their Editor. Host-local REST/CLI operations, file scanning/recovery, tool sync and focus nudges are unavailable. See [Remote Server Auth](../guides/remote-server-auth.md).

## Change modes safely

Change transport in **Window > Unity MCP (ykh09242)**, ensure the intended server/bridge is running, then regenerate client configuration and reconnect. Discover the new Editor ID; identifiers can differ across transport/project location. Keep secrets out of shared configuration and logs.

See [Instance Routing](./instance-routing.md), [Tool Groups](../guides/tool-groups.md) and [Security And Consent](../guides/security.md). [Upstream v8 migration](../migrations/v8.md) records the historical introduction of HTTP, not the complete current security contract.
