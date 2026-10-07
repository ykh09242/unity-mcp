---
id: multi-instance
slug: /guides/multi-instance
title: Select The Unity Editor
sidebar_label: Multi-Instance Routing
description: Discover Editor IDs and use request-scoped targets, with legacy session defaults only when supported.
---

# Select The Unity Editor

Always confirm which Editor receives a request, especially when comparing projects or Editor versions.

## Discover IDs

Read `mcpforunity://instances`. Use the returned `Name@hash` rather than constructing an ID from a project name. Exact hashes take precedence over ambiguous prefixes; unambiguous prefixes are supported, and stdio also supports port selectors.

Local operation may auto-select a single Editor. Multiple local Editors require a choice; hosted operation always requires an explicit authorized target. Discovery in hosted mode is filtered by authenticated user.

## Target each request

For tools, include the selector in arguments:

```json
{
  "action": "get_hierarchy",
  "unity_instance": "MyGame@a1b2c3d4"
}
```

These are example arguments to `manage_scene`; replace the ID with a discovered one. For an MCP `resources/read` request:

```json
{
  "uri": "mcpforunity://project/info",
  "_meta": { "unity_instance": "MyGame@a1b2c3d4" }
}
```

The target lasts only for that request. This is the portable pattern for MCP 2 sessionless clients, and lets concurrent requests select different Editors without sharing transient state.

## Legacy session defaults

On a **stateful legacy connection**, `set_active_instance(instance="MyGame@a1b2c3d4")` retains a session default. A per-request selector overrides it without changing that default. Modern sessionless connections return an explanatory error because they cannot persist this selection; repeated retries will not make it persistent.

Clients unable to attach resource metadata can use a stateful legacy protocol and session selection. FastMCP Python clients expose this through `Client(..., mode="legacy")`; do not assume every MCP client supports that choice.

## CLI selection

The local HTTP CLI is stateless. Pass the global option before the subcommand:

```bash
unity-mcp --instance "MyGame@a1b2c3d4" --format json scene hierarchy
```

Or set `UNITY_MCP_INSTANCE` in the CLI environment. `unity-mcp instance set` cannot persist a server selection and deliberately fails with guidance. See [CLI](./cli.md).

## Reconnects and concurrent writers

Explicit targets are preserved through reconnect attempts, but domain reload can temporarily remove an Editor from discovery. Rediscover before changing targets; an ambiguous or unavailable selector must not silently choose another project.

Routing does not provide transaction isolation or exactly-once writes. Coordinate agents that edit the same scene/assets. A timeout after dispatch can leave a completed or in-progress mutation even when no result arrived. Inspect state before retrying; use returned job IDs to poll long-running work rather than launching duplicates.

See [Instance Routing](../architecture/instance-routing.md) for implementation details and [Security And Consent](./security.md) for hosted isolation.

## Shared local server lifetime

The local HTTP server can serve several Editors. Keeping it running when an Editor
closes is enabled by default and configured per project. Closing a bridge connection
does not by itself grant permission to terminate the shared server.

The project that launched the server retains its own launch identity. An explicit
stop request must match that identity and is refused while another Editor connection
is admitted or registered. The server decides admission and shutdown together, so a
new connection cannot slip between a peer-count check and process termination.

Older or externally managed servers may not support this shutdown handshake. In that
case the window reports the reason and leaves the process running. A port match alone
never authorizes terminating an unrelated process. Use the server's owning terminal
or service manager when deliberate manual shutdown is needed.
