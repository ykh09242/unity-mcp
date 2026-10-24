---
title: manage_netcode
sidebar_label: manage_netcode
description: "Inspect local Netcode for GameObjects configuration without starting a network session. capabilities reports optional package detection/version; preflight requires an explicit loaded scene_handle and checks managers, active same-scene tr…"
---

# `manage_netcode`

> **Auto-generated** from the Python tool registry. Do not hand-edit outside `<!-- examples:start --><!-- examples:end -->` blocks — the generator (`tools/generate_docs_reference.py`) will overwrite them.

**Group:** `core` &nbsp;·&nbsp; **Module:** `services.tools.manage_netcode`

## Description

Inspect local Netcode for GameObjects configuration without starting a network session. capabilities reports optional package detection/version; preflight requires an explicit loaded scene_handle and checks managers, active same-scene transports, raw serialized prefab registrations, root NetworkObject references and supported scalar settings. expected_settings compares selected values; omitted/null values add no expectation. Multiple managers compare unspecified settings against the first manager. Limits: 16 managers, 10000 scene objects, max_prefabs total registrations and 200 diagnostics; budget overflow fails without reporting readiness. Package absence is explicit. Static configuration only: no connectivity, peer/build compatibility, hosting, deployment, runtime prefab or callback guarantee; no implicit installation or configuration edits.

## Parameters

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `action` | `Literal['capabilities', 'preflight']` | yes | Read-only diagnostic action. |
| `scene_handle` | `int \| None` | — | Exact loaded scene handle for preflight. |
| `expected_settings` | `NetcodeExpectedSettings \| None` | — | Supported NetworkConfig scalar expectations. |
| `max_prefabs` | `int` | — | Maximum total inspected prefab registrations. |
| `unity_instance` | `str \| int \| None` | — | Target Unity instance for this call: Name@hash or hash prefix; an integer port is also supported over stdio. Read mcpforunity://instances for available targets. Omit to use the legacy active selection or the sole available instance. |

## Returns

A `dict` containing the Unity response. The exact shape depends on the action.

## Examples

<!-- examples:start -->
*No examples yet. Add usage examples here — they will be preserved across regenerations.*
<!-- examples:end -->

