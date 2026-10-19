---
title: manage_addressables
sidebar_label: manage_addressables
description: "Inspect existing optional Addressables settings (groups, explicit entries and raw profiles), run bounded preflight for duplicate addresses/missing references/profile issues, or analyze an existing2.7.6 native build-layout report for dupl…"
---

# `manage_addressables`

> **Auto-generated** from the Python tool registry. Do not hand-edit outside `<!-- examples:start --><!-- examples:end -->` blocks — the generator (`tools/generate_docs_reference.py`) will overwrite them.

**Group:** `core` &nbsp;·&nbsp; **Module:** `services.tools.manage_addressables`

## Description

Inspect existing optional Addressables settings (groups, explicit entries and raw profiles), run bounded preflight for duplicate addresses/missing references/profile issues, or analyze an existing2.7.6 native build-layout report for duplicated content. Reads never create settings, evaluate arbitrary static profile properties or start content builds. Guarded set_address/move_entry/set_profile_value preserve existing GUIDs/labels. Supply explicit settings_path and exact expected_revision for edits/continuation pages. No group/asset creation, content-build action or remote deployment. Caps128groups/4096entries/32profiles/64variables,256preflightentries,100rows/256KiB per response. Native layout files are bounded to16MiB; unsupported versions are explicit.

## Parameters

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `action` | `str` | yes |  |
| `settings_path` | `str \| None` | — |  |
| `section` | `str \| None` | — |  |
| `page_number` | `int` | — |  |
| `page_size` | `int` | — |  |
| `expected_revision` | `str \| None` | — |  |
| `entry_guid` | `str \| None` | — |  |
| `group_guid` | `str \| None` | — |  |
| `address` | `str \| None` | — |  |
| `profile_id` | `str \| None` | — |  |
| `variable_name` | `str \| None` | — |  |
| `value` | `str \| None` | — |  |
| `layout_path` | `str \| None` | — |  |

## Returns

A `dict` containing the Unity response. The exact shape depends on the action.

## Examples

<!-- examples:start -->
*No examples yet. Add usage examples here — they will be preserved across regenerations.*
<!-- examples:end -->

