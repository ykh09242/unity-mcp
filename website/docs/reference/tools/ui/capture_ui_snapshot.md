---
title: capture_ui_snapshot
sidebar_label: capture_ui_snapshot
description: "Capture a bounded complete semantic scene UI snapshot for ugui or uitoolkit."
---

# `capture_ui_snapshot`

> **Auto-generated** from the Python tool registry. Do not hand-edit outside `<!-- examples:start --><!-- examples:end -->` blocks — the generator (`tools/generate_docs_reference.py`) will overwrite them.

**Group:** `ui` &nbsp;·&nbsp; **Module:** `services.tools.capture_ui_snapshot`

## Description

Capture a bounded complete semantic scene UI snapshot for ugui or uitoolkit. Returns stable hierarchy identities, text, active/render eligibility/interactable state, current bounds and renderer-cull evidence where available. Does not write files or change resolution/layout. Ambiguous identities, overflow and unavailable live UI trees fail. Actual occlusion, pixel visibility and event delivery are not proven. Default captures use schema version 2 with fixed typed control states. Recognized editable input contents and owned text renderers are omitted before text access; ordinary labels remain. Native inventory allocation/time is separate from managed processing bounds. Use matching updated plugin/server versions; legacy v1 is supported only for offline comparison. Optional identity_map associates current child paths with nonsecret caller item IDs in either framework, emitting schema version 3 comparison identities. Update associations after row rebinding/reorder; valid stale associations cannot be detected. Mapped IDs are not mutation handles. Omission preserves schema version 2 and semantic ambiguity refusal.

## Parameters

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `framework` | `Literal['ugui', 'uitoolkit']` | yes |  |
| `target` | `str \| int` | yes |  |
| `max_nodes` | `int` | — |  |
| `identity_map` | `list[UiIdentityAssignment]` | — | Optional current-path to nonsecret item-ID map. At most max_nodes minus one entries and 65536 aggregate path/ID characters; duplicate paths/IDs refuse. Omit for semantic v2 capture; explicit null/empty refuse. Mapped v3 identities remain parent-scoped and require caller rebinding; they are not mutation handles. |

## Returns

A `dict` containing the Unity response. The exact shape depends on the action.

## Examples

<!-- examples:start -->
*No examples yet. Add usage examples here — they will be preserved across regenerations.*
<!-- examples:end -->

