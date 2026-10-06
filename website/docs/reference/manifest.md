---
id: manifest
slug: /reference/manifest
title: manifest.json Reference
sidebar_label: manifest.json
description: The repo-root manifest.json — what it describes, why it ships, and which fields are authoritative for the MCP marketplace bundle.
---

# `manifest.json` Reference

The repo-root `manifest.json` is the fork's MCP marketplace/bundle template. It carries the fork identity and the same commit-addressed server archive as `MCPForUnity/package.json`; its presence does not mean an MCPB bundle has been published. Python distribution metadata lives in `Server/pyproject.toml`. For the supported installation path, use the [fork installation guide](/getting-started/install).

If you're adding a new MCP tool, update [the tool registry](/architecture/python-layers) and let CI's drift check fail any stale entry — the generator keeps the docs in sync. The `tools` block in `manifest.json` is a separate, hand-maintained surface (see Notes below).

## Top-level fields

| Field | Type | Description |
|---|---|---|
| `manifest_version` | string | Schema version for this manifest (currently `"0.3"`) |
| `name` | string | Display name shown by aggregators |
| `version` | string | Declared package version; not proof of a published fork release |
| `description` | string | One-line product description |
| `author.name` | string | Maintainer's display name |
| `author.url` | string | Maintainer's website |
| `repository.type` | string | `"git"` |
| `repository.url` | string | Canonical repo URL |
| `homepage` | string | Project homepage |
| `documentation` | string | Docs landing URL |
| `support` | string | Where to file issues |
| `icon` | string | Path to a square icon, relative to the manifest |

## `server`

The manifest's server invocation uses the full-commit fork source archive. The example abbreviates that URL as `<mcpServerSource>`; replace it with the exact value in the installed Unity package's `package.json`.

```json
"server": {
  "type": "python",
  "entry_point": "Server/src/main.py",
  "mcp_config": {
    "command": "uvx",
    "args": ["--from", "<mcpServerSource>", "mcp-for-unity"],
    "env": {}
  }
}
```

- **`type`** — runtime family. Currently always `"python"`.
- **`entry_point`** — file an aggregator would point a Python interpreter at if it weren't using `uvx`.
- **`mcp_config.command`** — launch command. `uvx` keeps the dependency tree managed without a global install.
- **`mcp_config.args`** — invocation arguments. Use `--transport http` or `--transport stdio` explicitly to select the transport.
- **`mcp_config.env`** — environment variables to set before launching (telemetry opt-outs, log levels, etc.).

## `tools`

A flat array of `{ name, description }` entries listing every MCP tool the server exposes. Aggregators use it for search and category surfaces without having to introspect the live registry.

This list is hand-maintained for now. The authoritative count and metadata live in the Python tool registry — see the [Tool reference](/reference/tools) for the generated catalog with full parameter docs.

## Notes

- `manifest.json` is NOT the Unity UPM manifest. That's `MCPForUnity/package.json` (fork name: `com.ykh09242.unity-mcp`, display name: `Unity MCP (ykh09242)`).
- The fork Python distribution metadata lives in `Server/pyproject.toml` (name: `ykh09242-unity-mcp-server`); this does not imply publication on PyPI.
- These are independent surfaces. Keep fork identity and immutable source pins aligned across them; CLI and protocol identifiers remain unchanged.
- The optional [`tools/generate_mcpb.py`](https://github.com/ykh09242/unity-mcp/blob/beta/tools/generate_mcpb.py) builder requires an explicit `--icon` path. No default upstream icon or published fork bundle is implied.

## Where it ships

The current `manifest.json` is at the repo root: [`manifest.json`](https://github.com/ykh09242/unity-mcp/blob/beta/manifest.json).
