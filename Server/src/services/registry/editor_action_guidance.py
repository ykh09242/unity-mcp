"""Reviewed purposes for asset, camera and UI Toolkit dispatchers and their focused adapters."""

from typing import Final

from services.registry.action_guidance_types import ActionGuidance
from services.registry.capability_catalog import (
    CALLBACK_INSPECTION_EFFECTS,
    MUTATING_EFFECTS,
    READ_ONLY_EFFECTS,
)


def _read(purpose: str, *required: str, limit: str = "") -> ActionGuidance:
    return ActionGuidance(purpose, required, READ_ONLY_EFFECTS, limit, "observe")


def _inspect(purpose: str, *required: str, limit: str) -> ActionGuidance:
    """The request asks for evidence but the implementation can execute other code."""
    return ActionGuidance(purpose, required, CALLBACK_INSPECTION_EFFECTS, limit, "observe")


def _edit(purpose: str, *required: str, limit: str = "") -> ActionGuidance:
    return ActionGuidance(purpose, required, MUTATING_EFFECTS, limit, "edit")


def _interact(purpose: str, *required: str, limit: str = "") -> ActionGuidance:
    return ActionGuidance(purpose, required, MUTATING_EFFECTS, limit, "interact")


_PREFLIGHT_IMPORT: Final = "When permitted, preflight may import pending external changes, including scripts that trigger compilation."
_LABEL_WRITE: Final = (
    "Supply the observed label_revision for path or a revision per label_targets row. "
    "Batch writes run sequentially and can partially apply."
)
_REFERENCE_LIMIT: Final = (
    "Coverage follows AssetDatabase dependencies within the scan scope and budgets. "
    "String-based or custom-loader references can be missing."
)

_ASSET: Final = {
    "search": _inspect(
        "Find imported assets by name or type in a folder. metadata_only=true returns paged metadata "
        "without explicit object or preview loads. The default loads result assets.",
        limit=_PREFLIGHT_IMPORT,
    ),
    "get_info": _inspect(
        "Load the main asset at path to read its GUID, type, instance ID and file metadata. "
        "generate_preview can request a thumbnail.",
        "path",
        limit=_PREFLIGHT_IMPORT,
    ),
    "get_components": _inspect(
        "Load a GameObject asset, such as a prefab, and list its root component slots, types and missing scripts.",
        "path",
        limit=_PREFLIGHT_IMPORT,
    ),
    "import": _edit(
        "Force Unity to reimport the existing asset at path. Omit importer properties. Import callbacks can run.",
        "path",
    ),
    "create": _edit(
        "Create a Folder, Material or PhysicsMaterial at the requested project path.",
        "path",
        "asset_type",
    ),
    "create_folder": _edit("Create the requested folder in the AssetDatabase.", "path"),
    "modify": _edit(
        "Apply properties to the asset at path, including supported material, ScriptableObject or prefab component fields.",
        "path",
        "properties",
    ),
    "duplicate": _edit(
        "Copy the asset to destination, or generate a unique path when omitted.", "path"
    ),
    "rename": _edit(
        "Rename or relocate an asset using its full destination path through the AssetDatabase.",
        "path",
        "destination",
    ),
    "move": _edit(
        "Move path to destination through the AssetDatabase. Supply reference_revision from preview_move to recheck the same scan options, require complete scan coverage and reject reference drift before mutation; omitting it performs an unguarded move.",
        "path",
        "destination",
    ),
    "delete": _edit(
        "Delete the asset at path through the AssetDatabase. Supply reference_revision from preview_delete to recheck the same scan options, require complete scan coverage and reject reference drift before mutation; omitting it performs an unguarded delete.",
        "path",
    ),
    "get_dependencies": _read(
        "Find assets referenced by the selected asset in the imported dependency database.",
        "path",
        limit=_REFERENCE_LIMIT,
    ),
    "get_reverse_references": _read(
        "Find project assets that refer to the selected asset, including usages of a prefab.",
        "path",
        limit=_REFERENCE_LIMIT,
    ),
    "preview_move": _read(
        "Observe bounded reference impact of moving path to destination without moving it. Return referenceRevision for an optional move with reference_revision; inspect scan coverage before relying on the result.",
        "path",
        "destination",
        limit=_REFERENCE_LIMIT,
    ),
    "preview_delete": _read(
        "Observe bounded references and deletion impact for path without deleting it. Return referenceRevision for an optional delete with reference_revision; inspect scan coverage before relying on the result.",
        "path",
        limit=_REFERENCE_LIMIT,
    ),
    "get_labels": _inspect(
        "Read exact AssetDatabase labels and a revision for path or each label_targets row.",
        limit="Reads load each main asset, which may run asset callbacks. This action neither refreshes the project nor writes labels.",
    ),
    "set_labels": _edit(
        "Replace the asset's labels with the supplied set. An empty array clears all labels. "
        + _LABEL_WRITE,
        "labels",
    ),
    "add_labels": _edit(
        "Add the supplied labels while retaining existing labels. " + _LABEL_WRITE, "labels"
    ),
    "remove_labels": _edit(
        "Remove the supplied labels while retaining other labels. " + _LABEL_WRITE, "labels"
    ),
    "list_asset_bundles": _read(
        "Page through registered bundle assignment names in the AssetDatabase without building bundles."
    ),
    "get_bundle_assets": _read(
        "List asset paths assigned to bundle_name in the AssetDatabase. include_dependencies adds a dependency page.",
        "bundle_name",
    ),
    "get_bundle_dependencies": _read(
        "List direct or recursive dependencies of a registered bundle assignment. Built bundle files are outside this query.",
        "bundle_name",
    ),
}

