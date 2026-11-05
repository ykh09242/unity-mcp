---
title: batch_execute
sidebar_label: batch_execute
description: "Execute multiple native MCP commands in one request."
---

# `batch_execute`

> **Auto-generated** from the Python tool registry. Do not hand-edit outside `<!-- examples:start --><!-- examples:end -->` blocks — the generator (`tools/generate_docs_reference.py`) will overwrite them.

**Group:** `core` &nbsp;·&nbsp; **Module:** `services.tools.batch_execute`

**Maturity:** `stable` (support status; independent of enabled state, readiness and permissions).

## Description

Execute multiple native MCP commands in one request. Batch independent operations whose inputs are already known; keep operations needing earlier returned IDs/results in separate calls. Batching reduces round trips, with no guaranteed latency or token savings. It is not transactional: inspect each indexed outcome, and observe uncertain writes before retrying. The command limit is configurable in the Unity MCP Tools window (default 100, hard max 1000). Use small bounded batches. Preview validates supported scope without running children; applying a target guard rechecks targets/permissions without a rollback guarantee.

## Parameters

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `commands` | `list[dict[str, Any]]` | yes | List of commands with 'tool' and 'params' keys. |
| `parallel` | `bool \| None` | — | Compatibility request; the Editor currently executes commands sequentially |
| `fail_fast` | `bool \| None` | — | Stop processing after the first failure |
| `max_parallelism` | `int \| None` | — | Compatibility hint; the Editor currently executes commands sequentially |
| `validate_only` | `bool` | — | Validate action names without executing children; requires the peer preview capability |
| `preview_scope` | `Literal['action_names', 'targets']` | — | With validate_only, inspect action names or supported concrete targets |
| `preview` | `dict[str, Any] \| None` | — | Unmodified target-preview guard to recheck before supported mutations; no rollback or retry guarantee |
| `unity_instance` | `str \| int \| None` | — | Target Unity instance for this call: Name@hash or hash prefix; an integer selects only that exact port over stdio and is rejected over HTTP. Read mcpforunity://instances for available targets. Omit to use the legacy active selection or the sole available instance. |

## Returns

A `dict` containing the Unity response. The exact shape depends on the action.

## Examples

<!-- examples:start -->
### Why batch?

A batch reduces Unity round trips when the next command does not need the previous command's return value. Actual latency depends on the commands and environment.

### Spawn three colored cubes in one round trip

> Create a red, blue, and yellow cube at x = -1, 0, 1.

```json
{
  "commands": [
    { "tool": "manage_gameobject", "params": {
        "action": "create", "name": "RedCube",
        "primitive_type": "Cube", "position": [-1, 0, 0]
    }},
    { "tool": "manage_gameobject", "params": {
        "action": "create", "name": "BlueCube",
        "primitive_type": "Cube", "position": [0, 0, 0]
    }},
    { "tool": "manage_gameobject", "params": {
        "action": "create", "name": "YellowCube",
        "primitive_type": "Cube", "position": [1, 0, 0]
    }},
    { "tool": "manage_material", "params": {
        "action": "create", "material_path": "Materials/Red.mat",
        "shader": "Standard", "properties": { "_Color": [1, 0, 0, 1] }
    }},
    { "tool": "manage_material", "params": {
        "action": "create", "material_path": "Materials/Blue.mat",
        "shader": "Standard", "properties": { "_Color": [0, 0, 1, 1] }
    }},
    { "tool": "manage_material", "params": {
        "action": "create", "material_path": "Materials/Yellow.mat",
        "shader": "Standard", "properties": { "_Color": [1, 1, 0, 1] }
    }},
    { "tool": "manage_material", "params": {
        "action": "assign_material_to_renderer", "target": "RedCube",
        "search_method": "by_name", "material_path": "Materials/Red.mat"
    }},
    { "tool": "manage_material", "params": {
        "action": "assign_material_to_renderer", "target": "BlueCube",
        "search_method": "by_name", "material_path": "Materials/Blue.mat"
    }},
    { "tool": "manage_material", "params": {
        "action": "assign_material_to_renderer", "target": "YellowCube",
        "search_method": "by_name", "material_path": "Materials/Yellow.mat"
    }}
  ]
}
```

### Mix tools freely

A batch can mix any tools, as long as ordering inside the batch doesn't depend on a previous call's *return value*. If you need the response of step N to feed step N+1, split into two batches.

### Stop on first failure vs continue

Set `fail_fast: true` to abort the rest on the first failed step. Pass `fail_fast: false` (the default) to attempt every operation and collect per-step results, useful for "best-effort cleanup" patterns.

### Parallel compatibility options

`parallel` and `max_parallelism` are compatibility options. The current Editor executes commands sequentially, including reads; a parallel request does not guarantee concurrency.

### Limits

Batch size is configurable in the Unity MCP Tools window (default 100, hard max 1,000). The dedicated `batch run` and `batch inline` CLI commands admit up to 1,000 commands locally; the selected Editor enforces its configured limit and returns a failure when it is exceeded. Past that limit, split into multiple batches.
### Preview action names without executing children

Set `validate_only: true` for a preview. It uses the separate native `batch_validate`
capability, linked to the existing batch enable/consent gate. Older peers refuse
this capability; the server never falls back to executing a batch. False or omitted
preserves the existing execution wire. Both batch names are forbidden as members.

Preview checks every command's envelope, registered handler, enabled state and the
shared native action catalogue for `manage_gameobject`, `manage_components` and
`manage_scene`. The scope is **action names**, not complete parameters, target
existence, assets, future authorization or execution success. Other registered
handlers/custom commands are explicitly incomplete, never approved by probing them.
A moved scene action remains rejected. Names in new receipts are limited to 128 UTF-16
code units with a truncation flag; input indices preserve duplicate names.

The returned `data.validation` identifies `contractVersion: 1`, `validateOnly: true`
and `scope: action_names`, with exactly one ordered item per request. Each item has a
valid, rejected or incomplete status and a fixed reason. An incomplete preview is
not valid. Preview invokes no requested handler, changes no supplied input, and
requires the same full member grants and selected-plugin intersection as execution.
Preview neither reserves permission nor bypasses the batch's explicit consent.

### Read partial outcomes honestly

A normally returned execution response retains its attempted-only `results`, raw
handler payloads and `callSuccessCount`/`callFailureCount`. The additive `outcomes`
contains every input index as succeeded, failed or not_run. Policy refusal rows
count as attempted; a fail-fast suffix is not_run. `requestedCount`, `attemptedCount`
and `notRunCount` explain this distinction. `executionStatus` describes processing
completion, not whether every call succeeded. Bounded invalid envelopes have zero
attempts and all outcomes not_run/invalid_batch while retaining existing error fields.

`rollbackPerformed` is false. A failed handler can have changed state. Preview is
read-only for child operations; ordinary execution may read or mutate according to
each member. Cancellation, domain reload or a lost reply retains the existing unknown
outcome contract; absent receipts do not prove that any particular item did not run.
Inspect state before deciding whether to retry. Parallel requests still run sequentially.
<!-- examples:end -->

