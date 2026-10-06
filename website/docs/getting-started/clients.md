---
id: clients
slug: /getting-started/clients
title: Connect Your MCP Client
sidebar_label: MCP Clients
description: Use the Editor configurator or review local HTTP and pinned stdio configuration examples.
---

# Connect Your MCP Client

Use the client that fits your workflow. This package configures MCP connections; it does not supply model access, subscriptions, provider accounts, or a hosted server. Vendor pricing and client capabilities change independently of the fork.

## Use the Editor configurator

Open **Window > Unity MCP (ykh09242)** and select the client in its configuration section. For local HTTP, start the server first so configuration includes its current token. Configure the selected client or detected clients together, inspect failures, then reconnect.

The [Client Configurators](../guides/client-configurators.md) guide documents the package's supported config formats and special cases:

- The package configures Claude Desktop through stdio regardless of the globally selected transport. This describes the configurator, not every transport the vendor may support.
- VS Code uses `servers` rather than `mcpServers`.
- Codex uses TOML and `http_headers` for HTTP authentication.
- OpenClaw requires its MCP bridge plugin; Pi requires an MCP extension. A config write cannot install or enable those integrations.
- Some clients need an MCP toggle or restart. Successful configuration is not proof of a live connection.

## Manual client configuration

These are connection shapes, not universal file paths. Use your client's format, preserve unrelated settings, and never commit token-bearing configuration.

### Local HTTP

Start the server. Replace the token placeholder privately with the contents of `~/.unity-mcp/auth/token-8080` for that launch and port:

```json
{
  "mcpServers": {
    "unityMCP": {
      "url": "http://localhost:8080/mcp",
      "headers": { "X-Unity-MCP-Token": "<current launch token>" }
    }
  }
}
```

For VS Code:

```json
{
  "servers": {
    "unityMCP": {
      "type": "http",
      "url": "http://localhost:8080/mcp",
      "headers": { "X-Unity-MCP-Token": "<current launch token>" }
    }
  }
}
```

Reconfigure and reconnect HTTP MCP clients after each restart; an old token produces 401 errors. See [Local Authentication](../guides/security.md#local-http-authentication).

### Stdio

This beta checkout uses the following immutable server source. Published stable releases keep their own source pins; use the installed package's `mcpServerSource`:

```json
{
  "mcpServers": {
    "unityMCP": {
      "command": "uvx",
      "args": [
        "--python",
        ">=3.11",
        "--from",
        "https://github.com/ykh09242/unity-mcp/archive/1229a17f2d8ee5848b145fdf1cde3e2f35c26fdb.zip#subdirectory=Server",
        "mcp-for-unity",
        "--transport",
        "stdio"
      ]
    }
  }
}
```

If `uvx` is not on the client's PATH, use its verified absolute path. For another package revision, use **that installed package's** `mcpServerSource`; do not retain this example's pin accidentally. Stdio does not use the local HTTP token header.

After updating the Editor package, regenerate the stdio client configuration and restart the Editor and client connection. The internal loopback bridge authenticates with automatically supplied per-launch credentials, so an older pinned server cannot connect to the new bridge.

## Confirm the connection and target

List the client's tools/resources, then read `mcpforunity://instances`. Include a returned Editor ID on calls as described in [Multi-Instance Routing](../guides/multi-instance.md). A config file, tool list, or health response alone does not establish an Editor connection.

For a shared remote service, use [Remote Server Auth](../guides/remote-server-auth.md), not the local-token example. Asset-generation provider credentials are separate from MCP authentication.