_CAMERA: Final = {
    "ping": _read(
        "Check whether the selected Editor can resolve Cinemachine and report its version when available."
    ),
    "list_cameras": _inspect(
        "List Unity cameras in loaded scenes, their brain attachments, and any Cinemachine cameras, targets, stages and current live state.",
        limit="With Cinemachine installed, the inventory reads provider methods and component getters. It does not attach a brain.",
    ),
    "get_brain_status": _inspect(
        "Read the existing Cinemachine brain's active virtual camera and current blend state. Reports an error if the package or brain is absent.",
        limit="Reads provider properties including the active camera name. A sample describes the current frame.",
    ),
    "ensure_brain": _edit(
        "Reuse an existing Cinemachine brain or attach one to a Unity Camera. properties.camera selects a particular camera. "
        "Without it, an existing brain is preferred, then the main camera fallback."
    ),
    "create_camera": _edit(
        "Create a scene GameObject with a preset Cinemachine virtual camera when supported, or a basic Unity Camera when Cinemachine is absent."
    ),
    "set_target": _edit(
        "Set follow or look-at references on the target Cinemachine camera. A basic Unity Camera rotates once to face the referenced object.",
        "target",
    ),
    "set_priority": _edit(
        "Set a Cinemachine camera's selection priority or a basic Unity Camera's render depth.",
        "target",
    ),
    "set_lens": _edit(
        "Write supported lens properties on the target Unity or Cinemachine camera, including field of view and clip planes.",
        "target",
    ),
    "set_body": _edit(
        "Configure the target Cinemachine camera's position control stage. bodyType can replace its existing component.",
        "target",
    ),
    "set_aim": _edit(
        "Configure the target Cinemachine camera's rotation control stage. aimType can replace its existing component.",
        "target",
    ),
    "set_noise": _edit(
        "Configure Cinemachine noise on the target camera. A missing Perlin component can be installed in its noise stage.",
        "target",
    ),
    "add_extension": _edit(
        "Attach a Cinemachine extension selected by properties.extensionType and apply its supplied properties on creation.",
        "target",
        "properties",
    ),
    "remove_extension": _edit(
        "Remove the selected extension component from the target Cinemachine camera using properties.extensionType.",
        "target",
        "properties",
    ),
    "set_blend": _edit(
        "Write the existing Cinemachine brain's default transition style or duration. Inspect the active blend separately."
    ),
    "force_camera": _interact(
        "Set an override on the existing Cinemachine brain to select the target camera until released. Blend completion requires a later observation.",
        "target",
    ),
    "release_override": _interact(
        "Release the tool's active camera override on the brain that owns it, allowing ordinary camera selection to resume."
    ),
    "screenshot": _inspect(
        "Capture a PNG from the Game View, an explicit camera or the Scene View. Inspect the reported capture source for a camera fallback.",
        limit="Capture can write files in the project and run rendering callbacks. A camera render omits overlay canvases and UI Toolkit panels.",
    ),
    "screenshot_multiview": _inspect(
        "Capture six surrounding viewpoints as a PNG contact sheet with the image included in the response.",
        limit="Selects surround capture and inline output. Capture can write files in the project and run rendering callbacks.",
    ),
}

