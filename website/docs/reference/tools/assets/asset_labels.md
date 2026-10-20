---
title: asset_labels
sidebar_label: asset_labels
description: "Read or change asset labels with revision checks."
---

# `asset_labels`

> **Auto-generated** from the Python tool registry. Do not hand-edit outside `<!-- examples:start --><!-- examples:end -->` blocks — the generator (`tools/generate_docs_reference.py`) will overwrite them.

**Group:** `assets` &nbsp;·&nbsp; **Module:** `services.registry.capability_adapter`

## Description

Read or change asset labels with revision checks.

## Parameters

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `action` | `Literal['get_labels', 'set_labels', 'add_labels', 'remove_labels']` | yes | Feature action to perform. |
| `path` | `str \| None` | — | Asset path required for asset operations; search defaults to Assets. Ignored for bundle assignment inspection. |
| `labels` | `list[str] \| None` | — | Exact labels; set replaces, empty set clears. |
| `label_targets` | `list[AssetLabelTarget] \| None` | — | Common-operation batch; each mutation row requires revision. |
| `label_revision` | `str \| None` | — | Opaque revision from get_labels for a single-target mutation. |

## Returns

A `dict` containing the Unity response. The exact shape depends on the action.

## Examples

<!-- examples:start -->
*No examples yet. Add usage examples here — they will be preserved across regenerations.*
<!-- examples:end -->

