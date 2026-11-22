---
id: cli
slug: /reference/cli
title: CLI Reference
sidebar_label: CLI
description: Distinguish the server executable from the local HTTP CLI, inspect options, and discover registered command groups.
---

# CLI Reference

This page is authored, not the generated MCP tool schema. Follow [CLI setup](../guides/cli.md) before using the shorter commands below. See the [generated command reference](./cli-commands.md) for registered command paths, options and defaults. Authored CLI examples are checked against the same Click command tree without executing callbacks.

## Two different executables

| Executable | Purpose |
|---|---|
| `mcp-for-unity` | Run the Python MCP server; accepts server transport/bind/auth flags. |
| `unity-mcp` | Send Editor commands and source-quality workflows to a running server, or inspect bundled tool-search models. |

They are **not aliases for one another**. Both are installed from the matching immutable `mcpServerSource`; neither should implicitly install an upstream PyPI distribution. Most `unity-mcp` commands use the local REST interface, which stdio and hosted HTTP do not provide. The source-quality commands described below use public MCP composition.

## Global CLI options

| Option | Default | Meaning |
|---|---|---|
| `--host` | `127.0.0.1` | Local HTTP server host. |
| `--port` | `8080` | HTTP port, 1-65535. |
| `--timeout` | `300` | Positive timeout in seconds, including the whole `docs get` lookup. |
| `--instance` | auto-select one local Editor | Explicit discovered Editor ID; no persistent session selection. |
| `--format` | `text` | `text`, `json`, or `table`. |
| `--verbose` | off | Print command requests and raw responses to stderr; these may contain private project data. |
| `--version` | - | Installed fork distribution version. |
| `--help` | - | Options and command help. |

Place global options before subcommands. HTTP tokens are discovered automatically only for loopback; see [Authentication](../guides/security.md#local-http-authentication).

## Command groups

The top-level commands are `animation`, `asset`, `asset-gen`, `audio`, `batch`, `blender`, `build`, `camera`, `code`, `component`, `custom_tool`, `docs`, `doctor`, `editor`, `gameobject`, `graphics`, `instance`, `instances`, `lighting`, `material`, `packages`, `physics`, `play-scenario`, `prefab`, `probuilder`, `profiler`, `project-auditor`, `raw`, `receipt`, `reflect`, `scenario-recording`, `scene`, `scene-edit`, `script`, `search-model`, `shader`, `sprite`, `status`, `texture`, `tool`, `ui`, `verification`, `verification-candidate`, `verification-evidence`, and `vfx`.

`unity-mcp search-model status` checks both bundled model files locally. Add `--verify` to require both checks to pass for a successful exit. [Tool search guide](../guides/cli.md#find-a-tool-in-the-users-language)

CLI verbs/flags are not automatically generated from MCP tool parameters. Use exact help:

```bash
unity-mcp --help
unity-mcp scene --help
unity-mcp scene load --help
unity-mcp verification-candidate --help
unity-mcp verification-evidence --help
```

`raw` sends native Editor commands with a JSON object. Use dedicated commands such as `verification report` for Python-composed MCP tools. Tool discovery, request targeting, consent and budgets still apply. `instance set` deliberately cannot persist a target; `batch` is not a universal atomic transaction or rollback mechanism.

The `receipt` group explicitly protects one native command with a validated store and request key. Its `describe` and `get` commands inspect retained execution records without a connected Editor. Protection is opt-in; see [execution receipts](../guides/cli.md#recover-a-command-after-response-loss) for setup and recovery after an uncertain response.

The `sprite` group uses [`manage_sprite`](./tools/animation/manage_sprite.md) for sheet inspection, slicing, clip creation and controller setup. The beta checkout includes this group; use matching Editor/server revisions and inspect `unity-mcp sprite --help` before changing assets. Published older server pins may not expose it.

Use `unity-mcp play-scenario --help` for save/get/list/reports/delete/run/status/cancel and the corresponding suite-save/get/list/reports/delete/run/status/cancel commands. Single runs return a Unity-owned job immediately; suite-run explicitly waits on the selected `--instance` and writes suite.json plus junit.xml to its required `--output-dir`. See the [scenario tool reference](./tools/testing/manage_play_scenario.md) for its bounded definition and execution contract.

The `scenario-recording` commands save observed interactions as a draft that requires review before execution. The `scene-edit` commands preview supported property changes and apply them with drift checks and one native Undo group. Inspect each group's `--help` before use. See the [scenario recording](./tools/testing/manage_scenario_recording.md) and [scene edits](./tools/core/manage_scene_edits.md) tool references.

The `project-auditor availability` command checks the installed Project Auditor package or Editor module and its rules. `start` begins analysis of the current build target and may compile Player code. Retain the audit identity and use `status` after an uncertain response. `issues` reads completed results with `--page-size` from 1 to 100 (default 50) and the returned `--cursor`. See `unity-mcp project-auditor --help` for command syntax and the [Project Auditor tool reference](./tools/profiling/manage_project_auditor.md) for result details.

## Output and failures

`script quality` and `verification` use the public MCP server to retain analysis,
baseline and report identities. Select an exact `--instance` and explicitly use
`--no-project-context`: the HTTP project-root guard is unavailable on this surface
and a configured guard is refused. Inspect `unity-mcp script quality --help` and
`unity-mcp verification --help` for actions and bounded JSON options. See the
[script diagnostics](./tools/testing/manage_script_quality.md) and
[verification](./tools/testing/manage_verification.md) tool references.

JSON mode emits a machine-readable stdout document; diagnostics belong on stderr. Explicit Unity failures return nonzero status and preserve native diagnostic payloads. Check child results/jobs as well as the outer response. A timeout after dispatch does not prove cancellation: inspect state before retrying writes.

See [worked CLI examples](../guides/cli-examples.md), [generated MCP tool reference](./tools/index.md), and [Python layers](../architecture/python-layers.md). Definitions live in [CLI commands](https://github.com/ykh09242/unity-mcp/tree/beta/Server/src/cli/commands) and [CLI main](https://github.com/ykh09242/unity-mcp/blob/beta/Server/src/cli/main.py).
