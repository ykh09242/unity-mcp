# UI Toolkit mutation targets

`manage_ui(action="modify_visual_element", ...)` changes one element within the selected
GameObject's UI component. Existing callers can keep `element_name`: omitting
`strict_name`, or setting it to false, preserves Unity UQuery's first match.
Use the current GameObject instance ID as `target` when GameObject names are duplicated.
Instance IDs are live Editor identities, not durable handles across object replacement.

## Require an unambiguous name

```json
{
  "action": "modify_visual_element",
  "target": "123",
  "element_name": "statusLabel",
  "strict_name": true,
  "text": "Ready"
}
```

Replace `123` with the current target's actual instance ID. Strict name resolution scans
the same physical tree as `Q`, including the root and internal controls. It refuses if
the name is ambiguous or the bounded scan cannot establish uniqueness. It does not
silently select a first match after refusal.

## Select by current child positions

```json
{
  "action": "modify_visual_element",
  "target": "123",
  "element_path": "/1/0",
  "element_name": "statusLabel",
  "text": "Ready"
}
```

The path is relative to the selected UI root: `/1/0` means child 1, then child 0.
It uses the same **content-container child order** as UI Toolkit snapshots and
`get_visual_tree`, not the physical indices of internal control implementation elements.
Indices are zero-based ASCII decimal without signs or leading zeros. `/` separates
indices; names are not part of the path, so no name escaping applies.

An empty string (`"element_path": ""`) intentionally selects the root. For example,
use it with `tooltip` and omit `element_name` unless a root name guard is intended.
Omitted paths remain omitted; the MCP SDK rejects explicit null paths before dispatch.

Paths describe the current tree. They cannot detect renames, reordering, or replacement:
a new element at the same position becomes that path's target. An optional
`element_name` is an exact guard on the selected element; a mismatch refuses.

## Reuse a snapshot semantic ID

Copy the chosen `nodes[].id` from `capture_ui_snapshot` with `framework="uitoolkit"`
into `element_id`, keeping the same target GameObject. Descendant ID segments reuse the
snapshot's URI-escaped `name:` or fallback `type:` encoding. Copy the ID exactly; do not
construct a CSS selector or decode/re-escape its segments.

Semantic IDs require the canonical UI component selected by `component_type="auto"`,
which is also what snapshot capture uses. If another component is explicitly selected,
use `element_path` instead. Snapshot IDs have no component discriminator, so this
restriction avoids interpreting a document ID against a different component's root.

An ID refuses when its current semantic path is missing, foreign or ambiguous. Descendant
renames/reparenting and GameObject identity changes can invalidate it. The root ID
encodes the GameObject snapshot identity, **not the root VisualElement name**; renaming
only that root VisualElement does not invalidate its ID. These are semantic paths,
not persistent element handles, and cannot detect replacement with the same semantic
path. Duplicate unnamed same-type siblings or duplicate named siblings can make an ID
ambiguous; a positional path can still distinguish their current positions.

## Refusal and bounds

Supply at most one of `element_path` and `element_id`. With an explicit selector,
`element_name` is only a guard and `strict_name` does not perform a second name search.
Invalid, missing or ambiguous explicit selectors never fall back to a name. The SDK
rejects null/non-string explicit selectors before dispatch; malformed path strings and
conflicting selectors reach Unity, where rejection occurs before mutation.

Strict and explicit requests require an active GameObject, enabled UI component and
initialized root. They do not require a rendered panel or completed layout merely to
change an already built tree. Selection is bounded to 4096 selector characters,
128 levels including the root, 1000 siblings and 10000 examined nodes. Explicit
selection also verifies containment within 128 physical ancestors of the chosen root.
A refusal applies no requested text, classes, styles, enabled state or tooltip changes.

The legacy name-only path retains its existing behavior. UI capture request ownership,
polling and cancellation are described separately in [UI_CAPTURE.md](UI_CAPTURE.md).
