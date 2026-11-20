"""Purposes and evidence of reviewed package, job and menu operations."""

from typing import Final

from services.registry.action_guidance_types import ActionGuidance, Observation
from services.registry.capability_catalog import READ_ONLY_EFFECTS, CapabilityEffects


def _observe(
    purpose: str,
    *required: str,
    effects: CapabilityEffects | None = None,
    observation: Observation | None = None,
) -> ActionGuidance:
    # Asset loads, provider requests and job bookkeeping keep the enclosing tool's effect hints.
    return ActionGuidance(purpose, required, effects, intent="observe", observation=observation)


def _edit(purpose: str, *required: str) -> ActionGuidance:
    return ActionGuidance(purpose, required, intent="edit")


def _interact(purpose: str, *required: str) -> ActionGuidance:
    return ActionGuidance(purpose, required, intent="interact")


NAVIGATION_AVAILABILITY: Final[Observation] = {
    "source": "editor_live",
    "values": ["navigation.module_available", "navigation.package_available"],
    "wire_paths": {
        "navigation.module_available": "module_available",
        "navigation.package_available": "package_available",
    },
    "unavailable": [],
    "predicates": {
        "all": [],
        "by_value": {
            "navigation.module_available": [
                {"op": "type", "path": "module_available", "type": "boolean"}
            ],
            "navigation.package_available": [
                {"op": "type", "path": "package_available", "type": "boolean"}
            ],
        },
    },
}
AUDITOR_AVAILABILITY: Final[Observation] = {
    "source": "editor_live",
    "values": ["project_auditor.api_supported", "project_auditor.rules_available"],
    "wire_paths": {
        "project_auditor.api_supported": "api_supported",
        "project_auditor.rules_available": "rules_available",
    },
    "unavailable": [],
    "predicates": {
        "all": [],
        "by_value": {
            "project_auditor.api_supported": [
                {"op": "type", "path": "api_supported", "type": "boolean"}
            ],
            "project_auditor.rules_available": [
                {"op": "type", "path": "rules_available", "type": "boolean"}
            ],
        },
    },
}

_ATLAS_ID = ("path", "guid", "file_id", "expected_revision")
_TIMELINE_ID = ("path", "guid", "file_id", "expected_revision")
_DIRECTOR_ID = ("scene_handle", "target", "expected_director_revision")

MENU_DECLARATIONS_READ = (
    "To discover an unknown path, mcpforunity://menu-items lists managed MenuItem declarations. "
    "A match confirms that declaration. Some native menu entries may be absent."
)

