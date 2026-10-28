---
title: Resource reference
sidebar_label: Resources
slug: /reference/resources
description: Auto-generated catalog of every MCP for Unity resource.
---

# Resource reference

> **Auto-generated** from the Python tool registry. Do not hand-edit outside `<!-- examples:start --><!-- examples:end -->` blocks — the generator (`tools/generate_docs_reference.py`) will overwrite them.

Resources are read-only state surfaces exposed to MCP clients. Tools mutate; resources observe.

## `cameras`

**URI:** `mcpforunity://scene/cameras`

List all cameras in the scene (Unity Camera + CinemachineCamera) with status. Includes Brain state, Cinemachine camera priorities, pipeline components, follow/lookAt targets, and Unity Camera info.

URI: mcpforunity://scene/cameras


## `connection_doctor`

**URI:** `mcpforunity://doctor`

Read current bounded connection/native-read/source evidence for this MCP request. Does not attest another client application's connection or change configuration.


## `custom_tools`

**URI:** `mcpforunity://custom-tools`

Lists custom tools available for the active Unity project.

URI: mcpforunity://custom-tools


## `editor_active_tool`

**URI:** `mcpforunity://editor/active-tool`

Currently active editor tool (Move, Rotate, Scale, etc.) and transform handle settings.

URI: mcpforunity://editor/active-tool


## `editor_prefab_stage`

**URI:** `mcpforunity://editor/prefab-stage`

Current prefab editing context if a prefab is open in isolation mode. Returns isOpen=false if no prefab is being edited.

URI: mcpforunity://editor/prefab-stage


## `editor_selection`

**URI:** `mcpforunity://editor/selection`

Detailed information about currently selected objects in the editor, including GameObjects, assets, and their properties.

URI: mcpforunity://editor/selection


## `editor_state`

**URI:** `mcpforunity://editor/state`

Canonical editor readiness snapshot. Includes advice, server-computed staleness, and native_coordination from native admission. recovery_required blocks readiness even when compilation is idle; follow its recovery action instead of polling blindly. Also includes assets.external_changes_scan_complete/limited coverage. scan_in_progress distinguishes a routine recheck from verified coverage. Pending, limited or unknown local coverage is reported in advice.warnings without blocking native readiness; test admission requires verified local coverage. Only active scans suggest waiting; limited or unknown scans require inspection. ready_for_tools does not certify that all external changes were scanned or imported. Unknown remote coverage remains null.

URI: mcpforunity://editor/state


## `editor_windows`

**URI:** `mcpforunity://editor/windows`

All currently open editor windows with their titles, types, positions, and focus state.

URI: mcpforunity://editor/windows


## `gameobject`

**URI:** `mcpforunity://scene/gameobject/{instance_id}`

Get bounded GameObject metadata by instance ID: scalar transforms, parent, first 50 child IDs and up to 64 component type names. Follow childrenNextCursor via /children?page_size=50&cursor=... . Children use live sibling indices; restart after any hierarchy edit.

URI: mcpforunity://scene/gameobject/{instance_id}

**Parameters:**

- `instance_id` (`str`, required)

## `gameobject_api`

**URI:** `mcpforunity://scene/gameobject-api`

Documentation for GameObject resources. Use find_gameobjects tool to get instance IDs, then access resources below.

URI: mcpforunity://scene/gameobject-api

**Parameters:**

- `_ctx` (`Context`, required)

## `gameobject_by_handle`

**URI:** `mcpforunity://scene/gameobject-by-handle/{handle}`

Read the original loaded GameObject and bounded metadata from a v2 session handle. Handles expire on scene reopen, domain reload, Play transitions, Undo/Redo, fixed 600-second lifetime or earlier eviction. Never loads scenes/assets; respects the original GameObject resource policy. Requires v2 Editor support.

**Parameters:**

- `handle` (`str`, required)

## `gameobject_children`

**URI:** `mcpforunity://scene/gameobject/{instance_id}/children{?page_size,cursor}`

Read bounded direct child IDs in sibling order plus object metadata. page_size defaults to 50 (1-500). Reuse childrenNextCursor with identical target/page_size within 30 seconds. Stable sources page exactly; live-index cursors perform bounded observed-change checks, so restart after any hierarchy edit. Cursors expire on reload or eviction.

**Parameters:**

- `instance_id` (`str`, required) — Scene GameObject instance ID returned by find_gameobjects.
- `page_size` (`int`, optional) — Direct child IDs per page, from 1 to 500; default 50.
- `cursor` (`str | None`, optional) — Opaque childrenNextCursor from the preceding page; omit to start. Keep the same target and page_size.

## `gameobject_component`

**URI:** `mcpforunity://scene/gameobject/{instance_id}/component/{component_name}{?component_index,component_instance_id,strict_selection,evaluate_getters}`

Get a component on a GameObject. The bare URI preserves legacy case-insensitive first-match reads. Optional component_index or component_instance_id selects one live component; strict_selection=true requires uniqueness. Selected reads use the same canonical, case-sensitive component type as manage_components. Indices are zero-based within GetComponents(requested type), not the mixed component list. Prefer the component instanceID from /components; IDs and indices are ephemeral. Unsupported Editors fail without falling back. Default inspection reads fields and safe native properties. evaluate_getters=true explicitly runs user getters and serialization hooks, which can block the Editor.

**Parameters:**

- `instance_id` (`str`, required)
- `component_name` (`str`, required)
- `component_index` (`str | None`, optional)
- `component_instance_id` (`str | None`, optional)
- `strict_selection` (`str | bool`, optional)
- `evaluate_getters` (`str | bool`, optional)

