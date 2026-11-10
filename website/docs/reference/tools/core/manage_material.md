---
title: manage_material
sidebar_label: manage_material
description: "Manages Unity materials (properties, colors, shaders, render state)."
---

# `manage_material`

> **Auto-generated** from the Python tool registry. Do not hand-edit outside `<!-- examples:start --><!-- examples:end -->` blocks — the generator (`tools/generate_docs_reference.py`) will overwrite them.

**Group:** `core` &nbsp;·&nbsp; **Module:** `services.tools.manage_material`

**Maturity:** `stable` (support status; independent of enabled state, readiness and permissions).

## Description

Manages Unity materials (properties, colors, shaders, render state). Read-only actions: ping, get_material_info, get_render_state. Modifying actions: create, set_material_shader_property, set_material_color, assign_material_to_renderer, set_renderer_color, set_render_state. Render-state edits require explicit shared/instance mode. Explicit render_mode presets support Built-in Standard shaders; URP/HDRP/custom presets are unsupported.

## Parameters

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `action` | `Literal['ping', 'create', 'set_material_shader_property', 'set_material_color', 'assign_material_to_renderer', 'set_renderer_color', 'get_material_info', 'get_render_state', 'set_render_state']` | yes | Action to perform. |
| `material_path` | `str \| None` | — | Path to material asset (Assets/...) |
| `property` | `str \| None` | — | Shader property name (e.g., _BaseColor, _MainTex) |
| `shader` | `str \| None` | — | Shader name (default: Standard) |
| `properties` | `dict[str, Any] \| str \| None` | — | Initial properties to set as {name: value} dict. |
| `value` | `list[Any] \| dict[str, Any] \| float \| int \| str \| bool \| None` | — | Value to set: scalar, color/vector array or object, texture path or {"find": "Assets/...", "method": "by_path"} instruction. JSON-encoded strings remain supported. The native shader-property type validates the value shape. |
| `color` | `list[float] \| dict[str, float] \| str \| None` | — | Color as [r, g, b] or [r, g, b, a] array, {r, g, b, a} object, JSON string or hex. Numeric components use color_range; hex colors always use byte components. |
| `target` | `str \| None` | — | Target GameObject (name, path, or find instruction) |
| `search_method` | `Literal['by_id', 'by_name', 'by_path', 'by_tag', 'by_layer', 'by_component'] \| None` | — | Search method for target |
| `slot` | `int \| None` | — | Material slot index (0-based) |
| `mode` | `Literal['shared', 'instance', 'property_block', 'create_unique'] \| None` | — | Assignment/modification mode; behavior when omitted is action-specific on the Unity side. |
| `render_state` | `dict[str, Any] \| str \| None` | — | Render-state patch object or JSON object string: render_queue (-1 or 0..5000), keywords ({declared_name: bool}), shader_passes ({LightMode: bool}), texture_transforms ({exact_texture_property: {scale: [x,y], offset: [x,y]}}). render_mode is opaque/cutout/fade/transparent for Built-in Standard shaders only; it resets the queue and cannot coexist with render_queue or owned alpha keywords. Maps are limited to 128 entries. No automatic blending changes for raw edits. |
| `color_range` | `Literal['float', 'byte']` | — | Numeric color range: float (default) preserves HDR values and defaults alpha to 1; byte explicitly converts 0-255 components and defaults alpha to 255. Use byte for legacy 0-255 inputs; range is never inferred for material colors. |
| `unity_instance` | `str \| int \| None` | — | Target Unity instance for this call: Name@hash or hash prefix; an integer selects only that exact port over stdio and is rejected over HTTP. Read mcpforunity://instances for available targets. Omit to use the legacy active selection or the sole available instance. |

## Returns

A `dict` containing the Unity response. The exact shape depends on the action.

## Examples

<!-- examples:start -->
### Create a red material from scratch

> Create `Assets/Materials/Red.mat` using the Standard shader, base color red.

```json
{
  "action": "create",
  "material_path": "Materials/Red.mat",
  "shader": "Standard",
  "properties": { "_Color": [1, 0, 0, 1] }
}
```

For URP, use `"shader": "Universal Render Pipeline/Lit"` and property `_BaseColor`.

### Assign an existing material to a GameObject

> Apply `Assets/Materials/Red.mat` to the `RedCube`.

```json
{
  "action": "assign_material_to_renderer",
  "target": "RedCube",
  "search_method": "by_name",
  "material_path": "Materials/Red.mat",
  "slot": 0,
  "mode": "shared"
}
```

`mode: shared` reuses the asset. `mode: instance` clones it per-renderer (use sparingly — costs draw call batching).

### Change just one shader property

> Set `_Metallic` on `Materials/Red.mat` to 0.8.

```json
{
  "action": "set_material_shader_property",
  "material_path": "Materials/Red.mat",
  "property": "_Metallic",
  "value": 0.8
}
```

### Tint a renderer without touching the shared material

> Tint the cube's MeshRenderer blue using a MaterialPropertyBlock.

```json
{
  "action": "set_renderer_color",
  "target": "RedCube",
  "search_method": "by_name",
  "color": [0, 0, 1, 1],
  "mode": "property_block"
}
```

`property_block` mode avoids creating a per-instance material clone, preserving batching.
<!-- examples:end -->

