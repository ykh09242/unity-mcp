---
id: tool-groups
slug: /guides/tool-groups
title: Tool Visibility And Groups
sidebar_label: Tool Groups
description: Select focused feature tools, synchronize Editor choices, and preserve compatibility and project permissions.
---

# Tool Visibility And Groups

Tools are grouped so an assistant can discover the relevant operations without loading the entire catalog. Visibility is separate from consent and authorization to mutate a project.

## Start with the needed feature

Local HTTP and stdio start with essential `core` tools. Ask for `manage_tools(action="list_groups")` to discover all registered names and the `enabled_tools` actually visible in each group. Consult the generated [tool catalog](../reference/tools/index.md) for action and parameter schemas.

The larger combined tools are split by function. Each feature exposes only its supported actions and inputs while retaining the same validation, execution, response processing and project permissions.

| Work | Focused tools |
|---|---|
| Physics | `query_physics`, `apply_physics_force`, `manage_rigidbody`, `manage_physics_joints`, `manage_physics_materials`, `manage_physics_settings`, `diagnose_physics`, `simulate_physics` |
| Rendering | `manage_render_volumes`, `manage_light_baking`, `manage_rendering_stats`, `manage_render_pipeline`, `manage_renderer_features`, `manage_render_environment`, `manage_render_fog`, `manage_render_reflections` |
| Cameras | `manage_cameras`, `manage_cinemachine`, `capture_camera` |
| GameObjects | `gameobject_create`, `gameobject_edit`, `gameobject_duplicate`, `gameobject_positioning`, `gameobject_placement` |
| Prefabs | `prefab_inspect`, `prefab_create`, `prefab_contents`, `prefab_stage`, `prefab_overrides` |
| Scenes | `scene_lifecycle`, `scene_inspect`, `scene_validate`, `scene_reference_diagnostics` |
| Assets | `asset_inspect`, `asset_edit`, `asset_reference_diagnostics`, `asset_labels`, `asset_bundles` |
| UI Toolkit | `ui_source`, `ui_document`, `ui_inspect`, `ui_element`, `ui_capture` |

Essentials include `gameobject_create`, `gameobject_edit`, `prefab_inspect`, `scene_lifecycle`, `scene_inspect` and `asset_inspect`. More specialized features live in the optional `scene`, `assets`, `prefabs`, `camera`, `graphics`, `physics` or `ui` groups. Other groups such as `animation`, `testing` and `profiling` remain available.

With a stateful MCP handshake, enable just one tool when that is sufficient:

```text
manage_tools(action="activate", tool="query_physics")
manage_tools(action="deactivate", tool="query_physics")
manage_tools(action="activate", group="physics")
manage_tools(action="reset")
```

For activation or deactivation, specify exactly one `tool` or `group`. Selection affects only that session. Reset restores the configured initial selection and returns `visible_tools`, the resulting list after Unity availability is applied. Enabling a tool never installs a Unity package or bypasses Editor selection, authenticated catalogs or explicit consent.

## Existing combined calls

The eight original IDs (`manage_physics`, `manage_graphics`, `manage_camera`, `manage_gameobject`, `manage_prefabs`, `manage_scene`, `manage_asset`, `manage_ui`) remain available in the optional `legacy` group, shown as **Compatibility** in the Editor. Their action and parameter contracts are preserved. Enable an individual original tool or the compatibility group when an existing workflow depends on it.

Existing saved selections remain intact. When an original tool already has a saved preference, its new feature tools start disabled until explicitly selected. This avoids silently granting new tools or loading both catalogs after an upgrade. A fresh installation enables only essential built-in tools.

**Use Recommended** explicitly selects essential built-in tools and disables optional and compatibility tools. It preserves custom-tool selections and explicit-consent settings. The preset applies to rows hidden by search or the **Show** filter.

## Editor selection

On the Unity MCP window's **Tools** page, each row shows a readable feature name, its technical ID and a short purpose. **User Interface** distinguishes **uGUI / Canvas** (`manage_ugui`) from UI Toolkit source, document, inspection, element and capture operations. Compatibility rows include **(all actions)** in their name.

