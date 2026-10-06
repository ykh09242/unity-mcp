---
id: install
slug: /getting-started/install
title: Install And Connect
sidebar_label: Install
description: Install the stable fork, authenticate a client, and verify the Unity connection.
---

# Install And Connect

Install stable **Unity MCP (ykh09242) 1.1.3**, then connect an MCP client to your Editor. This is a Git-distributed fork of CoplayDev/unity-mcp, not an upstream release or a hosted AI service.

## Before you start

- Unity **2021.3 or newer**. The declared minimum is not runtime certification for every Editor; see the [release verification and limits](https://github.com/ykh09242/unity-mcp/releases/tag/ykh09242-v1.1.3).
- Git available to Unity Package Manager.
- Python **3.10+** and [`uv`/`uvx`](../guides/uv-setup.md) available to Unity.
- An MCP client. Use a client you already have; [client configuration](./clients.md) describes the package's configurators without requiring a specific provider.

**Coming from upstream?** First follow [Migrate To The Fork](./migrate.md). Never co-install `com.coplaydev.unity-mcp` and `com.ykh09242.unity-mcp`: assembly names and asset GUIDs are shared.

## 1. Install the Unity package

In **Window > Package Manager**, choose **+ > Add package from git URL**:

```text
https://github.com/ykh09242/unity-mcp.git?path=/MCPForUnity#ykh09242-v1.1.3
```

This tag identifies stable `1.1.3`. For a commit-addressed install, use the package preparation commit below. It contains the same Unity package as the release tag; subsequent release documentation commits do not change package contents:

```text
https://github.com/ykh09242/unity-mcp.git?path=/MCPForUnity#4fd56a7f30bf8e1f5acf371693dd6aeeee20a7c7
```

`#beta` follows a moving development branch; its name does not make the stable release a beta. The installed package's `mcpServerSource` selects its matching Python source independently. Do not substitute an inherited upstream tag or a PyPI package. See [version and update policy](./migrate.md#versions-and-updates).

## 2. Start the local connection

1. Let the package import and fix any Unity compilation errors first.
2. Open **Window > Unity MCP (ykh09242) > Toggle MCP Window**. For dependency checks, open **Local Setup Window** from the same menu.
3. Select local **HTTP** for a shared server, or **stdio** when your client needs a separate server process. Keep local HTTP on loopback unless deliberately configuring a trusted tunnel.
4. For HTTP, start the local server and wait for it to be reachable **before configuring clients**. Connect the Unity bridge to that server. Stdio clients launch their own Python process from the generated configuration.

The Unity bridge and the MCP client are separate connections. A running Python process alone does not mean an Editor is connected.

You can close the MCP window after connecting. Closing a window does not stop the
server or disconnect the bridge. Use the explicit server/session controls when
you intend to stop them. The HTTP auto-start setting in **Advanced** is a separate,
optional background-start preference; it does not require an open MCP window.
Unity can still restore tabs that were left open in its saved Editor layout.

:::note Upgrading from 1.1.0
The **1.1.1** release includes redesigned windows and manual setup: the package
does not open it automatically on Editor startup. **Start HTTP on Editor Startup**
remains optional and works without opening the MCP window. Editor Coroutines is
resolved as a package dependency for UPM waits; server connections remain independent
of window lifetime.
:::

Version **1.1.3** fixes Windows server installation failing with **Filename too
long** during Git checkout. It installs the same Python **1.1.1** commit through
a GitHub source archive, without changing global Git/OS settings. Clear any
unintended server source override and regenerate stale stdio client configurations
after updating. The archive is cached for repeat launches. Native Editor rendering
and UPM installation have not been runtime-tested; see the release notes for
bootstrap, compile and managed-test evidence.

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