_PANEL_ROOT: Final = "Reading a supported PanelRenderer root can temporarily register and unregister a reload callback."
_SOURCE_WRITE: Final = "Import and optional verify_target report separate stages. A saved file alone does not establish that a live panel rebuilt."
_UI: Final = {
    "ping": _read("Check that the selected Editor answers the UI Toolkit command dispatcher."),
    "list": _read(
        "List standalone UXML, USS and PanelSettings assets from the AssetDatabase in a folder, without loading their objects. "
        "Reuse inventory_revision with the same scope and filter to check subsequent pages."
    ),
    "read": _read(
        "Read the saved UTF-8 UXML or USS source file at path, independently of a scene panel or live visual tree.",
        "path",
    ),
    "create": _edit(
        "Create and import a new UXML or USS source file from contents.",
        "path",
        "contents",
        limit=_SOURCE_WRITE,
    ),
    "update": _edit(
        "Replace the existing UXML or USS source file with contents and import the result.",
        "path",
        "contents",
        limit=_SOURCE_WRITE,
    ),
    "delete": _edit("Delete the UXML or USS source asset at path from the project.", "path"),
    "link_stylesheet": _edit(
        "Add a reference to stylesheet in the UXML at path and import the changed source. An existing link is left in place.",
        "path",
        "stylesheet",
        limit=_SOURCE_WRITE,
    ),
    "patch_source": _edit(
        "Preview a targeted change to saved UXML or USS by default. preview=false with expected_source_hash from preview applies and reimports it.",
        "path",
        "source_selector",
        "source_changes",
        limit=_SOURCE_WRITE,
    ),
    "get_visual_tree": _inspect(
        "Inspect the current UI Toolkit element hierarchy, names, classes and available text on the target scene component within traversal budgets.",
        "target",
        limit="An unbuilt tree reports not_initialized. Custom control getters can run. "
        + _PANEL_ROOT,
    ),
    "diagnose": _inspect(
        "Inspect the current panel's resolved geometry for layout, clipping and overlap candidates within traversal and pair budgets.",
        "target",
        limit="Findings describe sampled geometry and omit text, bindings and source. "
        + _PANEL_ROOT,
    ),
    "attach_ui_document": _edit(
        "Attach or configure a UI component on target with its source_asset. Omitting panel_settings can reuse an existing PanelSettings or create the default asset.",
        "target",
        "source_asset",
    ),
    "detach_ui_document": _edit(
        "Remove the UI component from target and release its unused panel capture resources. The UXML source asset remains.",
        "target",
    ),
    "create_panel_settings": _edit(
        "Create a PanelSettings asset at path for UI Toolkit scaling, rendering and other supported panel options.",
        "path",
    ),
    "update_panel_settings": _edit(
        "Change the supported settings of an existing PanelSettings asset and save it.",
        "path",
        "settings",
    ),
    "modify_visual_element": _edit(
        "Change a selected live element's text, classes, style or state in the current UI tree. Edits do not rewrite the UXML or USS file.",
        "target",
        limit="Element changes can run control callbacks. strict_style validates requested styles before applying changes.",
    ),
    "render_ui": _inspect(
        "Capture a scene UI component or UXML asset as a PNG. Use capture_id to poll or cancel a pending capture.",
        limit="Rendering can allocate panel targets, run UI callbacks and write a PNG even with inline output.",
    ),
}

EDITOR_ACTIONS: Final = {"manage_asset": _ASSET, "manage_camera": _CAMERA, "manage_ui": _UI}
