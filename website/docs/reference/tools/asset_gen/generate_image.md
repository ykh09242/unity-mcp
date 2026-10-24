---
title: generate_image
sidebar_label: generate_image
description: "Generate 2D images with AI providers (fal.ai, OpenRouter) and import them as textures/sprites into the Unity project."
---

# `generate_image`

> **Auto-generated** from the Python tool registry. Do not hand-edit outside `<!-- examples:start --><!-- examples:end -->` blocks — the generator (`tools/generate_docs_reference.py`) will overwrite them.

**Group:** `asset_gen` &nbsp;·&nbsp; **Module:** `services.tools.generate_image`

## Description

Generate 2D images with AI providers (fal.ai, OpenRouter) and import them as textures/sprites into the Unity project. Bring-your-own-key: provider keys live in the editor's secure store and never cross the bridge.

ACTIONS:
- generate: Submit an image job (text->image or image->image). Returns { job_id }; poll with the status action. Params: provider, mode (text|image), prompt, image_path|image_url, model, transparent, width, height, name, output_folder.
- status: Poll an async job by job_id; includes recovery_state, recovery_code, provider_task_id and provider_phase. Recorded provider tasks can reconnect after a domain reload within the same Editor session and original two-hour budget. Unknown submission/import outcomes are not replayed; inspect retained output before retrying. Saved local output can resume without a provider key when its recorded file/metadata identity still matches. OpenRouter responses not yet saved locally cannot reconnect.
- cancel: Stop local job continuation by job_id; this does not guarantee provider-side cancellation or a refund.
- list_providers: List configured image providers and capabilities (no key values).
- list_models: Search/paginate live fal and OpenRouter models, modes and freshness.
- refresh_models: Force discovery. Repeat list_models while catalogs[].refreshing is true.

Background removal is unsupported. Legacy direct remove_background requests return an unsupported_capability error without submitting a job. The transparent option only sets the Unity texture import flag; it does not remove backgrounds.

## Parameters

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `action` | `Literal['generate', 'status', 'cancel', 'list_providers', 'list_models', 'refresh_models']` | yes | Action to perform. |
| `provider` | `str \| None` | — | Provider id (fal, openrouter). |
| `mode` | `str \| None` | — | Generation mode: text or image. |
| `prompt` | `str \| None` | — | Text prompt for text->image. |
| `image_path` | `str \| None` | — | Path to a source image for image->image mode. |
| `image_url` | `str \| None` | — | URL of a source image for image->image. |
| `model` | `str \| None` | — | Provider model id/slug returned by list_models; omit for the GUI-selected default. |
| `transparent` | `bool \| None` | — | Mark the imported texture as alpha-is-transparency. NOTE: fal/FLUX and OpenRouter have no generation-time transparency, so this only sets the Unity import flag — it does not make the model render a transparent background. |
| `width` | `int \| None` | — | Output width in pixels. |
| `height` | `int \| None` | — | Output height in pixels. |
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
{"action": "list_models", "provider": "openrouter", "search": "flux", "limit": 20}
```

CLI: `unity-mcp asset-gen list-models --kind image --provider openrouter --refresh`.
<!-- examples:end -->

