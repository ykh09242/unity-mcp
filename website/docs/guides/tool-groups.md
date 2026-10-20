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

Groups include `core`, `camera`, `graphics`, `physics`, `animation`, `ui`, `vfx`, `scripting_ext`, `testing`, `probuilder`, `profiling`, `docs`, and `asset_gen`. Local HTTP startup defaults begin with `core`; optional tools may also depend on Unity packages, provider setup, or explicit consent.

The specialized `camera`, `graphics`, and `physics` groups are optional. Their complete input schemas and tool IDs are unchanged; enable the relevant group when working on cameras, rendering, or physics.

## Editor selection and recommended defaults

On the Unity MCP window's **Tools** page, the **User Interface** group distinguishes **uGUI / Canvas** (`manage_ugui`) from **UI Toolkit / UXML & USS** (`manage_ui`). Each row keeps its technical tool ID under the display name. Expand **Details** for parameter and output information or tool-specific settings.

Fresh built-in selections enable essentials only. Existing saved tool choices remain unchanged, and custom tools retain their declared registration default. **Use Recommended** selects essential built-in tools and disables optional built-in groups; it preserves custom tools and all explicit-consent tool settings. This preset also applies to rows hidden by search or the **Show** filter.

Search names, IDs, descriptions, and groups, or filter enabled/disabled tools without changing their selection. Group counts explicitly report enabled tools; a mixed checkbox indicates a partially enabled group. The server confirmation below the controls distinguishes saved settings from settings actually received by the server.

## Tool inventory and model context

This server reduces the tools returned in the initial local HTTP catalog. It keeps standard `tools/list` responses, full schemas and stable tool names. A smaller visible catalog can reduce the definitions a host sends to its model, but the host decides how much of that catalog enters model context.

The [MCP client best practices](https://github.com/modelcontextprotocol/modelcontextprotocol/blob/main/docs/docs/2026-07-28/develop/clients/client-best-practices.mdx) recommend progressive tool discovery in the client: inspect the catalog, select relevant tools, and refresh it when tool-list notifications arrive. Server visibility settings complement this behavior; they do not implement host-side tool search or guarantee the same token savings in every client.

Stdio initially registers all groups for compatibility, then synchronizes with Unity. A smaller initial inventory is therefore specific to local HTTP. Existing saved Editor choices may enable more tools when the Editor connects.

The `ui` group contains [`manage_ui`](../reference/tools/ui/manage_ui.md) for UI Toolkit and [`manage_ugui`](../reference/tools/ui/manage_ugui.md) for Canvas-based UI. The [uGUI guide](./ugui.md) covers creation, layout editing and screen-size diagnostics.

`manage_ui` accepts `component_type="auto"`, `"ui_document"`, or `"panel_renderer"` for component operations. Auto preserves an existing UIDocument and prefers PanelRenderer for new attachments when the editor exposes its supported API. Explicit PanelRenderer selection reports a capability error on older Editors. Tree inspection, live element edits, detach and rendering use the same selection.

The `animation` group includes [`manage_animation`](../reference/tools/animation/manage_animation.md) and [`manage_sprite`](../reference/tools/animation/manage_sprite.md). The latter handles sprite-sheet inspection, slicing, clips and controller setup when both the Editor and server expose the new tool.

The `testing` group includes [`manage_input`](../reference/tools/testing/manage_input.md), alongside async tests. Its `ui_click` action dispatches uGUI events directly to a scene target; it does not verify raycast occlusion or physical pointer targeting. Key, mouse and touch simulation requires the optional Input System package and compatible Active Input Handling. Inspect `status` before using those capabilities; holds have bounded frame counts and a hard lease.

The `core` group includes [`manage_recording`](../reference/tools/core/manage_recording.md) for bounded silent MP4 capture. Query `capabilities`, start a job, then poll or stop that job explicitly. Recording depends on a supported interactive Windows/macOS Editor and native codec availability; Game View capture requires running unpaused Play Mode and the visible focused view. Linux and headless recording are unsupported.

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
