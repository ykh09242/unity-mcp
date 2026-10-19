"""
Defines the manage_ui tool for creating and managing Unity UI Toolkit elements.

Supports creating UXML documents and USS stylesheets, attaching UIDocument or
PanelRenderer components to GameObjects, and inspecting visual trees.
"""

import base64
import os
from typing import Annotated, Any, Literal

from fastmcp import Context
from mcp.types import ToolAnnotations
from pydantic import Field

from models.ui_layout_diagnostics import validate_ui_layout_diagnostics

from services.registry import mcp_for_unity_tool
from services.tools import get_unity_instance_from_context
from services.tools.refresh_unity import send_mutation
from services.tools.rendering_limits import render_dimensions_error, screenshot_limits_error
from services.tools.pagination import validate_page
from transport.unity_transport import send_with_unity_instance
from transport.legacy.unity_connection import async_send_command_with_retry


_VALID_EXTENSIONS = {".uxml", ".uss"}


def _canonical_ui_path(path: str) -> str:
    normalized = path.replace("\\", "/")
    parts = normalized.split("/")
    if not parts or parts[0].lower() != "assets":
        raise ValueError("path must be under 'Assets/'.")
    if any(part in ("", ".", "..") for part in parts) or any(
        c in normalized for c in ':*?"<>|\x00\r\n'
    ):
        raise ValueError("path must not contain rooted or traversal sequences.")
    if os.path.splitext(normalized)[1].lower() not in _VALID_EXTENSIONS:
        raise ValueError("Invalid file extension. Must be .uxml or .uss.")
    return "Assets/" + "/".join(parts[1:])


