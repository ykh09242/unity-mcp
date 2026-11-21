---
title: create_script
sidebar_label: create_script
description: "Create a new C# script at the given project path."
---

# `create_script`

> **Auto-generated** from the Python tool registry. Do not hand-edit outside `<!-- examples:start --><!-- examples:end -->` blocks — the generator (`tools/generate_docs_reference.py`) will overwrite them.

**Group:** `core` &nbsp;·&nbsp; **Module:** `services.tools.manage_script`

**Maturity:** `stable` (support status; independent of enabled state, readiness and permissions).

## Description

Create a new C# script at the given project path.

## Parameters

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `path` | `str` | yes | Path under Assets/ to create the script at, e.g., 'Assets/Scripts/My.cs' |
| `contents` | `str` | yes | Contents of the script to create (plain text C# code). The server handles Base64 encoding. |
| `script_type` | `str \| None` | — | Script type (e.g., 'C#') |
| `namespace` | `str \| None` | — | Namespace for the script |
| `verification` | `Literal['auto', 'off']` | — | Bounded post-save source verification, with continuation for pending analysis. |
| `unity_instance` | `str \| int \| None` | — | Target Unity instance for this call: Name@hash or hash prefix; an integer selects only that exact port over stdio and is rejected over HTTP. Read mcpforunity://instances for available targets. Omit to use the legacy active selection or the sole available instance. |

## Returns

A `dict` containing the Unity response. The exact shape depends on the action.

## Examples

<!-- examples:start -->
Create a new component script in a scratch project. Choose an unused path whose filename matches the class. This defers additional tool verification. Unity may still import and compile the new source.

```json title="MCP arguments"
{
  "path": "Assets/Scripts/PrototypeProbe.cs",
  "contents": "using UnityEngine;\n\npublic sealed class PrototypeProbe : MonoBehaviour\n{\n}\n",
  "verification": "off"
}
```
<!-- examples:end -->

