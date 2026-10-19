# Compound uGUI controls

`manage_ugui(action="create_control")` creates one owned ScrollView, Toggle, Slider or TMP input graph. Ordinary `create` and `create_cli` retain their existing contracts. The new operation requires `edit` independently from each scoped requester and authenticated plugin; annotations and catalogs do not grant permission.

```json
{
  "action": "create_control",
  "element_type": "toggle",
  "parent": 12345,
  "name": "MusicToggle",
  "properties": {"label": "Music", "isOn": true, "sizeDelta": [160, 24]},
  "include_inactive": false
}
```

The parent must resolve uniquely to a nonpersistent RectTransform under a Canvas in the active loaded normal scene. Prefab Stage, preview scenes and another scene's parent are refused. The operation never changes the active scene. A parent is a nonzero signed Int32 identity or a bounded nonempty selector string. Names are single hierarchy names, at most 256 UTF-8 bytes. Text fields are valid Unicode and at most 1024 UTF-8 bytes. A native transport that supplies a non-string token, including a preconverted Date token, is refused rather than rewriting literal text.

Inputs are closed per kind; surplus fields and invalid types are refused before readiness or transport in the public tool and before Undo/allocation natively. The decoded request is at most 16 KiB. Shared properties are `sizeDelta` (exactly two finite Unity-float values, each 1..8192), `anchoredPosition` (two finite Unity-float values) and `eventSystem`. Root anchors and pivot are centered, local rotation is identity, local scale is one, and the parent layer is copied. No existing layout, CanvasScaler or parent settings are changed. Colors use simple Image quads; no sprites are imported.

| Kind | Properties and defaults | Owned graph |
| --- | --- | --- |
| `scroll_view` | `spacing=0`, finite 0..128; vertical only | 200x200 Image/ScrollRect root; masked Viewport; top-stretched Content with VerticalLayoutGroup and ContentSizeFitter. Exact viewport/content references, Clamped movement, vertical PreferredSize, horizontal Unconstrained, child width controlled and height unforced. No scrollbars. |
| `toggle` | `label="Toggle"`, `isOn=false` | 160x24 Toggle; Background Image; nested Checkmark Image; TMP Label. Exact targetGraphic/graphic references; label does not receive raycasts. |
| `slider` | `minValue=0`, `maxValue=1`, `value=0`, `wholeNumbers=false`, `direction="LeftToRight"` | 160x24 Slider; Background; Fill Area/Fill; Handle Slide Area/Handle. Exact fillRect/handleRect/targetGraphic. Direction accepts the four exact Unity Slider direction names. Bounds and value must be integral with wholeNumbers, with minValue strictly below maxValue and finite span. No silent clamp. |
| `tmp_input` | `text=""`, `placeholder="Enter text..."`, `characterLimit=256` | 160x36 Image/TMP_InputField; clipped Text Area; Text and Placeholder TMP children. Exact viewport/text/placeholder/font references, Standard content and SingleLine mode. Limit is a strict integer 0..1024; zero leaves the byte bound, otherwise initial text must fit its UTF-16 code-unit limit. |

An empty ScrollView has no scrolling range until populated. Production code does not force rendering or layout, focus controls, process input events or promise post-layout pixels or physical keyboard/IME/mobile readiness.

## Dependencies and text

uGUI is optional and reflected exact type/member signatures are checked before allocation. Toggle labels and TMP inputs additionally require TMP and its default font. Only a static reachable default/global fallback/weight font graph is admitted: at most 16 distinct font references and 64 nonnull reference edges. Traversal also admits at most 64 entries in each fallback list and ten weight pairs per font, so a large list of null entries cannot bypass the work bound. Dynamic and DynamicOS fonts are refused. Fonts, atlases, settings, sprite assets and source files are never created, imported or saved by this operation.

Every owned TMP text and placeholder has `richText=false` and `emojiFallbackSupport=false`; input rich text is also disabled. Markup-looking text is literal. The exact public readable/writable bool emoji member must exist during preflight. Its setter and getter execute only on a newly owned inactive text component, before assigning font/text or activating it. An exception or readback mismatch there is a postallocation failure and uses rollback. Separate global emoji font/sprite fallback assets are neither traversed nor changed because that route is disabled. No arbitrary user component is probed.

