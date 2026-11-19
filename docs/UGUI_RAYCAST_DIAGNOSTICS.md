# Current uGUI raycast evidence

`inspect_ugui_hit_order(target, screen_position)` reads the current native order at bottom-left screen pixel coordinates. The target identifies the scene RectTransform whose handler relationship to examine. Blockers elsewhere in the scene participate in the global query. The tool selects the strict read policy of `manage_ugui(action="diagnose_raycast")`. Geometric candidates from layout diagnostics remain separate evidence.

## Custom filter policy

allow_custom_filters defaults to false. A potentially applicable custom ICanvasRaycastFilter causes unknown before its callback is invoked. This includes disabled custom filters that the native Graphic may call. With true, native raycasting may evaluate those callbacks. Unknown custom BaseRaycaster or Graphic implementations still cause unknown before the query, even with the opt-in.

The receipt separates policy, custom-filter presence, native-query attempt and user_code_may_have_run. callback_invocations is always not_instrumented: an inventory does not establish how many callbacks ran or whether every filter was reached. A throwing callback returns success=false with constant failure evidence and retains those flags. The package does not retry or publish exception text.

The package does not explicitly dispatch pointer/selection events, process or activate an input module, change selection, edit sources, or force Canvas/layout/mesh/render updates. Opted-in user code can do any of those things itself, mutate the scene, throw, or never return. No rollback, atomic snapshot, interruptibility or hard deadline is promised. A completed hit list is the native query's returned order, not proof that the scene stayed unchanged during it.

## Evidence and limitations

Each returned hit retains its native ordinal, object/raycaster/root-raycaster IDs, current event camera ID, sorting-layer ID/value/order, depth, distance, index, display and screen position. The first-hit handler relationship is separate from current input-module facts. Click-handler eligibility is inspected as metadata using the installed ExecuteEvents rules (active hierarchy, interface presence and enabled Behaviour), without invoking event interfaces or the native unbounded handler walk. Each lookup inspects at most 64 ancestors and charges ancestor/component/member inspection against the remaining max_evaluations budget. A found handler stops the lookup; overrideSorting does not. When either lookup cannot be admitted, first_hit_targets_handler remains null while admitted native hit evidence is retained. Null also covers absent handlers; it never means a proven mismatch. This private handler ceiling does not change max_depth, which still bounds filter ancestry. A hit can exist without an input module. A module or matching first hit does not guarantee visible/audible content, OS input or event receipt.

Compatibility is checked against the installed Unity-built or official registry uGUI assembly and its concrete APIs. The query requires the typed EventSystem raycast method, pointer position, native hit fields and initialized raycaster/graphic registries. The graphic inventory uses the official canvas query that returns raycastable graphics, or the all-graphics canvas query on older API shapes. Only an existing package collection may be inspected. Missing hit fields, including display information, leave the order unknown.

The query checks every registered raycaster and each potentially participating graphic and filter. It accepts exact supported built-in implementations and TMP components whose raycast implementation and target getter remain native. The strict route also checks that native graphics have existing renderers and that image alpha sampling cannot invoke logging callbacks. A missing capability, uninitialized registry or unsafe participant returns an unknown receipt. A confirmed empty order requires `status=observed`, `policy.native_query_attempted=true` and `hits_total=0`.

Display 0 requires a consistent canvas/camera route with no camera RenderTexture or physics-blocking raycaster. Missing or ambiguous EventSystems and secondary or ambiguous displays also return unknown.

max_nodes bounds initial registered Graphic entries across participating raycasters (default200, range1..1000). max_depth bounds filter ancestry (default10, range0..24). max_evaluations bounds precharged package inspection/result work (default20000, range1..200000); it is not a count of operations inside native or user code. max_characters reserves UTF16 metadata space before native evaluation (default65536, range1..262144). Internal caps are 32 registered raycasters, 128 returned hits and 64 ancestors per handler lookup. Names/type metadata are limited to256UTF16 units. Unknown/failure receipts contain no hit claims. A complete native query can return a bounded prefix with explicit tail truncation; an omitted target rank is not invented.

Native enumeration and metadata APIs can allocate before a returned count is inspected. The accepted initial inventory and output are bounded; those caps do not make native API allocation, opted-in callbacks or a query whose callbacks alter registries hard-bounded. Receipts may become unknown after a query if metadata, shapes or package budgets cannot be safely published. They retain query-attempt/user-code flags.

## Verification

The public dispatcher resolves strict ID, name and path selectors within the loaded scene or prefab stage and charges that work to the shared query budget. The server validates the native response with `validate_ugui_raycast_response`, including policy and attempt flags on failed queries.

The optional Unity Edit Mode fixture compares the public result with an owned `EventSystem.RaycastAll` oracle. It covers hit order, unsupported components, user filter admission, missing EventSystem or registry, work limits, and loaded scene ID resolution without loading assets. Its exports require a fresh sibling `exports` directory identified by `UNITY_MCP_UI09_EXPORT_ROOT`. Fixture setup and native canvas warm-up happen before the observations.

The complete native fixture passed on Unity 6000.0.63f1 and 6000.0.69f1 with each Editor's own built-in uGUI package. The older all-graphics registry accessor was checked separately against a typed fixture. Other Editor and package combinations are admitted by the runtime capability checks.