INTEGRATION_ACTIONS: Final = {
    "manage_addressables": {
        "inspect": _observe(
            "Page existing Addressables groups, entries or raw profile values and their settings revision.",
            "settings_path",
        ),
        "preflight": _observe(
            "Check the selected settings for duplicate addresses, missing references and profile issues.",
            "settings_path",
        ),
        "analyze_layout": _observe(
            "Analyze duplicated content in an existing native build-layout JSON report.",
            "layout_path",
        ),
        "set_address": _edit(
            "Change an existing entry's address at the observed settings revision.",
            "settings_path",
            "expected_revision",
            "entry_guid",
            "address",
        ),
        "move_entry": _edit(
            "Move an existing entry to an existing group at the observed settings revision.",
            "settings_path",
            "expected_revision",
            "entry_guid",
            "group_guid",
        ),
        "set_profile_value": _edit(
            "Replace a raw variable value in an existing profile.",
            "settings_path",
            "expected_revision",
        "preview_prefab": _edit(
            "Prepare a receipt-scoped prefab copy and registration.",
            "operation_id",
            "settings_path",
            "group_guid",
            "source_prefab",
            "prefab_path",
        ),
        "create_prefab": _edit(
            "Create the previewed prefab and checkpoint its actual GUID.", "operation_id"
        ),
        "register_prefab": _edit(
            "Register the checkpointed prefab in its previewed group.", "operation_id"
        ),
        "prefab_status": _observe(
            "Inspect the last confirmed step and outstanding work.", "operation_id"
        ),
        "cancel_prefab": _edit("Cancel outstanding prefab workflow steps.", "operation_id"),
            "profile_id",
            "variable_name",
            "value",
        ),
    },
    "manage_project_auditor": {
        "availability": _observe(
            "Check this Editor's Project Auditor provider, native analysis API and rules. Reads capability state without starting an audit.",
            observation=AUDITOR_AVAILABILITY,
        ),
        "start": _interact(
            "Start an Editor-session audit for the current build target. It may compile Player assemblies and save Auditor settings."
        ),
        "status": _observe(
            "Read the state and outcome of the audit identified by audit_id.", "audit_id"
        ),
        "issues": _observe(
            "Page the captured diagnostics of a finished audit using its returned cursor.",
            "audit_id",
        ),
    },
    "manage_navigation": {
        "capabilities": _observe(
            "Check the built-in navigation module and AI Navigation authoring package in the selected Editor. When available, read agent types, named areas and supported component properties.",
            effects=READ_ONLY_EFFECTS,
            observation=NAVIGATION_AVAILABILITY,
        ),
        "list": _observe(
            "List components of the requested navigation kind in one loaded scene.",
            "scene_handle",
            "kind",
        ),
        "read": _observe(
            "Read navigation properties of the selected component in its scene.",
            "scene_handle",
            "kind",
            "target",
        ),
        "add": _edit(
            "Add one navigation component to the specified scene object with Undo.",
            "scene_handle",
            "kind",
            "target",
        ),
        "configure": _edit(
            "Change supported properties on an existing scene navigation component.",
            "scene_handle",
            "kind",
            "target",
            "properties",
        ),
        "bake": _edit(
            "Queue a native bake on one eligible active surface and return its job identity.",
            "scene_handle",
            "kind",
            "target",
        ),
        "bake_status": _observe(
            "Read a bake job's progress or list retained navigation jobs when job_id is omitted."
        ),
        "cancel_bake": _interact(
            "Cancel a queued navigation bake. A synchronous bake already running cannot be interrupted.",
            "job_id",
        ),
        "sample_position": _observe(
            "Sample baked NavMesh data near a world position using an agent type. Check the returned scene-attribution limits.",
            "scene_handle",
            "agent_type_id",
            "position",
        ),
        "calculate_path": _observe(
            "Calculate a path between two world positions on the current NavMesh for an agent type.",
            "scene_handle",
            "agent_type_id",
            "start",
            "end",
        ),
    },
    "manage_localization": {
        "capabilities": _observe(
            "Read installed locale, string-table, key identity and fallback capabilities."
        ),
        "list_locales": _observe(
            "List existing locale asset identities and identifiers in the provided paths or bounded discovery scope."
        ),
        "list_tables": _observe(
            "List existing localization table assets and locale identities in the discovery scope."
        ),
        "inspect": _observe(
            "Read shared keys, missing entries, asset references and static Smart String findings for the selected tables.",
            "table_paths",
        ),
        "set_string_entry": _edit(
            "Update an existing string entry using exactly one of key_id or key_name and the observed table revision.",
            "table_path",
            "value",
            "expected_revision",
        ),
        "set_fallbacks": _edit(
            "Replace a locale's ordered fallback references using its observed revision. An empty list clears all fallbacks.",
            "locale_path",
            "fallback_locale_paths",
            "expected_revision",
        ),
        "set_asset_entry": _edit(
            "Asset-table edits are refused because the native operation also changes Addressables groups, labels and shared metadata."
        ),
    },
    "manage_sprite_atlas": {
        "capabilities": _observe(
            "Read installed atlas provider operations and the active build target."
        ),
        "read": _observe(
            "Read atlas packing settings, packable identities and the revision required for editing.",
            "path",
        ),
        "create": _edit(
            "Create a new atlas at path with explicit include-in-build and initial settings.",
            "path",
            "properties",
        ),
        "configure": _edit(
            "Apply supported importer or atlas settings using the observed atlas identity and revision.",
            *_ATLAS_ID,
            "properties",
        ),
        "add_packables": _edit(
            "Add exact sprite references to an existing atlas.", *_ATLAS_ID, "properties"
        ),
        "remove_packables": _edit(
            "Remove exact sprite references from an existing atlas.", *_ATLAS_ID, "properties"
        ),
        "pack": _interact(
            "Run native packing for the observed atlas and active build target.",
            *_ATLAS_ID,
            "build_target",
        ),
    },
    "manage_timeline": {
        "capabilities": _observe("Read available Timeline asset, binding and preview APIs."),
        "read": _observe(
            "Read saved tracks, clips, persistent identities and the Timeline revision.", "path"
        ),
        "read_director": _observe(
            "Read the selected scene director's playable asset, bindings and director revision.",
            "scene_handle",
            "target",
        ),
        "create": _edit("Create a Timeline asset at a new .playable path.", "path"),
        "add_track": _edit(
            "Add a named animation or audio track to the saved Timeline.",
            *_TIMELINE_ID,
            "properties",
        ),
        "add_clip": _edit(
            "Add an identified clip source to a track with explicit timing properties.",
            *_TIMELINE_ID,
            "properties",
        ),
        "assign": _edit(
            "Assign the selected Timeline asset to an existing scene director.",
            *_TIMELINE_ID,
            *_DIRECTOR_ID,
        ),
        "bind": _edit(
            "Set the director's track binding to the specified compatible scene component.",
            *_TIMELINE_ID,
            *_DIRECTOR_ID,
            "properties",
        ),
        "preview_start": _interact(
            "Start an owned director preview and obtain its preview_id.",
            *_TIMELINE_ID,
            *_DIRECTOR_ID,
        ),
        "seek": _interact("Evaluate an owned preview at a specified time.", "preview_id", "time"),
        "preview_stop": _interact("Stop the preview identified by preview_id.", "preview_id"),
    },
    "manage_packages": {
        "ping": _observe("Check whether the Editor's Package Manager handler responds."),
        "list_packages": _observe(
            "Request the installed package list; poll the returned query job when pending."
        ),
        "search_packages": _observe(
            "Query the package registry and inspect the returned matches or pending job.", "query"
        ),
        "get_package_info": _observe(
            "Read the registered information for one installed package.", "package"
        ),
        "list_registries": _observe("Read the project's configured scoped package registries."),
        "status": _observe(
            "Observe a Package Manager job. Use its job_id for list and search requests; an unresolved Resolve receipt does not establish completion."
        ),
        "add_package": _edit(
            "Install a package from its identifier or supported source.", "package"
        ),
        "remove_package": _edit(
            "Remove an installed package; force can override dependent-package checks.", "package"
        ),
        "embed_package": _edit(
            "Embed a registered package in the project's Packages directory.", "package"
        ),
        "resolve_packages": _interact(
            "Trigger native package resolution and retain the request identity; Unity may provide no completion signal."
        ),
        "add_registry": _edit(
            "Add a named scoped registry to the project manifest.", "name", "url", "scopes"
        ),
        "remove_registry": _edit(
            "Remove a scoped registry from the project manifest by name or URL."
        ),
    },
    "execute_menu_item": {
        "inspect": _observe(
            "Read the declaration for an exact Editor menu path without invoking its validation callback. "
            + MENU_DECLARATIONS_READ,
            "menu_path",
            effects=READ_ONLY_EFFECTS,
        ),
        "exists": _observe(
            "Check the exact menu declaration's existence without evaluating availability.",
            "menu_path",
            effects=READ_ONLY_EFFECTS,
        ),
        "evaluate_availability": _observe(
            "Evaluate whether the menu is enabled. Unity can run the project's menu validation callback.",
            "menu_path",
        ),
        "execute": _interact(
            "Invoke the exact Editor menu command. Its effects depend on the registered handler.",
            "menu_path",
        ),
    },
    "import_model": {
        "search": _observe(
            "Search the external model marketplace and return matching model identifiers.", "query"
        ),
        "preview": _observe(
            "Fetch external model metadata, license and thumbnail links for a marketplace UID.",
            "uid",
        ),
        "import": _edit(
            "Download and import the selected marketplace model, returning an import job.", "uid"
        ),
        "status": _observe("Read the import job's progress, destination or error.", "job_id"),
        "cancel": _interact("Request cancellation of the in-flight marketplace import.", "job_id"),
        "list_providers": _observe("List marketplace providers and their configuration status."),
    },
    "manage_recording": {
        "capabilities": _observe(
            "Check native recording availability and host encoding constraints."
        ),
        "start": _interact("Start recording rendered frames to a local output file."),
        "status": _observe(
            "Read the recording job state, captured frames and skipped sample slots.", "job_id"
        ),
        "stop": _interact("Stop the specified recording and finalize its output.", "job_id"),
    },
    "manage_play_scenario": {
        "save": _edit("Save a scenario definition and its steps.", "scenario"),
        "get": _observe("Read one saved scenario definition by name.", "name"),
        "list": _observe("List the saved scenarios available to this project."),
        "delete": _edit("Delete the named saved scenario.", "name"),
        "run": _interact("Run a saved scenario in Unity and obtain a job identity.", "name"),
        "status": _observe("Read the progress and outcome of a scenario job.", "job_id"),
        "cancel": _interact("Request cancellation of a running scenario job.", "job_id"),
        "reports": _observe("Read scenario report history, optionally filtered by name."),
        "suite_save": _edit("Save a suite definition and its scenario selection.", "suite"),
        "suite_get": _observe("Read one saved scenario suite.", "name"),
        "suite_list": _observe("List available saved scenario suites."),
        "suite_delete": _edit("Delete the named saved scenario suite.", "name"),
        "suite_run": _interact(
            "Run a saved scenario suite and obtain its suite job identity.", "name"
        ),
        "suite_status": _observe("Read suite job progress and child scenario results.", "suite_id"),
        "suite_cancel": _interact("Request cancellation of a running scenario suite.", "suite_id"),
        "suite_reports": _observe("Read stored scenario suite reports."),
    },
}