## EventSystem option

`eventSystem="preserve"` is the default. It creates or configures no EventSystem/module and changes no current selection. It reports `preserved`, without asserting that input routing is ready.

`create_legacy_if_missing` explicitly requires the compiled legacy input backend and exact EventSystem/StandaloneInputModule types. Zero existing loaded-scene EventSystems, including inactive objects, creates one separate owned legacy system in the same scene. One is reused unchanged, even if inactive or unconfigured. More than one is refused before allocation. No InputSystemUIInputModule, action assets or project input settings are created or changed.

## Ownership, Undo and failures

This action first snapshots native Transform identity in all loaded scenes, before Undo/allocation. Admission allows at most 32 loaded scenes and 16,384 GameObjects. A larger scene set is refused; it is not partially scanned then mutated. Actual pre-existing object references, original parents/scenes and sibling indices are captured, rather than relying on recyclable instance IDs. This snapshot reads native scene/Transform state, without querying user component properties.

At most seven GameObjects are tool-authored, including an optional EventSystem. TMP may create additional submeshes and a PlayMode caret below the owned root. The seven-object limit does not describe the complete subtree. The returned closed readback is at most 8 KiB and contains only known role IDs, actual component-reference IDs, configured rect/variant values and EventSystem outcome.

The new action has its own Undo transaction; existing actions' transaction is unchanged. Normal construction failure rolls back the whole new graph, optional new EventSystem and captured generated descendants, then reports failure. It retains the original build error and distinct flush, rollback, detach, residue-cleanup and finalization errors. Diagnostics contain bounded phase/type names and never echo the request. Any cleanup/finalization error means `rollback_status="unknown"`, even when no new objects remain. A finalization failure may leave the newly built graph; callers must inspect the scene before deciding what to do next, and must not assume a safe retry.

If an existing callback reparents a pre-existing object under a new root, rollback first detaches/protects that exact reference and attempts its original parent/scene/sibling restoration. Such intervention always reports unknown. A root still containing a protected object is not recursively destroyed. Existing callbacks can have arbitrary side effects, including mutations after these guards run; this is not a sandbox or a guarantee of complete global rollback. Lost pre-existing references and cleanup errors are reported, never presented as complete restoration. No MarkSceneClean, metadata rollback or automatic retry is used.

## Verification scope

Python fixtures cover closed per-kind admission before I/O, literal transport, compatibility with basic create, scoped edit policy, and both installed SDK modes. Native fixtures cover graph and component-reference readback, layout settings, handler events with an independent raycast positive control, single Undo/Redo, mid-build rollback, pre-existing objects and EventSystems, unknown cleanup errors, callback reparenting, and TMP fallback submeshes.

The Explicit actual-tool lifecycle fixtures call `create_control` in EditMode, persist the owned scene and static font/settings, use normal domain and scene reload, reacquire roles through a bounded GUID manifest, and assert real native caret creation and destruction of the captured references. The engine-only test-project witness observes the graph without constructing or repairing it.

A separate Explicit standard-TMP ownership fixture enters PlayMode before acquiring its transient resources, asserts native caret allocation beneath the owned input, verifies destruction of the captured caret references, and cleans up before returning to EditMode. This supplemental probe does not carry a `create_control` graph through normal reload. EditMode graph results alone do not establish caret behavior. These fixtures do not establish Player, physical keyboard, IME, or mobile readiness; execution results and supported-version checks are recorded separately.

## API references

See the official [uGUI ScrollRect documentation](https://docs.unity3d.com/Packages/com.unity.ugui@2.0/manual/script-ScrollRect.html), [Slider documentation](https://docs.unity3d.com/Packages/com.unity.ugui@2.0/manual/script-Slider.html), [Toggle documentation](https://docs.unity3d.com/Packages/com.unity.ugui@2.0/manual/script-Toggle.html), and [TMP API](https://docs.unity3d.com/Packages/com.unity.textmeshpro@3.0/api/TMPro.TMP_InputField.html).
