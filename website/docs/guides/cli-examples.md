---
title: CLI Examples
sidebar_label: CLI Examples
description: Small local HTTP inspection workflows with explicit targets and valid JSON payloads.
---

# CLI Examples

Complete [CLI setup](./cli.md), start local HTTP and connect Unity. Run the matching installed `unity-mcp` executable, or prepend the pinned `uvx --from` prefix from that guide. Replace `MyGame@a1b2c3d4` with a discovered ID. These are terminal commands, not MCP protocol calls.

## Inspect before editing

```bash
unity-mcp --format json instance list
unity-mcp --instance "MyGame@a1b2c3d4" --format json scene active
unity-mcp --instance "MyGame@a1b2c3d4" --format json scene hierarchy
unity-mcp --instance "MyGame@a1b2c3d4" --format json gameobject find "Player"
```

An empty list indicates no connected Editor; successful health is not enough. `instance current` reflects the current command's configured target, not server-side persisted selection. `instance set` is intentionally unsupported for persistence.

## Send a read-only raw command

For a POSIX shell, quote the complete JSON object:

```bash
unity-mcp --instance "MyGame@a1b2c3d4" --format json raw manage_scene '{"action":"get_hierarchy"}'
```

Shell quoting differs across environments; preserve the actual JSON bytes. Raw parameters must be objects, not arrays/scalars. The native command still enforces Editor permissions, target ownership and budgets; raw access is not a validation bypass.

## Discover the next operation

```bash
unity-mcp gameobject create --help
unity-mcp editor tests --help
unity-mcp editor poll-test --help
unity-mcp batch run --help
```

Use help for the exact positional arguments/options before writing or running tests. Some workflows require a tool group, Unity dependency, provider credentials or explicit consent. Enable only what the intended operation needs; see [Tool Groups](./tool-groups.md) and [Security And Consent](./security.md).

## Automate responsibly

- Use JSON output and check exit status/native success fields; stderr is separate diagnostic output.
- Save a returned job ID and poll it instead of starting a duplicate build/test/generation job.
- A batch can contain partial results; it is not a universal atomic transaction.
- A timeout after dispatch can leave work applied or running. Inspect state before retrying a mutation.
- Never include local tokens/provider keys or private input files in shared job logs.

For exact command inventory see [CLI Reference](../reference/cli.md). This guide intentionally avoids a second copied command catalog with stale placeholders or undocumented flags.
