# Tool workflow verification

Editing tools return bounded observations of the affected source, object or asset in the same response. Longer checks retain their original run or job identity for continuation.

## Reading an edit result

The existing mutation receipt records saved, applied, partial or uncertain work. `data.verification` records the checks that followed it. Keep both when deciding what to do next.

| Field | Meaning |
| --- | --- |
| `status` | `passed`, `issues_found`, `pending`, `incomplete`, `unavailable` or `stale` |
| `scope` | The objects, fields or source covered by the observation |
| `complete` | Whether the stated checks have complete evidence |
| `observed_values` | Bounded native readback or saved-source evidence |
| `findings` | Observed problems requiring attention |
| `further_fixes_required` | `true` for findings, `false` for a completed successful check, or `null` when undetermined |

Reports can identify issues while coverage remains incomplete. Truncation fields and per-domain limits describe omitted results. A saved edit followed by a compiler error retains its saved receipt and returns the diagnostic. A timeout retains the original handle for observation. Use each report's continuation fields to retrieve more evidence.

## Source editing

Script creation, text edits, structured edits and deletion use `verification="auto"` by default. Confirmed writes feed the existing verification service after the mutation lease has ended. The service uses configured changed-file analysis and compilation evidence. The initial response can contain findings or a pending run.

Use `verification="off"` when collecting several edits for one final check. `script_change_set` and `batch_execute` preserve their default apply-only behavior. Select the change set's verification option or `batch_execute(verification="scripts")` to verify the final confirmed source selection once. Partial batches keep each mutation outcome and exclude unconfirmed writes from the final scope.

`manage_verification(action="run")` accepts `paths` or `change_set_id` directly in `options`, with an optional `profile_id`. It prepares the selection internally. A requested pre-edit comparison still uses `prepare` with `capture_baseline` before editing. Returned source hashes describe raw saved bytes, including encoding markers and line endings.

```json
{
  "action": "run",
  "options": {
    "paths": ["Assets/Scripts/Player.cs"],
    "profile_id": "script-check",
    "wait_seconds": 2
  }
}
```

## Native authoring

| Area | Composed result |
| --- | --- |
| Components, ScriptableObjects and GameObjects | Exact affected component identities, supported retained properties, partial patch receipts and bounded broken-reference findings |
| Prefabs | Saved requested fields, selected override disposition, remaining property-override count and saved-stage state |
| Scenes | `get_loaded_scenes` returns each loaded scene's `scene_handle`. Select a save source with the handle, exact `scene_path`, or unique `scene_name`. With no selector, save uses the active scene. Supply `path` or `name` for Save As. `verify=true` observes the saved or loaded scene in one validation slice. |
| Assets | Affected-path metadata; `await_graph_seconds` optionally advances an existing graph receipt and attaches its first diff |
| Addressables | `include_preflight=true` adds scoped entry, group-profile or selected profile-variable findings to a metadata edit |
| Localization | Retained table/key identity, string and smart-format checks, or saved locale fallback diagnostics |
| Generated and imported assets | Completed image, audio and model jobs include actual published-path metadata and bounded material, mesh and rig findings |
| Blender | `verify_import=true` on `import_model` observes the published asset and any placed object |
| Asset bundles | `include_dependencies=true` combines contents with the first dependency page |
| UI Toolkit and uGUI | Same-target property, layout or projection evidence; source writes accept an explicit `verify_target` document |
| Rendering, cameras, materials and VFX | Retained fields, material ownership/binding and bounded static findings |
| Physics, animation and ProBuilder | Selected body/joint/material state, animation binding/revision/key evidence and bounded mesh health |
| Input Actions | Ordered document edits with local references, one final validation and one import |
| Navigation | Add with validated properties under one Undo operation and final component readback |

UI layout and generated text can remain pending until Unity processes its normal update. Current geometry observations state that timing limitation. Unsupported serialized fields, ambiguous mappings and exhausted budgets retain incomplete evidence.

`find_gameobjects(projection=[...])` optionally projects `name`, `active`, `transform`, `components` and `handle` for the same returned IDs. The projection has separate coverage limits and retains search pagination.

Use `find_gameobjects(scene_scope="exact", scene_path="Assets/Levels/East.unity", search_method="by_path", search_term="/Doors/Main", projection=["handle"])` to isolate a saved additive scene when other loaded scenes have the same hierarchy names. `resultScene` identifies the exact path and session-local native scene handle for that page. An unloaded scene returns `scene_not_loaded`. Multiple loaded instances of the same path return `scene_ambiguous`. Require one unique result before editing. Send its current instance ID to `gameobject_edit` with `search_method="by_id"` and check the edit's returned `instanceID` and `scenePath`. `manage_object_handles(action="resolve", handle=...)` reports the original scene and object while the handle is valid, and `stale_handle` after the scene is unloaded. Rediscover explicitly for a different scene.

Imported-asset reports use the published asset GUID and retain the original job and provider-task IDs. A replaced or missing published asset produces unavailable evidence. Model checks inspect up to 32 hierarchy nodes and 64 components, with separate material and bone limits. Addressables checks retain the edited entry/profile revision and report incomplete coverage when traversal or serialized-field limits are reached.

## Jobs and result summaries

`wait_for_operation` observes build, package, scenario, recording, Project Auditor and test jobs against their captured owner and generation. It reports job outcome and cleanup/finalization state. A successful status query can observe a failed job.

Build, package, recording, auditor and test start/cancel wrappers support `wait_timeout` from 0 through 60 seconds. Zero preserves immediate return. Selected waits attach `data.completion` to the original receipt. Terminal scenario and recording-draft results include bounded summaries of existing failures, step counts, saved draft identity and review warnings.

