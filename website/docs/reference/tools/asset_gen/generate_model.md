---
title: generate_model
sidebar_label: generate_model
description: "Generate 3D models with AI providers (Tripo, Meshy, fal) and import them into the Unity project."
---

# `generate_model`

> **Auto-generated** from the Python tool registry. Do not hand-edit outside `<!-- examples:start --><!-- examples:end -->` blocks — the generator (`tools/generate_docs_reference.py`) will overwrite them.

**Group:** `asset_gen` &nbsp;·&nbsp; **Module:** `services.tools.generate_model`

## Description

Generate 3D models with AI providers (Tripo, Meshy, fal) and import them into the Unity project. Bring-your-own-key: provider keys live in the editor's secure store and never cross the bridge.

ACTIONS:
- generate: Submit a generation job (text->3D or image->3D). Returns { job_id } immediately; poll with the status action. Params: provider, mode (text|image), prompt, image_path|image_url, format (glb|fbx|obj|usdz), target_size, texture, tier, model, name, output_folder, allow_image_upload. Tripo local PNG/JPEG input requires allow_image_upload=true to upload original bytes including metadata and start potentially billable generation. Hosted image_url takes precedence and does not upload the local file. Tripo supports GLB generation only; omit tier. Tripo texture=false requires v3.0-20250812, v3.1-20260211, or P1-20260311. Other formats require a separate conversion workflow, which this tool does not submit. target_size applies only when Auto-Normalize Imported Models is enabled; finite zero or negative values preserve imported scale. Successful normalization of a model without ModelImporter may return a prefab variant while retaining its source model.
- status: Poll an async job by job_id; includes recovery_state, recovery_code, provider_task_id and provider_phase. Recorded provider tasks can reconnect after a domain reload within the same Editor session and original two-hour budget. Unknown submission/import outcomes are not replayed; inspect retained output before retrying. Saved local output can resume without a provider key when its recorded file/metadata identity still matches. No recovery across Editor exit is promised.
- cancel: Stop local job continuation by job_id; this does not guarantee provider-side cancellation or a refund.
- list_providers: List configured 3D providers and capabilities (no key values).
- list_models: Search/paginate live fal 3D models and bundled Tripo/Meshy models.
- refresh_models: Refresh fal discovery. Repeat list_models while catalogs[].refreshing is true.

## Parameters

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `action` | `Literal['generate', 'status', 'cancel', 'list_providers', 'list_models', 'refresh_models']` | yes | Action to perform. |
| `provider` | `str \| None` | — | Provider id (tripo, meshy, fal). Tripo and fal support GLB output only. |
| `mode` | `str \| None` | — | Generation mode: text or image. |
| `prompt` | `str \| None` | — | Text prompt for text->3D. |
| `image_path` | `str \| None` | — | Path to a source image for image->3D. |
| `image_url` | `str \| None` | — | URL of a source image for image->3D. |
| `allow_image_upload` | `bool` | — | Consent to send a selected local PNG/JPEG including metadata to Tripo for potentially billable generation; false by default. Hosted URLs take precedence. |
| `format` | `str \| None` | — | Output model format: glb, fbx, obj, or usdz, subject to provider support. Tripo supports glb only. |
| `target_size` | `float \| None` | — | Largest default-pose mesh-bounds dimension in meters when Auto-Normalize Imported Models is enabled; finite zero/negative values preserve scale. Omitted defaults to 1. Normalized glTF may return a prefab variant. |
| `texture` | `bool \| None` | — | Whether to generate textures. Tripo texture=false requires v3.0-20250812, v3.1-20260211, or P1-20260311. |
| `tier` | `str \| None` | — | Provider quality/cost tier. Omit for Tripo; nonblank values are unsupported. |
| `model` | `str \| None` | — | Provider model id/version (e.g. Tripo v3.1, Meshy meshy-6). Omit for the GUI-selected default. |
| `name` | `str \| None` | — | Base name for the imported asset. |
| `output_folder` | `str \| None` | — | Destination folder under Assets/ for the import. |
| `job_id` | `str \| None` | — | Job id for status/cancel. |
| `search` | `str \| None` | — | Filter list_models by name, id or use case. |
| `limit` | `int \| None` | — | Model page size (1..200; default 50). |
| `offset` | `int \| None` | — | Model page offset (default 0). |
| `unity_instance` | `str \| int \| None` | — | Target Unity instance for this call: Name@hash or hash prefix; an integer port is also supported over stdio. Read mcpforunity://instances for available targets. Omit to use the legacy active selection or the sole available instance. |

## Returns

A `dict` containing the Unity response. The exact shape depends on the action.

## Examples

<!-- examples:start -->
```json
{"action": "list_models", "provider": "fal", "mode": "image"}
```

CLI: `unity-mcp asset-gen list-models --kind model --provider fal --refresh` (GLB output).
<!-- examples:end -->

