# Unity MCP (ykh09242) - Editor Plugin

`com.ykh09242.unity-mcp` connects the Unity Editor to MCP clients through a commit-pinned Python server. Stable fork versions begin at `1.0.0`, independently of upstream versions.

## Install and connect

Use [Install And Connect](https://ykh09242.github.io/unity-mcp/getting-started/install). Remove `com.coplaydev.unity-mcp` first: the packages share assembly names and asset GUIDs and must not coexist.

1. Let the package import and resolve compilation errors.
2. Open **Window > Unity MCP (ykh09242)** and complete dependency checks.
3. For local HTTP, start the server before configuring clients, then connect the Unity bridge. Stdio clients launch their own server process.
4. Configure the intended client, reconnect it, and read `mcpforunity://instances` to verify the Editor identity.
5. Try [a small first change](https://ykh09242.github.io/unity-mcp/getting-started/first-prompt) in a saved or disposable scene.

Local HTTP uses a fresh token each launch. Reconfigure HTTP MCP clients after restarting the server. Keep token files and generated client configs private.

## Choose the right guide

| Task | Guide |
|---|---|
| Replace an upstream installation | [Migration](https://ykh09242.github.io/unity-mcp/getting-started/migrate) |
| Configure an MCP client manually | [MCP Clients](https://ykh09242.github.io/unity-mcp/getting-started/clients) |
| Select one of several Editors | [Multi-Instance Routing](https://ykh09242.github.io/unity-mcp/guides/multi-instance) |
| Enable a needed tool/group | [Tool Groups](https://ykh09242.github.io/unity-mcp/guides/tool-groups) |
| Review high-impact permissions | [Security And Consent](https://ykh09242.github.io/unity-mcp/guides/security) |
| Diagnose connection problems | [Troubleshooting](https://ykh09242.github.io/unity-mcp/guides/troubleshooting) |
| Validate scripts with optional Roslyn | [Roslyn](https://ykh09242.github.io/unity-mcp/guides/roslyn) |

## Server source and development

The package's `mcpServerSource` records immutable Git source for the matching `Server` implementation; the UPM revision and server SHA need not be identical. Keep the default pin for released use. **Server Source Override** and **Dev Mode** are explicit development settings, not requirements for normal setup. See the [Server README](https://github.com/ykh09242/unity-mcp/blob/beta/Server/README.md) and [development guide](https://ykh09242.github.io/unity-mcp/contributing/dev-setup).

Existing executable names, MCP tool/resource identifiers, C# namespaces and assembly identities are retained. Original authorship and MIT notices are preserved; see [license and upstream attribution](Documentation~/LICENSE.md). Maintained by [ykh09242](https://github.com/ykh09242), based on [CoplayDev/unity-mcp](https://github.com/CoplayDev/unity-mcp); not affiliated with Unity Technologies.