@mcp_for_unity_tool(
    group="ui",
    description=(
        "Manages Unity UI Toolkit elements (UXML, USS, UIDocument and Unity 6.5+ PanelRenderer components). "
        "Read-only actions: ping, read, get_visual_tree, diagnose, list.  "
        "Modifying actions: create, update, delete, attach_ui_document, detach_ui_document, create_panel_settings, update_panel_settings, modify_visual_element.\n"
        "diagnose observes bounded current-panel physical geometry/clipping/overflow/sibling-overlap candidates without text or layout updates. "
        "Unknown/partial results are not a clean-layout assertion; diagnostic paths are not mutation/snapshot selectors.\n"
        "Visual actions: render_ui (captures UI panel to a PNG screenshot for self-evaluation).\n"
        "Structural actions: link_stylesheet (adds a Style src reference to a UXML file).\n\n"
        "UI Toolkit workflow:\n"
        "1. Use list to discover existing UI assets\n"
        "2. Create a UXML file (structure, like HTML)\n"
        "3. Create a USS file (styling, like CSS)\n"
        "4. Link stylesheet to UXML via link_stylesheet\n"
        "5. Attach a UI component with the UXML source via attach_ui_document and component_type\n"
        "   - auto (default) preserves existing UIDocument; new objects prefer PanelRenderer when available.\n"
        "   - ui_document explicitly selects UIDocument; panel_renderer requires Unity 6.5+ API support.\n"
        "6. Use get_visual_tree to inspect the result\n"
        "7. Use modify_visual_element to change text, classes, or inline styles on live elements. "
        "Legacy element_name selects the first match; strict_name=true requires uniqueness. "
        "Use element_path for current child indices or element_id from a snapshot for explicit selection. "
        "Explicit selectors never fall back to a name.\n"
        "8. Use render_ui to capture a visual preview for self-evaluation\n"
        "   - In play mode: first call queues a WaitForEndOfFrame screen capture and returns pending=true;\n"
        "     resume with capture_id from that response to retrieve the PNG. Cancel with that ID and cancel_capture=true.\n"
        "     ID-less calls always start separate requests, including identical settings; another live owner is rejected.\n"
        "   - In editor mode: immediate panel readback is best-effort; warmup_updates=1..4 opts into bounded deferred readback using capture_id.\n"
        "     Results report capture source, readiness and full-image alpha occupancy; opaque pixels do not prove UI presence.\n"
        "9. Use detach_ui_document to remove the selected UI component from a GameObject\n"
        "10. Use delete to remove .uxml/.uss files\n\n"
        "Important: Always use <ui:Style> (with the ui: namespace prefix) in UXML, not bare <Style>. "
        "UI Builder will fail to open files that use <Style> without the prefix."
    ),
    annotations=ToolAnnotations(
        title="Manage UI",
        destructiveHint=True,
    ),
)
async def manage_ui(
    ctx: Context,
    action: Annotated[
        Literal[
            "ping",
            "create",
            "read",
            "update",
            "delete",
            "attach_ui_document",
            "detach_ui_document",
            "create_panel_settings",
            "update_panel_settings",
            "get_visual_tree",
            "diagnose",
            "render_ui",
            "link_stylesheet",
            "list",
            "modify_visual_element",
        ],
        "Action to perform.",
    ],
    # File operations (create/read/update/link_stylesheet)
    path: Annotated[
        str,
        "Assets-relative path (e.g., 'Assets/UI/MainMenu.uxml' or 'Assets/UI/Styles.uss'). "
        "For render_ui: optional UXML path to render directly without a scene GameObject.",
    ]
    | None = None,
    contents: Annotated[
        str, "File content (UXML or USS markup). Plain text - encoding handled automatically."
    ]
    | None = None,
    # attach_ui_document / get_visual_tree / render_ui
    target: Annotated[
        str, "Target GameObject name or path for attach_ui_document / get_visual_tree / render_ui."
    ]
    | None = None,
    component_type: Annotated[
        Literal["auto", "ui_document", "panel_renderer"],
        "UI component for attach/detach, get_visual_tree, modify_visual_element and render_ui. "
        "Default auto preserves an existing UIDocument, otherwise resolves PanelRenderer; "
        "new attachments prefer PanelRenderer when available and fall back to UIDocument. "
        "panel_renderer requires Unity 6.5+ API support.",
    ]
    | None = None,
    source_asset: Annotated[str, "Path to UXML VisualTreeAsset (e.g., 'Assets/UI/MainMenu.uxml')."]
    | None = None,
    panel_settings: Annotated[str, "Path to PanelSettings asset. Auto-creates default if omitted."]
    | None = None,
    sort_order: Annotated[int, "UI component sort order (default 0)."] | None = None,
    # create_panel_settings
    scale_mode: Annotated[
        Literal[
            "ConstantPixelSize",
            "ConstantPhysicalSize",
            "ScaleWithScreenSize",
        ],
        "Panel scale mode. Legacy shorthand; prefer using 'settings' dict.",
    ]
    | None = None,
    reference_resolution: Annotated[
        dict[str, int],
        "Reference resolution as {width, height}. Legacy shorthand; prefer using 'settings' dict.",
    ]
    | None = None,
    settings: Annotated[
        dict[str, Any],
        "Generic PanelSettings properties dict for create_panel_settings. "
        "Keys: scaleMode (ConstantPixelSize|ConstantPhysicalSize|ScaleWithScreenSize), "
        "referenceResolution ({width,height}), screenMatchMode (MatchWidthOrHeight|ShrinkToFit|ExpandToFill), "
        "match (0-1 float), referenceDpi, fallbackDpi, sortingOrder, targetDisplay, "
        "clearColor (bool), colorClearValue (#RRGGBB or {r,g,b,a}), clearDepthStencil, "
        "themeStyleSheet (asset path), dynamicAtlasSettings ({minAtlasSize,maxAtlasSize,maxSubTextureSize,activeFilters}).",
    ]
    | None = None,
    # get_visual_tree
    max_depth: Annotated[
        int | None,
        Field(
            ge=0,
            le=24,
            strict=True,
            description="get_visual_tree / diagnose inclusive depth: root is 0; default 10, maximum 24.",
        ),
    ] = None,
    max_nodes: Annotated[
        int | None,
        Field(
            ge=1,
            le=1000,
            strict=True,
            description="get_visual_tree / diagnose emitted node budget, including root (default 200).",
        ),
    ] = None,
    max_evaluations: Annotated[
        int | None,
        Field(
            ge=1,
            le=20000,
            strict=True,
            description="get_visual_tree operation budget: node admissions, child fetches and class probes; diagnose charges ancestors, admissions, child fetches, fixed style probes and pairs (default 5000). Not a time or scene-lookup bound.",
        ),
    ] = None,
    max_characters: Annotated[
        int | None,
        Field(
            ge=1,
            le=262144,
            strict=True,
            description="get_visual_tree / diagnose shared copied UTF-16 string budget including metadata (default 65536). get_visual_tree per-string limit 8192/class limit 64; diagnose name/type limit 256 and whole physical paths.",
        ),
    ] = None,
    max_pairs: Annotated[
        int | None,
        Field(
            strict=True,
            ge=0,
            le=20000,
            description="diagnose physical sibling pairs examined before filters (default 2000); zero disables overlap and yields partial evidence.",
        ),
    ] = None,
    max_findings: Annotated[
        int | None,
        Field(
            strict=True,
            ge=1,
            le=2000,
            description="diagnose retained candidate findings (default 200); reaching the cap reports partial coverage.",
        ),
    ] = None,
    # render_ui
    capture_id: Annotated[
        str,
        "Deferred capture ID returned by render_ui. Supply this ID alone to poll the original "
        "request; optional target/output settings must match the start request. Omit to start "
        "a separate capture even with identical settings. Results expire after 30 seconds.",
    ]
    | None = None,
    cancel_capture: Annotated[
        bool, "Cancel the deferred capture owned by capture_id (default false). For render_ui."
    ]
    | None = None,
    warmup_updates: Annotated[
        int,
        Field(
            strict=True,
            ge=0,
            le=4,
            description="Edit-mode panel readback delay in Editor updates, 0 to 4 (default 0). "
            "Positive values return capture_id for polling/cancellation; no repaint guarantee or automatic retries. "
            "Play mode requires 0 and already captures at end of frame. For render_ui.",
        ),
    ]
    | None = None,
    width: Annotated[
        int,
        "Render width in pixels, 1 to 8192 (default 1920). "
        "The frame may contain at most 33,554,432 pixels. For render_ui.",
    ]
    | None = None,
    height: Annotated[int, "Render height in pixels, 1 to 8192 (default 1080). For render_ui."]
    | None = None,
    include_image: Annotated[
        bool, "Return inline base64 PNG in the response (default false). For render_ui."
    ]
    | None = None,
    max_resolution: Annotated[
        int,
        "Max resolution for inline base64 image, 1 to 8192 "
        "(default 640; 0 selects the default). For render_ui.",
    ]
    | None = None,
    screenshot_file_name: Annotated[
        str, "Custom file name for the render output (default: auto-generated). For render_ui."
    ]
    | None = None,
    output_folder: Annotated[
        str,
        "Optional folder for the render output. Project-relative (e.g. 'Assets/Screenshots' or "
        "'Captures') or absolute path inside the project. Overrides the user's Editor preference. "
        "If omitted, falls back to the Editor preference, then to the built-in default "
        "(Assets/Screenshots). For render_ui.",
    ]
    | None = None,
    # link_stylesheet
    stylesheet: Annotated[
        str, "Path to USS stylesheet to link (e.g., 'Assets/UI/Styles.uss'). For link_stylesheet."
    ]
    | None = None,
    # list
    filter_type: Annotated[
        str, "Filter UI assets by type: 'uxml', 'uss', 'PanelSettings', or omit for all. For list."
    ]
    | None = None,
    page_size: Annotated[int, "Number of results per page: 1-1000 (default 50). For list."]
    | None = None,
    page_number: Annotated[int, "Page number, 1-based (default 1). For list."] | None = None,
    # modify_visual_element
    element_name: Annotated[
        str,
        "Visual element name. Legacy first match unless strict_name=true. "
        "Optional exact name guard with element_path or element_id. For modify_visual_element.",
    ]
    | None = None,
    element_path: Annotated[
        str,
        "Current snapshot/content-container child-index path, e.g. '/0/1'; empty string selects root. "
        "Zero-based decimal indices, no leading zeros. No persistent identity or replacement detection. "
        "Mutually exclusive with element_id; no name fallback. Omit when unused; explicit null is invalid.",
    ] = None,
    element_id: Annotated[
        str,
        "Exact current UI Toolkit snapshot node ID. Uses the snapshot's auto-selected UI component; "
        "use element_path for another component_type. Semantic path, not a persistent handle; "
        "same-path replacement is not detected. No name fallback. Omit when unused; null/empty invalid.",
    ] = None,
    strict_name: Annotated[
        bool,
        "Require exactly one element_name match over the UQuery root and physical control internals. "
        "Default false keeps legacy first match. Active initialized UI required; bounded scan may refuse. "
        "Explicit selectors take precedence; element_name then guards the selected name.",
    ]
    | None = None,
    text: Annotated[str, "New text content for Label/Button elements. For modify_visual_element."]
    | None = None,
    add_classes: Annotated[
        list[str], "USS class names to add to the element. For modify_visual_element."
    ]
    | None = None,
    remove_classes: Annotated[
        list[str], "USS class names to remove from the element. For modify_visual_element."
    ]
    | None = None,
    toggle_classes: Annotated[
        list[str], "USS class names to toggle on the element. For modify_visual_element."
    ]
    | None = None,
    style: Annotated[
        dict[str, Any],
        "Inline styles for modify_visual_element. Legacy scalar values remain supported. "
        "Typed lengths: {'value': 50, 'unit': '%'} or {'keyword': 'auto'} where supported. "
        "Inspect data.style_results for every applied/rejected entry and final inline overrides.",
    ]
    | None = None,
    enabled: Annotated[bool, "Set element enabled/disabled state. For modify_visual_element."]
    | None = None,
    visible: Annotated[
        bool, "Set element visibility (display: flex/none). For modify_visual_element."
    ]
    | None = None,
    tooltip: Annotated[str, "Set element tooltip text. For modify_visual_element."] | None = None,
    strict_style: Annotated[
        bool,
        Field(
            strict=True,
            description="Reject every requested mutation before application if any inline style is rejected. "
            "Only modify_visual_element; default false preserves lenient compatibility. Validation is not callback rollback.",
        ),
    ] = False,
) -> dict[str, Any]:
    action_lower = action.lower()
    if type(strict_style) is not bool:
        return {"success": False, "message": "strict_style must be a Boolean."}
    if strict_style and action_lower != "modify_visual_element":
        return {"success": False, "message": "strict_style requires modify_visual_element."}

    if warmup_updates is not None:
        if type(warmup_updates) is not int or not 0 <= warmup_updates <= 4:
            return {"success": False, "message": "warmup_updates must be an integer from 0 to 4."}
        if action_lower != "render_ui":
            return {"success": False, "message": "warmup_updates requires render_ui."}

    if action_lower in ("get_visual_tree", "diagnose"):
        for key, value, minimum, maximum in (
            ("max_depth", max_depth, 0, 24),
            ("max_nodes", max_nodes, 1, 1000),
            ("max_evaluations", max_evaluations, 1, 20000),
            ("max_characters", max_characters, 1, 262144),
        ):
            if value is not None and (type(value) is not int or not minimum <= value <= maximum):
                return {
                    "success": False,
                    "message": f"{key} must be an integer from {minimum} to {maximum}.",
                }
        if action_lower == "get_visual_tree" and target is not None and len(target) > 4096:
            return {
                "success": False,
                "message": "get_visual_tree target must be at most 4096 characters.",
            }

    if action_lower == "diagnose":
        for key, value, low, high in (
            ("max_pairs", max_pairs, 0, 20000),
            ("max_findings", max_findings, 1, 2000),
        ):
            if value is not None and (type(value) is not int or not low <= value <= high):
                return {
                    "success": False,
                    "message": f"{key} must be an integer from {low} to {high}.",
                }
        if (
            type(target) is not str
            or not target.strip()
            or len(target.encode("utf-16-le", errors="surrogatepass")) // 2 > 4096
        ):
            return {"success": False, "message": "invalid_diagnostics_target"}
        unsupported = (
            path,
            contents,
            source_asset,
            panel_settings,
            sort_order,
            scale_mode,
            reference_resolution,
            settings,
            capture_id,
            cancel_capture,
            width,
            height,
            include_image,
            max_resolution,
            screenshot_file_name,
            output_folder,
            stylesheet,
            filter_type,
            page_size,
            page_number,
            element_path,
            element_id,
            strict_name,
            element_name,
            text,
            add_classes,
            remove_classes,
            toggle_classes,
            style,
            enabled,
            visible,
            tooltip,
        )
        if any(value is not None for value in unsupported):
            return {"success": False, "message": "unsupported_diagnostics_parameter"}

    if component_type is not None and component_type not in (
        "auto",
        "ui_document",
        "panel_renderer",
    ):
        return {
            "success": False,
            "message": "component_type must be auto, ui_document, or panel_renderer.",
        }

    if action_lower == "list":
        try:
            page_size, page_number = validate_page(page_size, page_number)
        except ValueError as exc:
            return {"success": False, "message": str(exc)}

    if action_lower == "render_ui":
        render_error = render_dimensions_error(
            1920 if width is None else width,
            1080 if height is None else height,
        ) or screenshot_limits_error(1, 640 if max_resolution in (None, 0) else max_resolution)
        if render_error:
            return {"success": False, "message": render_error}

    # --- Path validation for file operations ---
    try:
        if (
            action_lower in ("create", "read", "update", "delete", "link_stylesheet", "render_ui")
            and path
        ):
            path = _canonical_ui_path(path)
        if action_lower == "link_stylesheet" and stylesheet:
            stylesheet = _canonical_ui_path(stylesheet)
    except ValueError as exc:
        return {"success": False, "message": str(exc)}

    # --- Build params dict ---
    params_dict: dict[str, Any] = {
        "action": action_lower,
    }

    # File operations: base64-encode contents for transport
    if action_lower in ("create", "update") and contents is not None:
        params_dict["encodedContents"] = base64.b64encode(contents.encode("utf-8")).decode("utf-8")
        params_dict["contentsEncoded"] = True
    elif action_lower in ("create", "update") and contents is None:
        # Let Unity-side validate and return the error
        pass

    if path is not None:
        params_dict["path"] = path
    if target is not None:
        params_dict["target"] = target
    if component_type is not None:
        params_dict["componentType"] = component_type
    if source_asset is not None:
        params_dict["sourceAsset"] = source_asset
    if panel_settings is not None:
        params_dict["panelSettings"] = panel_settings
    if sort_order is not None:
        params_dict["sortOrder"] = sort_order
    if scale_mode is not None:
        params_dict["scaleMode"] = scale_mode
    if reference_resolution is not None:
        params_dict["referenceResolution"] = reference_resolution
    if settings is not None:
        params_dict["settings"] = settings
    if max_depth is not None:
        params_dict["maxDepth"] = max_depth
    if max_nodes is not None:
        params_dict["maxNodes"] = max_nodes
    if max_evaluations is not None:
        params_dict["maxEvaluations"] = max_evaluations
    if max_characters is not None:
        params_dict["maxCharacters"] = max_characters

    if action_lower == "diagnose":
        if max_pairs is not None:
            params_dict["maxPairs"] = max_pairs
        if max_findings is not None:
            params_dict["maxFindings"] = max_findings

    # render_ui params
    if warmup_updates is not None:
        params_dict["warmup_updates"] = warmup_updates
    if capture_id is not None:
        params_dict["capture_id"] = capture_id
    if cancel_capture is not None:
        params_dict["cancel_capture"] = cancel_capture
    if width is not None:
        params_dict["width"] = width
    if height is not None:
        params_dict["height"] = height
    if include_image is not None:
        params_dict["include_image"] = include_image
    if max_resolution is not None:
        params_dict["max_resolution"] = max_resolution
    if screenshot_file_name is not None:
        params_dict["file_name"] = screenshot_file_name
    if output_folder is not None and output_folder.strip():
        params_dict["output_folder"] = output_folder.strip()

    # link_stylesheet params
    if stylesheet is not None:
        params_dict["stylesheet"] = stylesheet

    # list params
    if filter_type is not None:
        params_dict["filterType"] = filter_type
    if page_size is not None:
        params_dict["pageSize"] = page_size
    if page_number is not None:
        params_dict["pageNumber"] = page_number

    # modify_visual_element params
    if element_path is not None:
        params_dict["elementPath"] = element_path
    if element_id is not None:
        params_dict["elementId"] = element_id
    if strict_name is not None:
        params_dict["strictName"] = strict_name
    if element_name is not None:
        params_dict["elementName"] = element_name
    if text is not None:
        params_dict["text"] = text
    if add_classes is not None:
        params_dict["addClasses"] = add_classes
    if remove_classes is not None:
        params_dict["removeClasses"] = remove_classes
    if toggle_classes is not None:
        params_dict["toggleClasses"] = toggle_classes
    if style is not None:
        params_dict["style"] = style
    if strict_style:
        params_dict["strict_style"] = True
    if enabled is not None:
        params_dict["enabled"] = enabled
    if visible is not None:
        params_dict["visible"] = str(visible).lower()
    if tooltip is not None:
        params_dict["tooltip"] = tooltip

    # --- Route to Unity ---
    unity_instance = await get_unity_instance_from_context(ctx)
    is_mutation = action_lower in (
        "create",
        "update",
        "delete",
        "attach_ui_document",
        "detach_ui_document",
        "create_panel_settings",
        "update_panel_settings",
        "render_ui",
        "link_stylesheet",
        "modify_visual_element",
    )

    if is_mutation:
        result = await send_mutation(
            ctx,
            unity_instance,
            "manage_ui",
            params_dict,
        )
    else:
        result = await send_with_unity_instance(
            async_send_command_with_retry,
            unity_instance,
            "manage_ui",
            params_dict,
        )

    if action_lower == "diagnose":
        try:
            if type(result) is not dict or result.get("success") is not True:
                code = result.get("error") if type(result) is dict else None
                known = {
                    "unsupported_ui_component",
                    "unsupported_diagnostics_parameter",
                    "invalid_diagnostics_target",
                    "invalid_or_ambiguous_diagnostics_target",
                    "diagnostics_artifact_limit",
                }
                return {
                    "success": False,
                    "message": code
                    if type(code) is str and code in known
                    else "diagnostics_unavailable",
                }
            data = validate_ui_layout_diagnostics(result.get("data"))
            for key, value, fallback in (
                ("max_depth", max_depth, 10),
                ("max_nodes", max_nodes, 200),
                ("max_evaluations", max_evaluations, 5000),
                ("max_characters", max_characters, 65536),
                ("max_pairs", max_pairs, 2000),
                ("max_findings", max_findings, 200),
            ):
                if data["traversal"][key] != (fallback if value is None else value):
                    raise ValueError("ignored diagnostic budget")
            if component_type not in (None, "auto") and data["component_type"] != component_type:
                raise ValueError("different diagnostic component")
        except ValueError:
            return {"success": False, "message": "unsupported_diagnostics_response"}
        # Do not publish arbitrary old-peer envelope extras/messages or rejected source details.
        return {"success": True, "data": data}

    if isinstance(result, dict):
        # Decode base64 contents in read responses
        if action_lower == "read" and result.get("success"):
            data = result.get("data", {})
            if data.get("contentsEncoded") and data.get("encodedContents") is not None:
                try:
                    decoded = base64.b64decode(data["encodedContents"]).decode("utf-8")
                    data["contents"] = decoded
                    del data["encodedContents"]
                    del data["contentsEncoded"]
                except Exception:
                    pass
        return result

    return {"success": False, "message": str(result)}
