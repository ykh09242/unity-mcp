# CLI uGUI creation safety

The four existing CLI commands create a Canvas, TextMeshPro text, button with its existing label, or Image through one native manage_ugui create_cli transaction. This closed native transport action is for these CLI shapes; it is not a generic batch API or an extra Python MCP action. Ordinary manage_ugui create retains its current defaults and response.

Parent, required uGUI/TMP types, the configured TMP default font, render mode, position and requested exact sprite are checked before root allocation. Text --position X Y means anchored coordinates and defaults to zero; nonfinite/Unity-float-overflow coordinates refuse. Names use the common single-hierarchy-segment validation. Duplicate literal names remain allowed, and all construction continues by returned instance ID.

Working legacy component defaults and world-preserving parenting are retained through the existing GameObject construction path. Canvas render modes and Image sprite selectors are preserved. Button creates the existing NAME_Label child; no EventSystem, package import or additional control type is created. Image sprite selection reuses the exact UI15 selector; an omitted sprite remains valid.

JSON output retains the initial GameObject response, including data.instanceID and its initial transform/component snapshot. It intentionally is not a final UI hierarchy dump. Existing human success lines remain. Success is printed only after the whole native operation completes. Explicit native failure and transport failure exit nonzero; a lost response never triggers another create or a compensation delete.

One Undo removes the package-owned root and label, and Redo restores their registered state. Screen-space Canvas-driven geometry settles through Unity's normal render lifecycle; create, Undo and Redo success responses are not render-completion fences. A readback reports the current state and can precede that lifecycle transition. Managed construction failures revert the registered changes. This does not promise to undo arbitrary user callbacks, external effects, crashes or a transport failure after native commit.