Profiler `window_result` accepts up to eight explicit `thresholds`. Each threshold supplies `marker`, `statistic` (`mean`, `median`, `p95` or `max`), the exact native `unit`, and `maximum`. Checks use the already returned sampling window and require matching identity, available metrics and completed recorder cleanup.

Console tail results include checkpoint, page and retained-row interval coverage. Native history can change in ways the available cursor anchors cannot distinguish. `loss_unknown=true` preserves that uncertainty, including when all currently retained rows have been scanned.

## Reporting executed verification

Report the actual target, setup, interaction route and observed scope. Identify test-owned fixtures, substituted services and settings changed for the check. Attribute native framework counts to the final runner result and give custom runtime probes their own assertion counts. Record direct method invocation, UI pointer dispatch, raw input and complete user flows according to the route actually exercised.

For asynchronous work, observe the original job through completion and verify cleanup after its consumers finish. Report restored state, retained requested results, remaining test-owned objects and pending checks. A final report should make any unobserved user-flow, visual or audio outcome explicit.

Prefab serialized previews include `scope`, `complete`, `whole_prefab_complete` and before/after observation counts. `preview_scope="components"` covers the components named by a component-property edit on one target. Its `complete` field applies to that selection. Snapshot-limit failures return `prefab_preview_limit_exceeded` with the observed bound and recovery guidance. Preserve that scope when reporting preview evidence.

## Discovery and retained responses

`manage_tools` can return a schema when explicitly activating one tool with `include_schema=true`. Readiness hints describe supported actions and carry their observation state. `set_active_instance(include_context=true)` adds a bounded observation from the selected instance.

To hand an observation to another agent, copy `data.handoff` from that selection. Pass
`evidence_needed` to record up to eight caller-supplied checks of up to 256 characters each.
This argument also requests context. For example, a pending check might be "Capture the
Game View after dismissing the dialog." These checks travel with the copied handoff.

The handoff identifies the selected full instance, connection generation, active scene
(path, GUID, name, loaded scene handle and lifetime epoch when available), Play/Edit mode and Editor
observation time in Unix milliseconds.
`unity_version` is included when the same native snapshot supplies it.
`freshness=point_in_time` refers to that timestamp. Saved scene contents require separate
evidence. A scene with no saved path or GUID can carry a name alone. An unavailable read
preserves the selected instance and pending checks and leaves the observation unknown.
On a new stdio connection, `context.reason=selected_editor_identity_unavailable` can
mean the connection has yet to authenticate. Read `mcpforunity://editor/state` on the
selected instance, then request the handoff again.

On receipt, call `set_active_instance` with the full `instance` and `include_context=true`.
Sessionless clients receive the same handoff with `data.selection_persisted=false`.
Pass the full `unity_instance` on subsequent tools and `_meta.unity_instance` on resource
reads. Carry forward outstanding `evidence_needed` entries explicitly. Compare the
generation, mode, scene GUID/path, loaded scene handle and lifetime epoch. If any changed,
the mode is transitioning, identity is ambiguous, or the observation time is missing,
collect the affected evidence again. A scene name alone cannot distinguish unsaved scenes.
Use a current Game View or runtime observation for Play-mode claims and a saved-asset
read for claims about persisted content.

A Python receiving client can perform the comparison and forward the checks in one
selection. Pass the trusted parent's packet and use a separate connected client:

```python
from utils.editor_handoff import receive_editor_handoff

received = await receive_editor_handoff(child.call_tool, parent_packet)
confirmed = received["confirmed"]
pending_checks = received["evidence_needed"]
```

`confirmed` names only the identity fields observed in both snapshots. An unchanged
saved scene, stable mode and authenticated connection let the child proceed to the
pending checks without another project-info or scene lookup for these fields. Scene
contents, object IDs, readiness and saved state require their own evidence. To check
an explicitly reassigned destination, pass its full `instance=` selector. A changed
target, generation, mode or scene handle yields an empty `confirmed` with the new observation
in `handoff`. The helper also leaves `confirmed` empty for missing identity, an
unchanged or regressing timestamp, or a child sample outside the receiving call's time
window or more than two seconds old. Clock skew can therefore require a new check.
Unity can reuse a scene handle after unloading and reopening a scene. The lifetime
epoch follows the Editor's object-handle invalidation events, including scene opens,
unloads, Edit/Play transitions and Undo/Redo. A changed epoch clears the confirmed
fields even when the saved scene and handle match.
An older Editor may omit the loaded scene handle or epoch. When the scene asset identity still
matches, `comparison="same_scene_asset"` confirms `scene_asset` (GUID, path and name),
mode and editor identity. The receiving client must observe any loaded scene instance
or object it needs. A recognized SDK permission or target refusal returns
`comparison="observation_unavailable"`, empty observations and the pending checks.
Unclassified errors and cancellation propagate to the caller.

From `Server/`, run `python -m pytest -s tests/test_editor_handoff_clients.py` to
exercise separate parent and receiving MCP clients against controlled Editor peers.
The comparison client reads the project-info and editor-state resources independently.
The test counts native reads and UTF-8 application payload bytes for unchanged context,
a changed scene on the same connection, a replacement connection and another Editor.
The parent packet size is reported separately from the receiver's response bytes.

Python clients can use `utils.retained_response.assemble_response` for bounded page assembly, SHA verification and optional field selection. Supply the existing `read_response` caller and retained handle. Page, byte and time limits remain explicit.
