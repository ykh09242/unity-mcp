---
id: cli
slug: /guides/cli
title: Use The Local HTTP CLI
sidebar_label: CLI
description: Launch the pinned Editor-control CLI, target requests explicitly, and consume machine-readable output safely.
---

# Use The Local HTTP CLI

`unity-mcp` controls an already-connected Editor through **local HTTP**. It is not the MCP server executable, a stdio client, or the hosted API-key control interface. Start the local HTTP server and Unity bridge first; see [Install And Connect](../getting-started/install.md).

## Run the matching CLI

This beta checkout uses this pinned source. Published stable releases keep their own pins; use the installed package's `mcpServerSource`:

```bash
uvx --python ">=3.11" --from "https://github.com/ykh09242/unity-mcp/archive/2a8dd0159c8b5f60c36e3cce8d1d268b141458a1.zip#subdirectory=Server" unity-mcp --help
```

For another installed Unity revision, use its `mcpServerSource`. If `unity-mcp` is already installed from the matching source, the shorter commands below work directly. Otherwise prepend the same `uvx --python ">=3.11" --from` prefix to each command.

## Inspect and select

```bash
unity-mcp --format json status
unity-mcp --format json instance list
unity-mcp --instance "MyGame@a1b2c3d4" --format json scene hierarchy
unity-mcp --instance "MyGame@a1b2c3d4" --format json gameobject find "Player"
```

Replace the example ID with a discovered one. Global options go **before** subcommands. The CLI is stateless: `instance set` cannot persist a target and returns an explanatory failure. Use `--instance` on every command or set `UNITY_MCP_INSTANCE` in the CLI environment.

| Global option | Environment variable | Meaning |
|---|---|---|
| `--host` | `UNITY_MCP_HOST` | HTTP host; default `127.0.0.1`. |
| `--port` | `UNITY_MCP_HTTP_PORT` | Port, 1-65535; default 8080. |
| `--timeout` | `UNITY_MCP_TIMEOUT` | Positive seconds; default 30. |
| `--format` | `UNITY_MCP_FORMAT` | `text`, `json`, or `table`. |
| `--instance` | `UNITY_MCP_INSTANCE` | Target Editor ID. |
| `--verbose` | - | Write command requests and raw responses to stderr; use only with data safe to log. |

Explicit options override corresponding environment settings. The CLI loads the current local token for loopback requests. Non-loopback connections need explicit credentials and trusted transport; see [authentication](./security.md#local-http-authentication).

## Discover exact commands

```bash
unity-mcp --help
unity-mcp scene --help
unity-mcp editor --help
unity-mcp editor tests --help
```

Use help and the [CLI reference](../reference/cli.md) for flags, rather than assuming MCP tool parameters have identical CLI names. MCP tools, CLI commands, and Unity handlers are distinct layers. Additional task examples live in [CLI Examples](./cli-examples.md).

## Automate without losing errors

Use `--format json` for one machine-readable stdout document; diagnostics go to stderr. Explicit Unity command failures return nonzero exit status while retaining native diagnostic data. Check both exit status and the returned payload, including failed child commands/jobs.

Save returned job IDs and poll them; do not rerun a mutation when a timeout may have occurred after dispatch. Budget errors call for smaller requests, not disabled safeguards. High-impact commands still need Editor consent. Never log authentication headers or private input files into shared job artifacts.
