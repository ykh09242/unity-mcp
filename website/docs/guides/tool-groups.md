---
id: tool-groups
slug: /guides/tool-groups
title: Tool Visibility And Groups
sidebar_label: Tool Groups
description: Inspect visible tools, synchronize local Editor toggles, and distinguish legacy group state from hosted catalogs and consent.
---

# Tool Visibility And Groups

Tools are grouped so an assistant can discover the relevant operations without loading the entire catalog. Visibility is not consent or authorization to mutate a project.

## Inspect before enabling

Ask for `manage_tools(action="list_groups")`. It reports actual visible inventory rather than assuming every registered tool is enabled. Consult the [tool catalog](../reference/tools/index.md) for group membership and schemas; it is generated from the source instead of copied into this guide.

Groups include `core`, `animation`, `ui`, `vfx`, `scripting_ext`, `testing`, `probuilder`, `profiling`, `docs`, and `asset_gen`. Local defaults begin with `core`; optional tools may also depend on Unity packages, provider setup, or explicit consent.

The `ui` group contains [`manage_ui`](../reference/tools/ui/manage_ui.md) for UI Toolkit and [`manage_ugui`](../reference/tools/ui/manage_ugui.md) for Canvas-based UI. The [uGUI guide](./ugui.md) covers creation, layout editing and screen-size diagnostics.

## Local sessionless clients

Use the Editor's tool controls to enable the needed tools. Local clients can request `manage_tools(action="sync")` to refresh defaults from the selected Unity transport; include the intended `unity_instance` when necessary. Refresh/reconnect the MCP client if it does not process tool-list notifications.

Sessionless `activate`, `deactivate`, and `reset` return an explanatory failure: group state cannot persist without a stateful handshake. Use Editor controls rather than repeatedly attempting activation.

## Stateful legacy sessions

Legacy connections can use session group actions:

```text
manage_tools(action="activate", group="vfx")
manage_tools(action="deactivate", group="vfx")
manage_tools(action="reset")
```

Activation affects the session, not installed Unity dependencies or consent. Reset returns to defaults. After changing Editor toggles, use local `sync` and inspect the returned inventory.

## Hosted clients

Hosted clients of either protocol see and can call built-in tools enabled in their authenticated Unity plugin's catalog, including optional groups. Without a catalog, only server-side helper tools are available. Unity pushes catalog changes; remote `sync` is unavailable, and group activation cannot bypass Unity-disabled tools or another user's catalog.

## Missing tools versus denied operations

| Situation | Action |
|---|---|
| Tool missing from the client's list | Check group inventory and Editor toggles; refresh the client. |
| Tool present but consent denied | Grant only the intended high-impact capability in the Editor. |
| Tool requires a Unity package | Install/configure that dependency deliberately; visibility does not install it. |
| Hosted catalog absent | Check the authenticated plugin connection and catalog registration. |

See [Security And Consent](./security.md) and [`manage_tools`](../reference/tools/core/manage_tools.md).