Search names, IDs, descriptions and groups, or filter enabled/disabled tools without changing their selection. Group counts report enabled tools; a mixed checkbox indicates a partially enabled group. Expand **Details** for output information or tool-specific settings. Camera capture settings remain on `capture_camera` and the compatibility camera tool.

The confirmation below the controls distinguishes saved settings from settings actually received by the server. After changing Editor toggles, use `manage_tools(action="sync")` for a local connection and refresh the client inventory.

## Tool inventory and model context

The server keeps standard `tools/list` responses and full schemas for visible tools. A smaller visible catalog can reduce definitions a host sends to its model, but the host decides which definitions enter model context.

The [MCP client best practices](https://github.com/modelcontextprotocol/modelcontextprotocol/blob/main/docs/docs/2026-07-28/develop/clients/client-best-practices.mdx) recommend progressive tool discovery in the client: inspect the catalog, select relevant tools and refresh it when tool-list notifications arrive. Server visibility settings complement that behavior; they do not implement host-side tool search or guarantee the same token savings in every client.

Both local transports start with essential tools. Editor synchronization updates availability without changing the client selection. Enabling one optional tool in Unity does not select its group or reveal unavailable peers. Select the desired group or individual tool separately; both HTTP and stdio apply the current project catalog after selection. An explicitly empty catalog hides native tools, while an unknown or disconnected local catalog preserves offline discovery. Native execution still requires the selected Editor and its permissions.

## Related capabilities

The `ui` group contains [`ui_source`](../reference/tools/ui/ui_source.md) for UXML/USS assets and [`manage_ugui`](../reference/tools/ui/manage_ugui.md) for Canvas-based UI. The [uGUI guide](./ugui.md) covers creation, layout editing and screen-size diagnostics.

UI Toolkit document and capture operations accept `component_type="auto"`, `"ui_document"` or `"panel_renderer"`. Auto preserves an existing UIDocument and prefers PanelRenderer for new attachments when its supported API is available. Explicit PanelRenderer selection reports a capability error on older Editors.

The `animation` group includes [`manage_animation`](../reference/tools/animation/manage_animation.md) and [`manage_sprite`](../reference/tools/animation/manage_sprite.md). Sprite-sheet inspection, slicing, clips and controller setup require support in both the Editor and server.

The `testing` group includes [`manage_input`](../reference/tools/testing/manage_input.md), alongside async tests. Its `ui_click` action dispatches uGUI events directly; it does not verify raycast occlusion or physical pointer targeting. Key, mouse and touch simulation requires the optional Input System package and compatible Active Input Handling. Inspect `status` first; holds have bounded frame counts and a hard lease.

The `core` group includes [`manage_recording`](../reference/tools/core/manage_recording.md) for bounded silent MP4 capture. Query `capabilities`, start a job, then poll or stop it explicitly. Recording depends on a supported interactive Windows/macOS Editor and native codecs. Game View capture requires running unpaused Play Mode and the visible focused view. Linux and headless recording are unsupported.

## Local sessionless clients

Use the Editor controls to allow tools, then request `manage_tools(action="sync")` from the intended Unity instance. Refresh/reconnect the MCP client if it does not process tool-list notifications. Sync preserves the server discovery profile and cannot select an optional group.

Sessionless `activate`, `deactivate` and `reset` return an explanatory failure because session selection cannot persist without a stateful handshake. Use a stateful client to change the selection. Editor controls remain the availability and consent boundary.

## Hosted clients

Hosted clients of either protocol see and can call built-in tools enabled in their authenticated Unity plugin's catalog, including optional and compatibility tools. Without a catalog, only server-side helpers are available. Unity pushes catalog changes; remote `sync` is unavailable. Selecting a group or individual tool cannot bypass Unity-disabled tools or another user's catalog.

## Missing tools versus denied operations

| Situation | Action |
|---|---|
| Tool missing from the client's list | Check group inventory and Editor toggles; refresh the client. |
| Tool present but consent denied | Grant only the intended high-impact capability in the Editor. |
| Tool requires a Unity package | Install/configure that dependency deliberately; visibility does not install it. |
| Hosted catalog absent | Check the authenticated plugin connection and catalog registration. |

See [Security And Consent](./security.md) and [`manage_tools`](../reference/tools/core/manage_tools.md).
