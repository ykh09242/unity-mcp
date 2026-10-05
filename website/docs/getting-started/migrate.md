---
id: migrate
slug: /getting-started/migrate
title: Migrate To The Fork
sidebar_label: Migration
description: Replace the upstream package and understand independent versions, pinned sources, and update routing.
---

# Migrate To The Fork

Stable **1.0.0** starts this fork's independent version series from upstream `10.3.x` code; it is not an upstream downgrade. [Fork release notes](https://github.com/ykh09242/unity-mcp/releases/tag/ykh09242-v1.0.0) distinguish additions from fixes and list verification limits.

## Replace the package

1. Save the project and review uncommitted changes before changing dependencies.
2. Remove `com.coplaydev.unity-mcp` in Package Manager. The fork retains original assembly names and asset GUIDs; the packages must not coexist.
3. Install `com.ykh09242.unity-mcp` with the [stable Git URL](./install.md#1-install-the-unity-package). Update old package entries in `testables` too, if present.
4. Open **Window > Unity MCP (ykh09242)**. Clear unintended development server overrides so the installed package selects its pinned server.
5. Start HTTP before configuring clients; regenerate configurations and reconnect. Remove obsolete entries that would launch the upstream distribution.
6. Discover connected Editors and perform a read-only verification before editing.

The Python distribution is `ykh09242-unity-mcp-server`; executables remain `mcp-for-unity` and `unity-mcp`. Python modules, existing tool names, resource identifiers, C# namespaces and original MIT notices are retained.

## Versions and updates

| Source | What it selects |
|---|---|
| `#ykh09242-v1.1.0` | The current stable fork release tag. |
| Full 40-character SHA | A commit-addressed Unity package revision. |
| `#beta` | The moving development branch, not an immutable release. |
| Installed `mcpServerSource` | Matching Python source at a full SHA and `Server` subdirectory. |

Unity and Python versions can evolve independently. Stable `1.1.0` pins Python `1.1.0` to `7013c0ca7a1a3998190eec6803721e0051e8dcdc`; it need not match the Unity package's later release commit. Default launch validates the same-fork Git pin and does not silently fall back to PyPI or a floating branch.

Update checks are **notifications**, not changes to the installed pin. Tagged/SHA/default fork installs check `beta` metadata. Explicit `#main` checks inherited `main`; explicit `#beta` checks `beta`. Other revision spellings do not imply a preserved separate update channel. Local-copy/Asset Store update routing is unsupported; manage local copies manually.

## Adapt client behavior

- Local HTTP/WebSocket needs a fresh token each launch. Reconfigure HTTP MCP clients after restarting.
- Modern sessionless calls need explicit targets when local selection is ambiguous, and always in hosted mode. Tools use `unity_instance`; resources use `_meta.unity_instance`. Persistent selection/group changes require stateful legacy connections.
- High-impact tools need explicit Editor consent; general enablement does not grant it. See [Security And Consent](../guides/security.md).
- Validation and budgets reject more invalid/oversized requests, but errors do not guarantee an unchanged project: best-effort operations can apply valid fields before reporting invalid ones. Inspect state before retrying writes. CLI Unity failures now return nonzero status; review shell-script assumptions.
- A timeout after dispatch does not prove a write was canceled. Inspect state before retrying.

## Historical documentation

[Upstream release history](../releases.md) and [version migrations](../migrations/v10.md) describe CoplayDev releases and older distribution routes. They are history, not instructions to install this fork from Asset Store, OpenUPM or PyPI. Use [Install And Connect](./install.md) for current setup.
