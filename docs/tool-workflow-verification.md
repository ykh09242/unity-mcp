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

Imported-asset reports use the published asset GUID and retain the original job and provider-task IDs. A replaced or missing published asset produces unavailable evidence. Model checks inspect up to 32 hierarchy nodes and 64 components, with separate material and bone limits. Addressables checks retain the edited entry/profile revision and report incomplete coverage when traversal or serialized-field limits are reached.

## Jobs and result summaries

`wait_for_operation` observes build, package, scenario, recording, Project Auditor and test jobs against their captured owner and generation. It reports job outcome and cleanup/finalization state. A successful status query can observe a failed job.

Build, package, recording, auditor and test start/cancel wrappers support `wait_timeout` from 0 through 60 seconds. Zero preserves immediate return. Selected waits attach `data.completion` to the original receipt. Terminal scenario and recording-draft results include bounded summaries of existing failures, step counts, saved draft identity and review warnings.

Profiler `window_result` accepts up to eight explicit `thresholds`. Each threshold supplies `marker`, `statistic` (`mean`, `median`, `p95` or `max`), the exact native `unit`, and `maximum`. Checks use the already returned sampling window and require matching identity, available metrics and completed recorder cleanup.

Console tail results include checkpoint, page and retained-row interval coverage. Native history can change in ways the available cursor anchors cannot distinguish. `loss_unknown=true` preserves that uncertainty, including when all currently retained rows have been scanned.

## Discovery and retained responses

`manage_tools` can return a schema when explicitly activating one tool with `include_schema=true`. Readiness hints describe supported actions and carry their observation state. `set_active_instance(include_context=true)` adds a bounded observation from the selected instance.

Python clients can use `utils.retained_response.assemble_response` for bounded page assembly, SHA verification and optional field selection. Supply the existing `read_response` caller and retained handle. Page, byte and time limits remain explicit.
