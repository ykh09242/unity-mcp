---
title: debug_request_context
sidebar_label: debug_request_context
description: "Inspect the client request metadata, server process and routed Unity Editor."
---

# `debug_request_context`

> **Auto-generated** from the Python tool registry. Do not hand-edit outside `<!-- examples:start --><!-- examples:end -->` blocks — the generator (`tools/generate_docs_reference.py`) will overwrite them.

**Group:** `core` &nbsp;·&nbsp; **Module:** `services.tools.debug_request_context`

**Maturity:** `stable` (support status; independent of enabled state, readiness and permissions).

## Description

Inspect the client request metadata, server process and routed Unity Editor. The diagnostic does not test write access.

## Parameters

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `unity_instance` | `str \| int \| None` | — | Target Unity instance for this call: Name@hash or hash prefix; an integer selects only that exact port over stdio and is rejected over HTTP. Read mcpforunity://instances for available targets. Omit to use the legacy active selection or the sole available instance. |

## Returns

A `dict` containing the Unity response. The exact shape depends on the action.

## Examples

<!-- examples:start -->
Inspect request context from the MCP client whose routing you are diagnosing. The response can contain session identifiers and client-supplied metadata, so keep it in the diagnostic session.

```json title="MCP arguments"
{}
```
<!-- examples:end -->

