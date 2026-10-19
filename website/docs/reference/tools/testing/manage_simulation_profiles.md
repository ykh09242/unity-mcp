---
title: manage_simulation_profiles
sidebar_label: manage_simulation_profiles
description: "Capture named Time/Physics3D/Physics2D/Audio/Input configuration profiles, inspect per-domain applicability, diff/validate against current settings, explicitly apply, and conditionally restore confirmed changes. capabilities lists exact…"
---

# `manage_simulation_profiles`

> **Auto-generated** from the Python tool registry. Do not hand-edit outside `<!-- examples:start --><!-- examples:end -->` blocks — the generator (`tools/generate_docs_reference.py`) will overwrite them.

**Group:** `testing` &nbsp;·&nbsp; **Module:** `services.tools.manage_simulation_profiles`

## Description

Capture named Time/Physics3D/Physics2D/Audio/Input configuration profiles, inspect per-domain applicability, diff/validate against current settings, explicitly apply, and conditionally restore confirmed changes. capabilities lists exact supported keys. capture optionally stores validated settings replacements without applying globals. apply requires profile_revision and current_revision returned by validate/diff. restore requires a retained receipt_id and preserves external writer drift, owner changes and uncertain writes. No cross-domain transaction, automatic simulation, input injection, hardware audibility, persistent Undo or asset save is promised. Profiles/receipts are bounded project-local Library metadata. delete removes a named profile plus its receipts and does not restore settings; restore first when needed.

## Parameters

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `action` | `Literal['capabilities', 'capture', 'list', 'get', 'diff', 'validate', 'apply', 'restore', 'receipt', 'delete']` | yes | Explicit named profile action. |
| `name` | `str \| None` | — | Exact profile name, 1..64 visible characters. |
| `domains` | `list[Literal['time', 'physics3d', 'physics2d', 'audio', 'input']] \| None` | — | Capture domains; omitted captures currently applicable supported domains. |
| `settings` | `dict[str, JsonValue] \| None` | — | Optional supported replacement values stored by capture, at most 32 flat keys such as time.timeScale, physics3d.gravity, audio.volume. No global application here. |
| `profile_revision` | `str \| None` | — | Required for apply or replacement capture; read from get/validate/diff. |
| `current_revision` | `str \| None` | — | Required for apply; exact current-state revision returned by validate/diff. |
| `receipt_id` | `str \| None` | — | Retained apply receipt for receipt/restore. |
| `offset` | `int \| None` | — | Nonnegative list offset. |
| `limit` | `int \| None` | — | List page size 1..32, default 20. |

## Returns

A `dict` containing the Unity response. The exact shape depends on the action.

## Examples

<!-- examples:start -->
*No examples yet. Add usage examples here — they will be preserved across regenerations.*
<!-- examples:end -->

