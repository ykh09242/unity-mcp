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
uvx --python ">=3.11" --from "https://github.com/ykh09242/unity-mcp/archive/57b04f30eadf46e344e5df8970e8653cb99c7e2d.zip#subdirectory=Server" unity-mcp --help
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
| `--timeout` | `UNITY_MCP_TIMEOUT` | Positive seconds, default 300. |
| `--format` | `UNITY_MCP_FORMAT` | `text`, `json`, or `table`. |
| `--instance` | `UNITY_MCP_INSTANCE` | Target Editor ID. |
| `--verbose` | - | Write command requests and raw responses to stderr; use only with data safe to log. |

Explicit options override corresponding environment settings. The CLI connects directly to loopback addresses and bypasses environment proxies for commands, health checks and inventory. It loads the current local token for authenticated requests. Non-loopback connections need explicit credentials and trusted transport. See [authentication](./security.md#local-http-authentication).

## Discover exact commands

```bash
unity-mcp --help
unity-mcp scene --help
unity-mcp editor --help
unity-mcp editor tests --help
```

Use help and the [CLI reference](../reference/cli.md) for flags, rather than assuming MCP tool parameters have identical CLI names. MCP tools, CLI commands, and Unity handlers are distinct layers. Additional task examples live in [CLI Examples](./cli-examples.md).

## Read and edit the same script version

Use `unity-mcp --format json script read Assets/Scripts/Player.cs --start-line 10 --line-count 5` to read a line range. The matching Editor returns the SHA-256 of the full source in `data.sha256`. Pass that value to `script edit` with `--expected-sha`. If another writer changes the script, the edit returns `stale_file` and the caller can read the new version before revising the edits.

## Automate without losing errors

Use `--format json` for one machine-readable stdout document; diagnostics go to stderr. Explicit Unity command failures return nonzero exit status while retaining native diagnostic data. Check both exit status and the returned payload, including failed child commands/jobs.

Save returned job IDs and poll them; do not rerun a mutation when a timeout may have occurred after dispatch. Budget errors call for smaller requests, not disabled safeguards. High-impact commands still need Editor consent. Never log authentication headers or private input files into shared job artifacts.

## Explicit readiness checks

`unity-mcp status` keeps its health-only exit contract: a healthy server can exit
successfully even when instance discovery fails or no Editor is connected. For
automation, use `unity-mcp --format json status --ready`. This exits nonzero if
health, authentication, discovery, or unique target selection fails. The JSON and
table output separate `server_reachable`, `authentication` (`accepted`, `rejected`,
or `unknown`), `instance_discovery`, and `target_ready`.

With multiple Editors, supply `--instance` before `status`. Readiness uses the local
command route's selection rules: the complete inventory `hash` matches first and
must identify exactly one Editor. `Name@Hash` uses the complete hash after the last
`@`; the name portion does not constrain an exact hash match. If there is no exact
hash match, a selector without `@` can match one exact project name. Duplicate
exact hash matches or duplicate fallback name matches are ambiguous. Hash prefixes
and transport `session_id` values are not selectors, and whitespace is preserved.
Copy the complete `hash`
from `instance list`; project names containing `@` should use that hash. Without a
selector (or with an empty CLI selector), exactly one discovered Editor is
required. This read-only check verifies discovery
availability; it does not dispatch a command or guarantee that compilation or a
subsequent Editor operation will succeed. HTTP errors other than 401/403 leave
authentication unknown. Rejected response bodies and credentials are not printed.
