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


## Diagnose current-panel layout candidates

`manage_ui(action="diagnose", target="123")` observes the selected UI component's
current flat panel. It reports separate display suppression, resolved visibility,
effective opacity, rectangular clipping, outside-parent bounds and sibling overlap
candidates. It does not force layout, read text/input values, resize the Game View,
change styles, or save source assets. Use the current instance ID, unique name or
unique hierarchy path; ambiguous GameObject targets refuse.

```json
{
  "action": "diagnose",
  "target": "123",
  "component_type": "auto",
  "max_depth": 10,
  "max_nodes": 200,
  "max_evaluations": 5000,
  "max_characters": 65536,
  "max_pairs": 2000,
  "max_findings": 200
}
```

The example shows defaults. Depth accepts0..24, nodes1..1000, evaluations1..20000,
characters1..262144, pairs0..20000 and findings1..2000. Omitted/null budgets use
defaults; other supplied values must be integers. Each physical ancestor inspection,
node admission, child fetch, fixed style probe and pair considered consumes an
operation. Pairs are charged before visibility/geometry filters. `max_pairs=0`
disables overlap and reports partial evidence. Names/types have a local256 UTF-16
unit limit; variable metadata and whole paths share the character budget. Limits
bound producer work and retention, not scene lookup or engine getter time.

`data.kind="uitoolkit_layout_diagnostics"` and `schema_version=1` identify this
closed response. `status` is `complete`, `partial` or `unavailable`; traversal
counters/cutoff reasons and independent clipping/overlap stages describe coverage.
Missing roots, detached panels, inactive components and unavailable layout are
explicit unavailable observations. An attached `display:none` or empty element is
reported separately. Unknown APIs, non-flat panel space, unsupported transforms
or rounded clipping shapes remain uncertain. An empty finding list under partial
or unavailable status does not establish a clean layout. Even complete geometry
is neither a pixel-visibility nor an event-delivery test.

Diagnostics use the **physical hierarchy**, including ScrollView viewport internals.
Their `diagnostic_path` values are current physical index positions, not
content-container `element_path` selectors, snapshot IDs, UI-05 identity assignments
or persistent handles. Do not send them to mutation actions. This action operates
on the component root; element selectors, identity maps, resolutions, file/render
and mutation options refuse rather than silently change the diagnostic scope.

Clipping uses checked engine-computed overflow and inherited rectangular constraints;
unset inline styles do not imply visible overflow. Overlap covers direct physical
siblings only, excludes ancestor/descendant pairs and returns candidates without
claiming front/back order or a raycast blocker. Outside-parent bounds may be intended
margins/absolute positioning. Text glyph overflow, curved/stencil/custom-render shapes,
cross-branch/document overlap and multi-resolution simulation are outside this action.
Ordinary names can still contain application-supplied information; the tool does not
identify arbitrary secrets copied into names by scripts.

Only exact engine-owned UIDocument/PanelRenderer types and validated engine members
can provide a diagnostic root. Project-defined PanelRenderer lookalikes refuse before
getters or receiver registration. The verified built-in PanelRenderer's temporary
root receiver is the sole callback exception and is unregistered synchronously.
No source/settings getter is used. Optional or changed engine clip APIs are reported
as unknown, and old peers returning unrelated success data are refused by the server.
Existing UI group/tool permissions and the mixed read/write tool's annotations remain
unchanged; diagnosis uses the read transport. Snapshot versions2/3, identities,
privacy/state extraction and existing mutation/read actions retain their contracts.

## Inline style values and validation

`modify_visual_element` changes the live UI Toolkit element. It does not persist UXML or USS. The action remains an edit: `strict_style` validates values and does not grant permission or make callbacks transactional.

Legacy scalar numbers, canonical numeric strings, HTML color strings and enum strings remain accepted. Numeric `null` retains the existing zero default. A malformed or nonfinite numeric value rejects the whole request before text, classes, styles, enabled state, top-level visibility or tooltip changes. A scalar string such as `"50%"`, `"12px"` or `"auto"` is not a numeric scalar; use an explicit typed object.

| Properties (existing camelCase and hyphen aliases) | Additional typed form |
| --- | --- |
| `width`, `height`, four margins | `{"value": 50, "unit": "%"}`, `{"value": 12, "unit": "px"}` or `{"keyword": "auto"}` |
| Four paddings, `borderRadius` | Numeric `value` with `unit` equal to `px` or `%` |
| `fontSize` | Numeric `value` with `unit` equal to `px` |
| `opacity`, `flexGrow`, `flexShrink` | `{"value": 0.5}` |
| `color`, `backgroundColor` | `{"value": "#FF0000"}` (the existing HTML color parser) |
| `display`, `visibility` | `{"value": "none"}` / `{"value": "hidden"}`; only defined enum values |

Object keys are exact; extra keys and mixed keyword/value forms are rejected. This is a bounded value contract, not a general CSS parser. No resource/font lookup, new properties, `initial`/`inherit` or reset keyword is added. Existing finite scalar range behavior is preserved.

Omit `strict_style` or pass `false` for compatible lenient handling: valid entries apply in request order; invalid colors/enums remain omitted; unsupported properties retain their existing `[skipped]` string. Unsupported typed units or property/keyword combinations reject that entry. Legacy undefined numeric enum values remain admitted only in this default scalar mode and are labelled `legacy_undefined_enum`.

Pass the Boolean `strict_style: true` to reject every requested mutation if any style entry is rejected. The option is supported only by `modify_visual_element`; non-Boolean values, including null, are invalid before dispatch. It is separate from `strict_name`. Malformed numerics remain fatal in both modes. Limits of 64 style entries, 128 UTF-16 characters per key and 256 characters per parsed scalar text are checked before mutation; a limit refusal does not publish an unbounded entry report.

When a style is supplied or strict validation is requested, `data.style_results` has `schema_version: 1`, complete ordered `applied` and `rejected` arrays, and an optional request-level reason. Entries contain the original `index`/`key`, canonical `property` (null for unsupported properties), and a fixed `reason`; rejected arbitrary values are not echoed. Applied entries add safe normalized `assigned_value` and `final_inline_value`. `superseded_by` identifies a later alias or top-level `visible`. For example, style `display: "None"` is an assignment, but a later `visible: true` leaves the inline display `Flex`. On validation abort, applied is empty and otherwise valid entries have `request_aborted_before_mutation`.

These are **inline** values after assignments, not computed/resolved styles, rendered visibility or layout results. `borderRadius` assigns all four corners and reports their shared assigned value with separate final inline readbacks for all four corners. Existing `modifications`/`skipped` fields and application order remain available. Strict preflight does not roll back exceptions raised by application callbacks after validation.

The [Unity 2021.3 USS type documentation](https://docs.unity3d.com/2021.3/Documentation/Manual/UIE-USS-PropertyTypes.html) and [supported properties](https://docs.unity3d.com/2021.3/Documentation/Manual/UIE-USS-SupportedProperties.html) describe the unit/keyword families. The [2021.3 IStyle API](https://docs.unity3d.com/2021.3/Documentation/ScriptReference/UIElements.IStyle.html) distinguishes inline from computed values. This source proposal does not certify minimum-version compilation or native behavior; exact 2021.3 API/type and runtime verification remain separate.
