# State And Resource Contracts

Discover resource/template availability first. Resource reads inspect state, but some trigger discovery or local metadata work; they are not free health probes. Responses usually wrap the payload in `data`. Check `success` and the actual envelope before extracting fields.

## Readiness

`mcpforunity://editor/state` exposes the versioned readiness snapshot. Relevant fields:

| Field under data | Meaning |
|---|---|
| `unity.instance_id` | Editor associated with this observation |
| `compilation.is_compiling` | Script compilation underway |
| `compilation.is_domain_reload_pending` | Reload pending |
| `assets.is_updating`, `assets.refresh.is_refresh_in_progress` | Import/refresh state |
| `tests.is_running`, `tests.current_job_id` | Active test run |
| `editor.play_mode.is_playing`, `is_paused` | Playback state |
| `editor.active_scene.path` | Active scene asset |
| `advice.ready_for_tools`, `blocking_reasons` | Readiness and why a command might wait |
| `advice.recommended_retry_after_ms` | Suggested delay |
| `observed_at_unix_ms`, `staleness.is_stale` | Observation age, not proof of a new change |

Do not read legacy top-level `is_compiling`/project-root fields from this resource. A fresh timestamp need not increase `sequence`. A failed or stale snapshot is not permission to mutate.

## Project And Target Inspection

| URI/template | Use |
|---|---|
| `mcpforunity://instances` | Available Editors and IDs; select only the intended project |
| `mcpforunity://project/info` | `projectRoot`, `assetsPath`, Unity version, platform, rendering/input configuration and package/API flags |
| `mcpforunity://project/tags` | Defined tags before assigning a custom tag |
| `mcpforunity://project/layers` | Existing layer names/indices |
| `mcpforunity://editor/selection` | Current selection, not an implicit authorization target |
| `mcpforunity://scene/gameobject-api` | GameObject resource usage |
| `mcpforunity://scene/gameobject/{instance_id}` | Object identity, hierarchy path and transform metadata |
| `mcpforunity://scene/gameobject/{instance_id}/components?include_properties=false&page_size=25&cursor=0` | Component inventory without expensive property details |
| `mcpforunity://scene/gameobject/{instance_id}/component/{component_name}` | One component's properties |

Use actual returned IDs in templates. For repeated component types, inspect inventory order to choose the zero-based `component_index`; it is not the component's instance ID. Detailed getters may fail independently or return version-dependent property names.

Component list payloads use `components`, `nextCursor`, `hasMore`, `totalCount` and `includeProperties`. Follow that resource's cursor fields; do not assume every tool uses `next_cursor`.

## Conditional Inspection

Read these only for the corresponding task:

- `mcpforunity://scene/cameras`: Unity/Cinemachine camera state and availability.
- `mcpforunity://scene/volumes`: Volume/profile/effect summaries.
- `mcpforunity://rendering/stats`: Rendering counters; unavailable counters are not zero-cost proof.
- `mcpforunity://pipeline/renderer-features`: Active renderer data and feature slots.
- `mcpforunity://editor/prefab-stage`: Current prefab-editing context.
- `mcpforunity://prefab-api`: Prefab resource usage.
- `mcpforunity://prefab/{encoded_path}` and `mcpforunity://prefab/{encoded_path}/hierarchy`: Prefab asset and hierarchy. Encode the complete asset path, for example `mcpforunity://prefab/Assets%2FPrefabs%2FPlayer.prefab`.
- `mcpforunity://tests` or `mcpforunity://tests/{mode}`: Test discovery, which may require waiting. Select `EditMode` or `PlayMode` as appropriate.
- `mcpforunity://custom-tools`: Project custom-tool schema where supported.
- `mcpforunity://tool-groups`: Tool-group resource inventory where exposed.
- `mcpforunity://menu-items`: Discover an exact menu path before invoking it.
- `mcpforunity://editor/windows`, `mcpforunity://editor/active-tool`: Editor UI state.

Script locators such as `mcpforunity://path/Assets/Scripts/Player.cs` are inputs to script tools, not a promise of a registered generic file-read resource.

## Paging And Budgets

Start with small pages and metadata-only responses; fetch details only for the relevant items. Search/hierarchy tools and component resources use different envelopes and cursor casing. Continue only when the returned continuation field indicates more data, and stop if the cursor fails to advance. A truncated payload is not a complete project inventory.
