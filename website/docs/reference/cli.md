---
id: cli
slug: /reference/cli
title: CLI Reference
sidebar_label: CLI
description: Distinguish the server executable from the local HTTP CLI, inspect options, and discover registered command groups.
---

# CLI Reference

This page is authored, not the generated MCP tool schema. Follow [CLI setup](../guides/cli.md) before using the shorter commands below.

## Two different executables

| Executable | Purpose |
|---|---|
| `mcp-for-unity` | Run the Python MCP server; accepts server transport/bind/auth flags. |
| `unity-mcp` | Send Editor-control commands to an already-running local HTTP server. |

They are **not aliases for one another**. Both are installed from the matching immutable `mcpServerSource`; neither should implicitly install an upstream PyPI distribution. Stdio and hosted HTTP do not provide the local REST interface used by `unity-mcp`.

## Global CLI options

| Option | Default | Meaning |
|---|---|---|
| `--host` | `127.0.0.1` | Local HTTP server host. |
| `--port` | `8080` | HTTP port, 1-65535. |
| `--timeout` | `30` | Positive timeout in seconds. |
| `--instance` | auto-select one local Editor | Explicit discovered Editor ID; no persistent session selection. |
| `--format` | `text` | `text`, `json`, or `table`. |
| `--verbose` | off | Print command requests and raw responses to stderr; these may contain private project data. |
| `--version` | - | Installed fork distribution version. |
| `--help` | - | Options and command help. |

Place global options before subcommands. HTTP tokens are discovered automatically only for loopback; see [Authentication](../guides/security.md#local-http-authentication).

## Command groups

Registered groups include `instance`, `scene`, `gameobject`, `component`, `asset`, `asset_gen`, `blender`, `script`, `code`, `editor`, `play_scenario`, `prefab`, `material`, `lighting`, `animation`, `sprite`, `audio`, `ui`, `shader`, `vfx`, `batch`, `texture`, `probuilder`, `build`, `camera`, `graphics`, `packages`, `reflect`, `docs`, `physics`, `profiler`, `tool`, and `custom_tool`. Optional module-load failures can change available groups; installed help is authoritative.

CLI verbs/flags are not automatically generated from MCP tool parameters. Use exact help:

```bash
unity-mcp --help
unity-mcp scene --help
unity-mcp scene load --help
```

Root commands also include `status`, `instances`, and `raw`. Raw parameters must be a JSON object. Tool discovery, request targeting, consent and budgets still apply. `instance set` deliberately cannot persist a target; `batch` is not a universal atomic transaction or rollback mechanism.

The `sprite` group uses [`manage_sprite`](./tools/animation/manage_sprite.md) for sheet inspection, slicing, clip creation and controller setup. The beta checkout includes this group; use matching Editor/server revisions and inspect `unity-mcp sprite --help` before changing assets. Published older server pins may not expose it.

The `play_scenario` registration is exposed as `play-scenario` in CLI commands. Use `unity-mcp play-scenario --help` for save/get/list/reports/delete/run/status/cancel and the corresponding suite-save/get/list/reports/delete/run/status/cancel commands. Single runs return a Unity-owned job immediately; suite-run explicitly waits on the selected `--instance` and writes suite.json plus junit.xml to its required `--output-dir`. See the [scenario tool reference](./tools/testing/manage_play_scenario.md) for its bounded definition and execution contract.

## Output and failures

JSON mode emits a machine-readable stdout document; diagnostics belong on stderr. Explicit Unity failures return nonzero status and preserve native diagnostic payloads. Check child results/jobs as well as the outer response. A timeout after dispatch does not prove cancellation: inspect state before retrying writes.

See [worked CLI examples](../guides/cli-examples.md), [generated MCP tool reference](./tools/index.md), and [Python layers](../architecture/python-layers.md). Definitions live in [CLI commands](https://github.com/ykh09242/unity-mcp/tree/beta/Server/src/cli/commands) and [CLI main](https://github.com/ykh09242/unity-mcp/blob/beta/Server/src/cli/main.py).
