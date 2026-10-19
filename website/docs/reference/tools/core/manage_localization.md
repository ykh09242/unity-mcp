---
title: manage_localization
sidebar_label: manage_localization
description: "Inspect optional Unity Localization locale/table identities, shared keys, missing translations, static Smart String selectors and asset GUID references."
---

# `manage_localization`

> **Auto-generated** from the Python tool registry. Do not hand-edit outside `<!-- examples:start --><!-- examples:end -->` blocks — the generator (`tools/generate_docs_reference.py`) will overwrite them.

**Group:** `core` &nbsp;·&nbsp; **Module:** `services.tools.manage_localization`

## Description

Inspect optional Unity Localization locale/table identities, shared keys, missing translations, static Smart String selectors and asset GUID references. No runtime formatting, loading or locale activation. inspect requires 1..16 explicit table_paths; argument_names is the caller's available root-selector inventory, not proof of custom formatter or runtime variable behavior. list_locales/list_tables use optional exact paths or bounded Assets discovery. set_string_entry corrects an existing shared key ID in one table and preserves is_smart when omitted. set_fallbacks replaces only explicit locale fallback metadata, refusing cycles. Writes require expected_revision from inspection and save the exact asset with native Undo. Revision includes loaded serialization and source bytes; external process races are not excluded. set_asset_entry explicitly refuses: supported package authoring also changes Addressables groups/labels/shared metadata. No asset/table/locale creation, key deletion or implicit Addressables changes.

## Parameters

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `action` | `Literal['capabilities', 'list_locales', 'list_tables', 'inspect', 'set_string_entry', 'set_asset_entry', 'set_fallbacks']` | yes | Explicit read or correction action. |
| `paths` | `list[str] \| None` | — | Optional exact asset paths for discovery. |
| `table_paths` | `list[str] \| None` | — | Explicit tables to inspect, at most 16. |
| `table_path` | `str \| None` | — | Exact table asset for correction. |
| `locale_path` | `str \| None` | — | Exact Locale asset for fallback correction. |
| `key_id` | `str \| None` | — | Existing positive Int64 shared key ID as a decimal string. |
| `value` | `str \| None` | — | New string value, including an explicit empty value. |
| `is_smart` | `bool \| None` | — | Explicit Smart String flag; omit to preserve. |
| `asset_path` | `str \| None` | — | Asset correction is refused because it requires Addressables authoring. |
| `argument_names` | `list[str] \| None` | — | Available root selectors for static smart diagnostics. |
| `fallback_locale_paths` | `list[str] \| None` | — | Ordered fallbacks; explicit empty list clears fallback metadata only. |
| `expected_revision` | `str \| None` | — | Exact current asset revision from inspection. |
| `offset` | `int \| None` | — | Page offset. |
| `limit` | `int \| None` | — | Discovery page size, default 50. |

## Returns

A `dict` containing the Unity response. The exact shape depends on the action.

## Examples

<!-- examples:start -->
*No examples yet. Add usage examples here — they will be preserved across regenerations.*
<!-- examples:end -->

