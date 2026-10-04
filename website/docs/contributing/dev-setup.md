# Unity MCP (ykh09242) - Developer Guide

[简体中文](https://github.com/ykh09242/unity-mcp/blob/beta/docs/development/README-DEV-zh.md)

Branch from `beta` and open fork PRs against `beta`. It is a moving development branch, distinct from stable `1.0.0`. Do not change release tags, versions or the immutable server pin as a side effect of unrelated work; see [Migration](../getting-started/migrate.md#versions-and-updates) and [Releasing](./releases.md).

## Choose the smallest environment

| Change | Start here |
|---|---|
| Markdown | Relevant guides, manifests, links/examples; no Editor/server launch needed. |
| Website presentation | `website/README.md`, rendered desktop/mobile checks, docs build. |
| Python tool/CLI/transport | `Server/`, focused tests and generated-reference check if schemas change. |
| Unity handler/shim | `MCPForUnity/`, affected compile matrix plus deliberately scoped native tests. |
| Tooling | Relevant `tools/tests` module; avoid launching products unless the test requires it. |

## Prepare Python development

With the repository's existing toolchain, from `Server/`:

```bash
uv sync --locked --extra dev
uv run --locked --extra dev pytest tests/test_manage_material.py -v -W error
```

Use the relevant test module instead of this example. Preserve lockfiles and dependency versions unless dependency work is explicitly part of the change. Most tests use controlled fixtures, not live Unity. [Testing](./testing.md) explains the evidence boundaries and support-floor coverage.

## Connect a local Unity package

For the existing test project, run from the repository root **before opening it**:

```bash
python mcp_source.py --manifest TestProjects/UnityMCPTests/Packages/manifest.json --repo . --choice 4
```

This deliberately changes the local project manifest: option 4 selects this workspace's `MCPForUnity`, replaces the old package ID and migrates its existing `testables`. Review the diff and keep contributor-specific project changes local. CI prepares isolated package profiles; it does not require this checked-in manifest to be rewritten.

For another project:

```bash
python mcp_source.py --manifest /path/to/UnityProject/Packages/manifest.json --repo .
```

Options 1/2 intentionally select upstream main/beta; option 3 uses the current remote branch; option 4 uses local workspace source. Inspect `Packages/packages-lock.json` to confirm resolved Git identity. Do not patch `Library/PackageCache` as a development workflow.

## Point the Editor at your server

Open **Window > Unity MCP (ykh09242)** and set **Server Source Override** to this checkout's `Server/` directory in Advanced Settings. **Dev Mode** requests fresh installs. These are development overrides, not stable-user requirements; clear them when validating the released default pin.

For local HTTP, start the server before configuring clients, connect the bridge, then configure/reconnect the client. Restarts rotate the local token. Discover Editors and send explicit targets; do not use `set_active_instance` unless the connection is stateful legacy. See [Install](../getting-started/install.md), [Routing](../guides/multi-instance.md) and [Consent](../guides/security.md).

## Add or change a tool

MCP tools, CLI commands and Unity handlers are separate layers; changing one does not generate the others.

1. Add/change the Python wrapper under `Server/src/services/tools/` and the corresponding Unity handler under `MCPForUnity/Editor/Tools/`.
2. Update the CLI layer deliberately if the workflow is exposed there; preserve native values, explicit nulls and failure exit codes.
3. Add focused Python and Unity regression coverage at the affected contract boundary.
4. Route version-dependent Unity APIs through existing compatibility helpers rather than proliferating conditional branches.
5. Regenerate reference docs through `tools/generate_docs_reference.py`; authored example blocks are preserved. See [Docs Workflow](./docs.md).

Read-only resources use `Server/src/services/resources/` and Unity resource handlers. Bound large results and advertise pagination through actual schema/URI parameters, not prose-only promises.

## Tool visibility during development

Editor toggles, local server defaults, legacy session groups and hosted catalogs are distinct. After toggles change, refresh the client's inventory; local `manage_tools sync` follows the selected transport. Hosted Unity pushes its catalog and remote `sync` is unavailable. High-impact consent still applies. See [Tool Groups](../guides/tool-groups.md).

## Review and report

Keep PRs focused. Include the reproduction, exact Editor/package/server revisions, OS/client/transport, commands actually run and whether evidence is compile-only, fixture-based or native runtime. Do not imply a skipped licensed job passed. Check current workflow YAML and `tools/unity-versions.json` rather than copying a stale version/count from a guide.

[Testing](./testing.md) covers checks and [CONTRIBUTING.md](https://github.com/ykh09242/unity-mcp/blob/beta/CONTRIBUTING.md) covers contribution policy. Preserve MIT notices, upstream attribution, assembly names and GUIDs; do not include credentials or private project data in reports.
