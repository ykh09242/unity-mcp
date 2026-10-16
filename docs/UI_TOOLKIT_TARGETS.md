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

Copy the chosen `nodes[].id` from default `capture_ui_snapshot` with
`framework="uitoolkit"` and no `identity_map` (schema version 2) into `element_id`, keeping the same target GameObject. Descendant ID segments reuse the
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

## Read a bounded visual tree

`get_visual_tree` reads the selected component's current content-container hierarchy,
including duplicate names. It does not require a live panel, force layout, or apply the
active/enabled mutation-selector requirements. Use an instance ID to avoid ambiguous
GameObject names. Target selectors are limited to 4096 characters.

```json
{
  "action": "get_visual_tree",
  "target": "123",
  "max_depth": 10,
  "max_nodes": 200,
  "max_evaluations": 5000,
  "max_characters": 65536
}
```

The example shows defaults. `max_depth` accepts 0 through 24, inclusive: the root is
depth 0. `max_nodes` accepts 1 through 1000, `max_evaluations` 1 through 20000, and
`max_characters` 1 through 262144. Omitted/null budgets use defaults; zero depth means
root only. Invalid budgets refuse before target lookup. Depths above 24, previously
unchecked, now refuse explicitly.

Each node admission, child fetch, and class-enumerator probe consumes one evaluation;
the terminating class probe counts too. Traversal does not precollect siblings or
count skipped descendants. These counters bound this serializer's work, not elapsed
time, shared scene/component lookup, or arbitrary Unity property getters.

The character budget counts copied UTF-16 code units across node strings and envelope
metadata, including GameObject/source-asset names and emitted color strings. Individual
strings are limited to 8192 units and each node to 64 classes. Prefixes preserve valid
surrogate pairs. A color is emitted whole or omitted; it is never clipped into an
invalid hex value. Fixed keys, protocol labels and reason strings are constant overhead.

`data.traversal` reports effective budgets, `returned_nodes`, `evaluations`, `characters`,
`complete`, `truncated`, `tree_status`, `reasons`, and `metadata_truncated_fields`.
Known reasons are `max_depth`, `max_nodes`, `max_evaluations`, `max_characters`,
`per_string_limit`, and `class_limit`. Node `truncated_fields` identifies clipped or
incomplete fields. Node `truncated=true` specifically means omitted children;
`childCount` is the immediate child count and any returned `children` array is its
ordered prefix. No recursive omitted-node total is claimed. Depth pruning continues
later siblings; global node/work/character exhaustion stops new traversal. Local
string/class caps can still permit other fields and later nodes.

A ready root can be a partial stub if metadata used the character budget. An
uninitialized root instead returns `tree=null`, `tree_status="not_initialized"`, zero
node/evaluation counters and `complete=false`; unavailable data alone is not budget
truncation. `complete=true` means a ready tree with no reported omissions. Truncated
names/metadata are observations, not complete identity strings. Existing positional
selectors and snapshot semantic IDs keep their separate contracts described above.


## Explicit identities for repeated snapshot rows

`capture_ui_snapshot` accepts an optional per-capture `identity_map` for uGUI and
UI Toolkit. It associates current child paths with caller-supplied, nonsecret item IDs:

```json
{
  "framework": "uitoolkit",
  "target": "123",
  "identity_map": [
    {"element_path": "/0", "id": "item-A"},
    {"element_path": "/1", "id": "item-B"}
  ]
}
```

Replace123 with the current target's actual ID/path. A mapped segment uses `@id:`;
map IDs use1-128 ASCII letters, digits, period, underscore, colon or hyphen and are
case-sensitive. Paths use current snapshot/content-container child order for Toolkit
and Transform child order for uGUI. They have1-127 canonical indices0-999, at most512
characters; empty/root paths are unsupported. At most999 assignments, no more than
`max_nodes - 1`, and65536 total path/ID characters are allowed. Duplicate paths or
IDs, unused paths and remaining ambiguity fail the complete capture. Explicit null
or empty maps refuse; omission preserves semantic capture and ambiguity refusal.

Two same-name rows with distinct assigned IDs are valid. One assigned row can coexist
with one same-name unmapped row because their effective segments differ. Two unmapped
duplicate siblings still refuse even when another sibling is assigned.

The caller must update associations when rows reorder or rebind. If B moves to index0,
submit `/0 -> item-B`. Recreating the same item under the same effective parent can
keep its ID. A valid stale map cannot be detected automatically. IDs remain scoped to
the semantic root and effective ancestor path; reparenting/root changes can invalidate
them. Only realized rows are captured; logical offscreen items are not enumerated.
Sibling order, geometry and state changes remain real diffs even when item IDs match.
Do not use input contents, credentials or personal data as IDs. Input text privacy is
unchanged; the map does not read native input values, userData or viewDataKey.

Mapped captures use schema version3 with `identity_method="explicit_path_map_v1"`,
retaining version2 typed/private control state. Compare v3 with v3; mixed versions
refuse. IDs containing mapped segments are comparison identities and are unsupported
by `manage_ui.element_id`; they do not become mutation/object handles. The root or a
wholly unmapped semantic branch may still resolve through the unchanged semantic
selector. Use a default semantic capture for editing IDs, or the independent current
`element_path` mutation selector.
