---
title: capture_ui_snapshot
sidebar_label: capture_ui_snapshot
description: "Capture a bounded complete semantic scene UI snapshot for ugui or uitoolkit."
---

# `capture_ui_snapshot`

> **Auto-generated** from the Python tool registry. Do not hand-edit outside `<!-- examples:start --><!-- examples:end -->` blocks — the generator (`tools/generate_docs_reference.py`) will overwrite them.

**Group:** `ui` &nbsp;·&nbsp; **Module:** `services.tools.capture_ui_snapshot`

## Description

Capture a bounded complete semantic scene UI snapshot for ugui or uitoolkit. Returns stable hierarchy identities, text, active/render eligibility/interactable state, current bounds and renderer-cull evidence where available. Does not write files or change resolution/layout. Ambiguous identities, overflow and unavailable live UI trees fail. Actual occlusion, pixel visibility and event delivery are not proven.

## Parameters

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `framework` | `Literal['ugui', 'uitoolkit']` | yes |  |
| `target` | `str \| int` | yes |  |
| `max_nodes` | `int` | — |  |

## Returns

A `dict` containing the Unity response. The exact shape depends on the action.

## Examples

<!-- examples:start -->
*No examples yet. Add usage examples here — they will be preserved across regenerations.*
<!-- examples:end -->

