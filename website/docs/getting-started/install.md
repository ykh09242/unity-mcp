---
id: install
slug: /getting-started/install
title: Install And Connect
sidebar_label: Install
description: Install the stable fork, authenticate a client, and verify the Unity connection.
---

# Install And Connect

Install stable **Unity MCP (ykh09242) 1.0.0**, then connect an MCP client to your Editor. This is a Git-distributed fork of CoplayDev/unity-mcp, not an upstream release or a hosted AI service.

## Before you start

- Unity **2021.3 or newer**. The declared minimum is not runtime certification for every Editor; see the [release verification and limits](https://github.com/ykh09242/unity-mcp/releases/tag/ykh09242-v1.0.0).
- Git available to Unity Package Manager.
- Python **3.10+** and [`uv`/`uvx`](../guides/uv-setup.md) available to Unity.
- An MCP client. Use a client you already have; [client configuration](./clients.md) describes the package's configurators without requiring a specific provider.

**Coming from upstream?** First follow [Migrate To The Fork](./migrate.md). Never co-install `com.coplaydev.unity-mcp` and `com.ykh09242.unity-mcp`: assembly names and asset GUIDs are shared.

## 1. Install the Unity package

In **Window > Package Manager**, choose **+ > Add package from git URL**:

```text
https://github.com/ykh09242/unity-mcp.git?path=/MCPForUnity#ykh09242-v1.0.0
```

This tag identifies stable `1.0.0`. For a commit-addressed install, use the released full SHA:

```text
https://github.com/ykh09242/unity-mcp.git?path=/MCPForUnity#7daf87b908643d8e2a325d077abd16a3c3559b61
```

`#beta` follows a moving development branch; its name does not make the stable release a beta. The installed package's `mcpServerSource` selects its matching Python source independently. Do not substitute an inherited upstream tag or a PyPI package. See [version and update policy](./migrate.md#versions-and-updates).

## 2. Start the local connection

1. Let the package import and fix any Unity compilation errors first.
2. Open **Window > Unity MCP (ykh09242)**. Complete the dependency checks if the setup window appears.
3. Select local **HTTP** for a shared server, or **stdio** when your client needs a separate server process. Keep local HTTP on loopback unless deliberately configuring a trusted tunnel.
4. For HTTP, start the local server and wait for it to be reachable **before configuring clients**. Connect the Unity bridge to that server. Stdio clients launch their own Python process from the generated configuration.

The Unity bridge and the MCP client are separate connections. A running Python process alone does not mean an Editor is connected.

## 3. Configure your client

Use the Editor window's client configuration section for the selected client, or configure the detected clients together. Review the result, then reconnect or restart the client if it has not reloaded its configuration.

Local HTTP requires a fresh `X-Unity-MCP-Token` each server launch. Generated configurations include the current token. **After restarting HTTP, reconfigure and reconnect HTTP MCP clients.** Native CLI and Unity connections look up the current token automatically. Never commit token files or token-bearing client configurations.

For unsupported clients, use [Manual Client Configuration](./clients.md#manual-client-configuration). For a manually launched Python server, see [Server README](https://github.com/ykh09242/unity-mcp/blob/beta/Server/README.md). Hosted HTTPS/API-key deployment is a different mode: [Remote Server Auth](../guides/remote-server-auth.md).

## 4. Verify before editing

Ask your assistant:

> Read `mcpforunity://instances`, show the available Editor IDs, and read the project info and active scene for the Editor I choose. Do not modify anything yet.

Use the returned `Name@hash` as `unity_instance` on each tool call and `_meta.unity_instance` on each resource read. Stateful legacy sessions can retain a default; modern sessionless clients cannot. See [Multi-Instance Routing](../guides/multi-instance.md).

Then try [Your First Prompt](./first-prompt.md) in a disposable or saved scene. Enable only the needed tools and review [consent and safe operation](../guides/security.md).

## If a step fails

| Symptom | Next check |
|---|---|
| Package Manager rejects the Git URL | Git/PATH and the exact URL; [Git troubleshooting](../guides/troubleshooting.md#package-manager-error-when-executing-git-command--not-in-a-git-directory). |
| Python server does not start | Dependency paths and `Library/MCPForUnity/Logs/server-launch-<port>.log`. |
| HTTP returns 401 | Current launch token; reconfigure after restart. |
| HTTP returns 403 | Browser-origin headers are unsupported; use a native client. |
| Server responds but no Editor is listed | Unity compile errors, bridge status, matching transport/endpoint. |
| Multiple Editors are listed | Send the chosen `unity_instance` explicitly. |

Continue with [Troubleshooting](../guides/troubleshooting.md); report fork issues at [ykh09242/unity-mcp](https://github.com/ykh09242/unity-mcp/issues).