## `gameobject_components`

**URI:** `mcpforunity://scene/gameobject/{instance_id}/components{?page_size,cursor,include_properties,evaluate_getters}`

Get components on a GameObject. Optional URI query parameters: page_size (default 25), cursor (default 0), include_properties (default true), and evaluate_getters (default false). Default reads use fields and safe native properties. Explicit getter evaluation can run user code and block the Editor.

URI: mcpforunity://scene/gameobject/{instance_id}/components{?page_size,cursor,include_properties,evaluate_getters}

**Parameters:**

- `instance_id` (`str`, required)
- `page_size` (`int`, optional)
- `cursor` (`int`, optional)
- `include_properties` (`bool`, optional)
- `evaluate_getters` (`str | bool`, optional)

## `get_tests`

**URI:** `mcpforunity://tests`

Provides the first page of Unity tests (default 50 items). For mode filtering, read mcpforunity://tests/{mode}. Use the get_tests tool for name filtering and further pages.

URI: mcpforunity://tests


## `get_tests_for_mode`

**URI:** `mcpforunity://tests/{mode}`

Provides the first page of tests for a specific mode (EditMode or PlayMode). Use the get_tests tool for name filtering and further pages.

URI: mcpforunity://tests/{mode}

**Parameters:**

- `mode` (`Literal['EditMode', 'PlayMode']`, required) — The mode to filter tests by (EditMode or PlayMode).

## `menu_items`

**URI:** `mcpforunity://menu-items`

Lists managed MenuItem attribute declarations; this is not a complete native menu inventory or availability report.

URI: mcpforunity://menu-items


## `named_tool_diagnostics`

**URI:** `mcpforunity://tool-diagnostics/{tool_name}`

Read bounded diagnostics for one tool; schemas remain authoritative in tools/list.

**Parameters:**

- `tool_name` (`str`, required)

## `prefab_api`

**URI:** `mcpforunity://prefab-api`

Documentation for Prefab resources. Use manage_asset action=search filterType=Prefab to find prefabs, then access resources below.

URI: mcpforunity://prefab-api

**Parameters:**

- `_ctx` (`Context`, required)

## `prefab_hierarchy`

**URI:** `mcpforunity://prefab/{encoded_path}/hierarchy`

Get the full hierarchy of a prefab with nested prefab information. Returns all GameObjects with their components and nesting depth.

URI: mcpforunity://prefab/{encoded_path}/hierarchy

**Parameters:**

- `encoded_path` (`str`, required)

## `prefab_info`

**URI:** `mcpforunity://prefab/{encoded_path}`

Get detailed information about a prefab asset by URL-encoded path. Returns prefab type, root object name, component types, child count, and variant info.

URI: mcpforunity://prefab/{encoded_path}

**Parameters:**

- `encoded_path` (`str`, required)

## `project_capabilities`

**URI:** `mcpforunity://capabilities`

Current selected project's supported features, bounded limits, connection generation and recovery actions. Package presence and scene/runtime readiness are distinct. Unknown means unobserved; Player readiness requires existing bundle preflight. At admission capacity, connection and pending request counts reflect server transport records; native readiness and features stay unknown. Counts are not Unity's queue. One read budget includes resource selection; separately bounded transport cancellation grace is reported in limits. No completed query cache (TTL 0). URI: mcpforunity://capabilities


## `project_info`

**URI:** `mcpforunity://project/info`

Static project information including root path, Unity version, and platform. This data rarely changes.

URI: mcpforunity://project/info


## `project_layers`

**URI:** `mcpforunity://project/layers`

All layers defined in the project's TagManager with their indices (0-31). Read this before using add_layer or remove_layer tools.

URI: mcpforunity://project/layers


## `project_tags`

**URI:** `mcpforunity://project/tags`

All tags defined in the project's TagManager. Read this before using add_tag or remove_tag tools.

URI: mcpforunity://project/tags


## `renderer_features`

**URI:** `mcpforunity://pipeline/renderer-features`

Lists all URP renderer features on the active renderer with type, name, and active state.


## `rendering_stats`

**URI:** `mcpforunity://rendering/stats`

Snapshot of rendering counters (draw calls, batches, triangles, texture counts and bytes, etc.).


## `runtime_doctor`

**URI:** `mcpforunity://doctor/runtime`

Read resolved/reviewed SDK versions and optional adapter state without contacting Unity or changing configuration.


## `scoped_project_capabilities`

**URI:** `mcpforunity://capabilities/scoped{?features,sources}`

Optional comma-separated feature/source selection; always reads current Editor state. Unrequested observations are unknown with reason not_requested; partial refers to the full view. Default full resource and doctor are unchanged. No completed-query cache. URI: mcpforunity://capabilities/scoped{?features,sources}

**Parameters:**

- `features` (`str | None`, optional)
- `sources` (`str | None`, optional)

## `tool_diagnostics`

**URI:** `mcpforunity://tool-diagnostics`

Bounded current discovery and registration diagnostics; unknown when unobserved.


## `tool_groups`

**URI:** `mcpforunity://tool-groups`

Available tool groups and their tools. Use manage_tools to activate/deactivate groups per session.

URI: mcpforunity://tool-groups


## `unity_instances`

**URI:** `mcpforunity://instances`

Lists all running Unity Editor instances with their details.

URI: mcpforunity://instances


## `volumes`

**URI:** `mcpforunity://scene/volumes`

Lists all Volume components in the active scene with their profiles, effects, and settings.


