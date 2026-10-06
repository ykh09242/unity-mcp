---
title: execute_code
sidebar_label: execute_code
description: "Execute arbitrary C# code inside the Unity Editor."
---

# `execute_code`

> **Auto-generated** from the Python tool registry. Do not hand-edit outside `<!-- examples:start --><!-- examples:end -->` blocks — the generator (`tools/generate_docs_reference.py`) will overwrite them.

**Group:** `scripting_ext` &nbsp;·&nbsp; **Module:** `services.tools.execute_code`

## Description

Execute arbitrary C# code inside the Unity Editor. The code runs as a method body with access to UnityEngine and UnityEditor namespaces. Use 'return' to send data back. Compiled in-memory — no script files created. Actions: execute (run code), get_history (list past executions), replay (re-run a history entry with its saved safety and compiler settings), clear_history. NOTE: safety_checks blocks known dangerous patterns but is not a full sandbox. Compiler options: 'auto' (Roslyn if available, else CodeDom), 'roslyn' (language support depends on the installed Microsoft.CodeAnalysis version), 'codedom' (C# 6 only).

## Parameters

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `action` | `Literal['execute', 'get_history', 'replay', 'clear_history']` | yes | Action to perform. |
| `code` | `str \| None` | — | C# code to execute (for 'execute' action). Must be a valid method body. Access UnityEngine and UnityEditor namespaces. Use 'return' to send data back. |
| `safety_checks` | `bool` | — | Enable basic blocked-pattern checks (File.Delete, Process.Start, infinite loops, etc). For 'execute' only; replay uses the saved setting. Not a full sandbox — advanced bypass is possible. Default: true. |
| `index` | `int \| None` | — | History entry index to replay (for 'replay' action). |
| `limit` | `int` | — | Number of history entries to return (for 'get_history' action, 1-50). Default: 10. |
| `compiler` | `Literal['auto', 'roslyn', 'codedom']` | — | Compiler backend for 'execute' action. 'auto' uses Roslyn if Microsoft.CodeAnalysis is installed, else falls back to CodeDom. 'roslyn' forces Roslyn; supported language features depend on the installed Microsoft.CodeAnalysis version. 'codedom' forces legacy CSharpCodeProvider (C# 6). Default: auto. |

## Returns

A `dict` containing the Unity response. The exact shape depends on the action.

## Examples

<!-- examples:start -->
Run statements without returning a value:

```json
{"action": "execute", "code": "Debug.Log(\"Hello from MCP\");"}
```

Return structured data, including Unity values. Vectors retain their numeric components inside nested results:

```json
{"action": "execute", "code": "return new { position = new Vector3(1.234567f, 2f, 3f), count = 42 };"}
```

List recent executions, then pass an entry's `index` to `replay`:

```json
{"action": "get_history", "limit": 5}
```

```json
{"action": "replay", "index": 0}
```

Replay uses the selected entry's saved safety and compiler settings. Every execute or replay runs the code again; the compilation cache reuses compiled code, not the execution result.
<!-- examples:end -->

