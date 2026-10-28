---
title: script_apply_edits
sidebar_label: script_apply_edits
description: "Edit C# source using operations scoped to the intended members or text."
---

# `script_apply_edits`

> **Auto-generated** from the Python tool registry. Do not hand-edit outside `<!-- examples:start --><!-- examples:end -->` blocks — the generator (`tools/generate_docs_reference.py`) will overwrite them.

**Group:** `core` &nbsp;·&nbsp; **Module:** `services.tools.script_apply_edits`

## Description

Edit C# source using operations scoped to the intended members or text.
    Choose operations from the inspected source:
    - Method/class edits adapt to local indentation and line endings without changing string values.
    - Range, anchor, regex, prepend and append payloads are literal: include all desired whitespace/newlines.
    - options.preview=true prepares complete proposed contents, target paths and hashes without applying changes.
    - options.mixed_order='request' prepares mixed edits in request order against the current buffer.
    - Ordered apply requires precondition_bytes_sha256 and candidate_bytes_sha256 from its preview; it commits once.
    - Omitted or text_first mixed_order preserves text-first execution and unsupported mixed preview.
    - For Codex-native file visibility, verify local target/workspace and original SHA, use an exposed native file tool only if it preserves exact candidate bytes, then verify candidate_bytes_sha256 before Unity validate/refresh.
    - Native patch capabilities vary: CRLF, BOM and missing final newline may require a byte-preserving tool or direct Unity edit fallback. Never normalize string bytes or fabricate file events.
    - Use member operations for whole-method changes; use text/anchor operations for precise literal edits.
    - Verify anchor uniqueness and enclosing scope. Do not assume a file's final brace belongs to the intended class.
    - Validate structural changes; syntax checks do not establish runtime behavior.
    - Pass options.validate='standard' for structural checks; 'basic' for interior-only edits
    Canonical fields (use these exact keys):
    - op: replace_method | insert_method | delete_method | anchor_insert | anchor_delete | anchor_replace
    - className: string (defaults to 'name' if omitted on method/class ops)
    - methodName: string (required for replace_method, delete_method)
    - replacement: string (required for replace_method, insert_method)
    - position: start | end | after | before (insert_method only)
    - afterMethodName / beforeMethodName: string (required when position='after'/'before')
    - anchor: regex string (for anchor_* ops)
    - text: string (for anchor_insert/anchor_replace)

## Parameters

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `name` | `str` | yes | Name of the script to edit |
| `path` | `str` | yes | Path to the script to edit under Assets/ directory |
| `edits` | `list[dict[str, Any]] \| str` | yes | List of edits to apply to the script (JSON list or stringified JSON) |
| `options` | `dict[str, Any] \| None` | — | Options for the script edit |
| `script_type` | `str` | — | Type of the script to edit |
| `namespace` | `str \| None` | — | Namespace of the script to edit |
| `unity_instance` | `str \| int \| None` | — | Target Unity instance for this call: Name@hash or hash prefix; an integer port is also supported over stdio. Read mcpforunity://instances for available targets. Omit to use the legacy active selection or the sole available instance. |

## Returns

A `dict` containing the Unity response. The exact shape depends on the action.

## Examples

<!-- examples:start -->
:::tip
Prefer this over [`apply_text_edits`](./apply_text_edits.md) for method-level changes. Structured ops keep braces balanced and survive incidental whitespace drift. Use `apply_text_edits` only when you need surgical line/column patching.
:::

### Replace a single method

> In `Assets/Scripts/PlayerController.cs`, make `HasTarget` return `currentTarget != null`.

```json
{
  "name": "PlayerController",
  "path": "Scripts/",
  "edits": [
    {
      "op": "replace_method",
      "className": "PlayerController",
      "methodName": "HasTarget",
      "replacement": "public bool HasTarget() { return currentTarget != null; }"
    }
  ],
  "options": { "validate": "standard", "refresh": "immediate" }
}
```

`path` ends with `/`. `validate: 'standard'` runs Roslyn structural checks before the write commits; use `'basic'` for cheap interior-only checks when you're touching a method body and trust the signature.

### Insert a new method at the end of a class

> Add a `Reset()` method after `Update` in `PlayerController`.

```json
{
  "name": "PlayerController",
  "path": "Scripts/",
  "edits": [
    {
      "op": "insert_method",
      "className": "PlayerController",
      "methodName": "Update",
      "position": "after",
      "replacement": "void Reset() { currentTarget = null; }"
    }
  ]
}
```

`position`: `start | end | before | after`. With `before`/`after`, `methodName` is the anchor; with `start`/`end`, it's the position within the class body.

### Delete a method

> Remove `PlayerController.LegacyTick`.

```json
{
  "name": "PlayerController",
  "path": "Scripts/",
  "edits": [
    { "op": "delete_method", "className": "PlayerController", "methodName": "LegacyTick" }
  ]
}
```

### Anchor-based insert (around a regex marker)

> Add a `Debug.Log` right after the line containing `// --- input ---`.

```json
{
  "name": "PlayerController",
  "path": "Scripts/",
  "edits": [
    {
      "op": "anchor_insert",
      "anchor": "//\\s*---\\s*input\\s*---",
      "position": "after",
      "replacement": "    Debug.Log(\"input frame\");"
    }
  ]
}
```

Anchor ops are great for adding instrumentation near stable comment markers without locking into exact line numbers. The anchor is a regex; escape literal characters.

### Apply several edits atomically

> Replace two methods AND remove a third — all in one transaction. If validation fails on any, nothing is written.

```json
{
  "name": "PlayerController",
  "path": "Scripts/",
  "edits": [
    { "op": "replace_method", "methodName": "Awake",  "replacement": "void Awake() { Init(); }" },
    { "op": "replace_method", "methodName": "Update", "replacement": "void Update() { Tick(); }" },
    { "op": "delete_method",  "methodName": "OnDestroyOld" }
  ],
  "options": { "validate": "standard" }
}
```

`className` defaults to `name` when omitted on method ops, so for single-class files you can skip it.

### After every edit

Poll the `mcpforunity://editor/state` resource until `data.compilation.is_compiling` flips back to `false`, then run [`read_console`](./read_console.md) to catch any compile errors before relying on the new types.
<!-- examples:end -->

