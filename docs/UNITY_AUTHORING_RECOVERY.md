# Prefab authoring and Editor lifecycle recovery

## Inspect prefab changes before saving

Use `prefab_contents` with `action="modify_contents"` and `preview_only=true` to
apply a proposed edit to isolated contents, compare serialized fields, and unload
without calling `SaveAsPrefabAsset`. For example:

```json
{
  "action": "modify_contents",
  "prefab_path": "Assets/UI/Panel.prefab",
  "preview_only": true,
  "component_properties": {
    "RectTransform": {"m_AnchoredPosition": {"x": 12, "y": 34}}
  }
}
```

`data.saved` is false. `data.preview.changes` compares the imported source asset
with the modified contents. It includes observed load/edit side effects, such as
layout-driven positions, even when only a component reference was requested.
Paths use `$` for the root, child sibling indices, component type/occurrence and
the exact serialized property path. Internal references use those stable paths;
external asset references use GUID and local file ID, not load-local instance IDs.

Inspect `complete`, `truncated`, `unsupported_before` and `unsupported_after`.
Unsupported property types and oversized strings are explicitly excluded from
coverage. Managed references are not traversed. Previews bound objects, property
visits, retained snapshot bytes and output size. Native loading and serialized
accessors remain synchronous Unity operations. Very small float changes are
reported without a rounding tolerance.

This preview does not issue an automatic refresh, generate an apply token, or
predict save-time callbacks. Custom lifecycle callbacks may have external side
effects even without an MCP save. A later edit reloads the contents and may differ.
Review the final asset diff after saving. To save the same request, omit
`preview_only` or set it to false. The existing `preview_modify_contents` /
`apply_preview` pair remains the separate, restricted root-transform workflow
with a one-attempt, source/dependency-checked token.

Serialized Vector2 fields such as `m_AnchorMin` and `m_AnchoredPosition` accept an
object containing exactly finite numeric `x` and `y` values. Child `.x` / `.y`
writes continue to work. Invalid vectors are rejected without saving the prefab.

## Recover from a connection change

The Editor process, project identity and connection session have different
lifetimes. Assembly reloads and reconnects can replace a connection while the
same Editor and server remain open. Operations retain a generation fence so an
admitted edit cannot silently continue against a replacement connection.

For `operation_target_changed`, read `mcpforunity://instances`, confirm the
intended project, and select its current instance. `data.dispatch=not_dispatched`
means the tool was refused before its callback started. Once the callback has
started, earlier steps may have executed: `dispatch=unknown` and
`hint=inspect_state_before_retry` require checking the target before retrying.
The server does not automatically replay the operation.

## Wait for Play Mode exit before reading Console

A pending stop response acknowledges the transition, not its completion. Read
`mcpforunity://editor/state` until `data.editor.play_mode.is_changing` is false
and `data.advice.ready_for_tools` is true. Then read Console. Pre-dispatch Console
readiness failures include `dispatch=not_dispatched` and
`next_action=wait_for_editor_ready`; their deadline is not restarted. An unanswered
readiness probe alone cannot identify the cause of a busy Editor.

Likewise, `refresh_unity.success=true` acknowledges the refresh/compile request.
Check `data.compilation.outcome`, readiness, and native coordination separately.
`failed` or `unknown` is not compiler success; `source_revision_confirmed` is a
separate source-to-assembly attribution result.

## Recursive runtime data during hot reload

Unity hot reload serializes eligible private fields and auto-property backing
fields even without `[SerializeField]`. A runtime scratch list that points into
a recursive data model should use `[System.NonSerialized]` when it does not need
to survive reload. Do not change persisted data to `[SerializeReference]` merely
to hide a warning from a transient cache. See the
[Unity 6000.0 serialization documentation](https://docs.unity3d.com/6000.0/Documentation/Manual/script-serialization-how-unity-uses.html).

The separate "Objects are trying to be loaded during a domain backup" message
does not identify its caller when Unity supplies no stack. Check the reload log
and serialization callbacks before attributing it to MCP or to background focus.
