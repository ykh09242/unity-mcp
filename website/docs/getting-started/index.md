---
id: index
slug: /getting-started
title: Overview
sidebar_label: Overview
description: AI-driven game development for the Unity Editor via the Model Context Protocol.
---

# Overview

Unity MCP (ykh09242) is a Git-only fork of CoplayDev/unity-mcp that bridges AI assistants — Claude, Codex, VS Code, local LLMs, and more — with the Unity Editor via the [Model Context Protocol](https://modelcontextprotocol.io/introduction). Give your LLM the tools to manage assets, control scenes, edit scripts, run tests, and automate workflows.

![MCP for Unity building a scene](https://raw.githubusercontent.com/ykh09242/unity-mcp/beta/docs/images/building_scene.gif)

## What you get

- **Editor tools** exposed over MCP — scene/object/asset authoring, scripting, physics, tests, and more. The [generated reference](../reference/tools/index.md) is the current inventory, not a fixed feature count.
- **Read-only resources** for state introspection — editor state, components, project capabilities, connected instances, and more.
- **Auto-configuration** for popular MCP clients — Claude Desktop, Claude Code, Cursor, VS Code, Windsurf, Cline, Codex, Qwen, Gemini CLI, Copilot CLI, OpenClaw.
- **Multi-instance support** — explicitly target each tool/resource request; persistent selection is available only on stateful legacy connections.
- **Two transports** — HTTP (multi-agent, default) and stdio (single-agent legacy).

## When you'd use it

- Prototype scenes and gameplay with natural language ("build a player controller with WASD and a double-jump").
- Generate and refactor C# scripts with full project context and validation.
- Automate repetitive editor tasks — bulk asset processing, scene validation, regression testing.
- Build custom AI-driven editor tools on top of the MCP protocol.

## Next steps

- **[Install](./install.md)** — Add the Unity package, install the Python server, and connect your first MCP client.
- **[Your First Prompt](./first-prompt.md)** — End-to-end "build me a red cube" tutorial.
- **[Choosing an MCP Client](./clients.md)** — A capability matrix across all supported clients.
- **[Migration](./migrate.md)** — Replace upstream without co-installing shared assemblies/GUIDs; understand stable release versus development branch.
- **[Security And Consent](../guides/security.md)** — Local authentication, high-impact tools, credentials, budgets, and retry limits.

Stable fork `1.1.2` follows its own release series. [Fork release notes](https://github.com/ykh09242/unity-mcp/releases/tag/ykh09242-v1.1.2) list the exact tested versions and limits. [Upstream history](../releases.md) is retained separately and is not the fork's latest release.

---

MIT licensed fork maintained by [ykh09242](https://github.com/ykh09242). Original copyright, artwork, authorship, and citation are retained from CoplayDev/unity-mcp. Not affiliated with Unity Technologies.
