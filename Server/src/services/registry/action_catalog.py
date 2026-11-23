"""One discovery projection of registered tools, actions and input contracts.

Catalog hints describe capabilities. Existing invocation and permission checks
remain authoritative and never consult these hints for admission or retries.
"""

import inspect
import re
from collections.abc import Callable, Collection, Mapping
from copy import deepcopy
from dataclasses import replace
from functools import lru_cache
from types import MappingProxyType
from typing import Final, Literal, NotRequired, TypedDict, get_args, get_origin

from mcp.types import ToolAnnotations
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    StrictInt,
    StrictStr,
    field_validator,
    model_validator,
)

from services.declared_action_contracts import ACTION_CONTRACTS
from services.operation_permissions import required_permissions
from services.reference_graph.action_options import GRAPH_ACTION_OPTIONS
from services.registry.action_contracts import ACTION_RULES
from services.registry.action_guidance_types import (
    ActionGuidance,
    ActionIntent,
    Observation,
    ToolGuidance,
)
from services.registry.action_guidance_types import (
    ObservationSource as ObservationSource,
    ObservationValue as ObservationValue,
)
from services.registry.capability_catalog import CAPABILITIES, READ_ONLY_EFFECTS, CapabilityEffects
from services.registry.editor_action_guidance import EDITOR_ACTIONS
from services.registry.integration_action_guidance import INTEGRATION_ACTIONS
from services.registry.open_shader_graph_observation import OPEN_GRAPH_OBSERVATION
from services.registry.tool_registry import TOOL_GROUPS, ToolMaturity


class CatalogEntry(TypedDict):
    name: str
    group: str | None
    description: str | None
    kwargs: dict[str, ToolAnnotations | dict[str, JsonValue]]
    maturity: NotRequired[ToolMaturity]
    func: NotRequired[Callable]
    input_schema: NotRequired[dict]


class SkillLink(TypedDict):
    name: str
    url: str


OBSERVATION_META: Final = "io.github.ykh09242.unity-mcp/observation"
SEARCH_TERMS_META: Final = "io.github.ykh09242.unity-mcp/search-terms"
ACTION_OBSERVATIONS_META: Final = "io.github.ykh09242.unity-mcp/action-observations"
GAME_VIEW_OBSERVATION: Final[Observation] = {
    "source": "editor_live",
    "values": [
        "render_size.width",
        "render_size.height",
        "current_size",
        "view_instance_id",
        "presets",
    ],
    "unavailable": ["rendered_image", "pointer_hit_order"],
}
GRAPH_FILE_OBSERVATION: Final[Observation] = {
    "source": "asset_file",
    "values": ["nodeCount", "edgeCount", "nodes", "edges", "properties", "keywords"],
    "unavailable": ["unsaved_graph_structure", "property_values", "shader_source"],
    "related_values": {"unsaved_graph_structure": ["nodeCount", "edgeCount", "nodes", "edges"]},
}


POINTER_ORDER_OBSERVATION: Final[Observation] = {
    "source": "editor_live",
    "values": ["pointer_hit_order"],
    "unavailable": [],
    "wire_paths": {"pointer_hit_order": "hits[].rank"},
    "required_parameters": ["target", "screen_position"],
    "fixed_arguments": {"allow_custom_filters": False, "strict_read": True},
    "predicates": {
        "all": [
            {"op": "equals", "path": "status", "value": "observed"},
            {"op": "equals", "path": "policy.strict_read", "value": True},
            {"op": "equals", "path": "policy.native_query_attempted", "value": True},
            {"op": "equals", "path": "policy.allow_custom_filters", "value": False},
            {"op": "equals", "path": "policy.custom_filters_present", "value": False},
            {"op": "equals", "path": "policy.user_code_may_have_run", "value": False},
            {"op": "equals", "path": "policy.package_explicit_event_dispatch", "value": False},
            {"op": "equals", "path": "policy.package_explicit_source_edits", "value": False},
            {"op": "equals", "path": "input.delivery", "value": "not_attempted"},
        ],
        "by_value": {
            "pointer_hit_order": [
                {
                    "op": "ordered_hits",
                    "path": "hits",
                    "rank_field": "rank",
                    "total_path": "hits_total",
                    "truncated_path": "hits_truncated",
                }
            ]
        },
    },
    "conditions": (
        "Requires a scene RectTransform target, in-bounds bottom-left Game View pixel coordinates, "
        "compatible official uGUI raycast and registry APIs, supported components and one active native EventSystem. "
        "Only status=observed with policy.native_query_attempted=true establishes ordered hits. "
        "Inspect hits_total and hits_truncated. Unknown, failed or missing evidence establishes no "
        "hit order. This query does not dispatch input or prove event receipt."
    ),
}
ACTIVE_SCENE_OBSERVATION: Final[Observation] = {
    "source": "editor_live",
    "values": ["active_scene.name", "active_scene.path"],
    "unavailable": [],
    "wire_paths": {"active_scene.name": "name", "active_scene.path": "path"},
    "predicates": {
        "all": [],
        "by_value": {
            "active_scene.name": [{"op": "type", "path": "name", "type": "string"}],
            "active_scene.path": [{"op": "type", "path": "path", "type": "string"}],
        },
    },
    "conditions": "Requires a valid active scene. An unsaved scene can have an empty path.",
}
EDITOR_STATE_OBSERVATION: Final[Observation] = {
    "source": "editor_live",
    "values": ["active_scene.name", "active_scene.path", "compilation.is_compiling"],
    "unavailable": ["compile_job.outcome"],
    "predicates": {
        "all": [{"op": "equals", "path": "staleness.is_stale", "value": False}],
        "by_value": {
            "active_scene.name": [
                {"op": "type", "path": "editor.active_scene.name", "type": "string"}
            ],
            "active_scene.path": [
                {"op": "type", "path": "editor.active_scene.path", "type": "string"}
            ],
            "compilation.is_compiling": [
                {"op": "type", "path": "compilation.is_compiling", "type": "boolean"}
            ],
        },
    },
    "wire_paths": {
        "active_scene.name": "editor.active_scene.name",
        "active_scene.path": "editor.active_scene.path",
    },
    "conditions": (
        "Check staleness.is_stale=false and the requested field in the selected Editor snapshot. "
        "An absent or null field supplies no observation. An unsaved scene can have an empty path. "
        "is_compiling reports activity at observation time."
    ),
}
COMPILE_JOB_OBSERVATION: Final[Observation] = {
    "source": "compile_job",
    "values": ["compile_job.outcome"],
    "unavailable": ["compilation.is_compiling"],
    "wire_paths": {"compile_job.outcome": "outcome"},
    "predicates": {
        "all": [
            {"op": "equals", "path": "state", "value": "finished"},
            {"op": "equals", "path": "attribution", "value": "observed_after_request"},
            {"op": "equals", "path": "collection_complete", "value": True},
            {"op": "type", "path": "compile_id", "type": "hex32"},
            {"op": "type", "path": "session_id", "type": "hex32"},
            {"op": "type", "path": "generation", "type": "positive_integer"},
            {"op": "type", "path": "assembly_count", "type": "positive_integer"},
            {
                "op": "single_item_equals",
                "path": "candidate_generations",
                "other_path": "generation",
            },
            {
                "op": "timestamp_lte",
                "path": "requested_at_unix_ms",
                "other_path": "started_at_unix_ms",
            },
            {
                "op": "timestamp_lte",
                "path": "started_at_unix_ms",
                "other_path": "finished_at_unix_ms",
            },
        ],
        "by_value": {
            "compile_job.outcome": [
                {"op": "one_of", "path": "outcome", "values": ["success", "failed"]}
            ]
        },
    },
    "required_parameters": ["compile_id"],
    "conditions": (
        "Poll the existing compile_id. Only a finished job with validated, complete attributed "
        "evidence can report success or failed. A pending or unknown outcome supplies no verdict."
    ),
}


class CatalogResource(TypedDict):
    name: str
    uri: str
    description: str | None
    func: NotRequired[Callable]
    schema_required: NotRequired[Collection[str]]
    kwargs: NotRequired[dict]


class CatalogResourceTemplate(TypedDict):
    kind: Literal["resource_template"]
    name: str
    uri_template: str
    description: str
    required_parameters: list[str]
    optional_parameters: list[str]
    observation: NotRequired[Observation]


RESOURCE_TEMPLATE_EXPANSION: Final = (
    "Expand the URI template (RFC 6570) with observed values for required parameters "
    "and any needed optional parameters, then read the resulting URI."
)


class CatalogTool(TypedDict):
    name: str
    group: str | None
    title: str
    description: str
    aliases: list[str]
    actions: list[str]
    maturity: ToolMaturity
    action_maturity: dict[str, ToolMaturity]
    advertised: bool | None
    readiness: Literal["unobserved"]
    requirements: list[str]
    effects: dict[str, JsonValue]
    official_skills: list[SkillLink]


# Paths and frontmatter verified in the official tree at this revision. Links are
# guidance only: a skill's prerequisites do not establish a tool's runtime support.
OFFICIAL_SKILLS_REVISION: Final = "089bedebab64e9e75943b19a364c7aec2162a7e7"
_SKILL_BASE: Final = (
    "https://github.com/Unity-Technologies/skills/blob/" + OFFICIAL_SKILLS_REVISION + "/skills/"
)
_PHYSICS: Final = ToolGuidance(
    skills=("create-2d-physics", "physics-3d-collision"),
)
_UITK: Final = ToolGuidance(skills=("ui-uitk",))
GAME_VIEW_READ: Final = (
    "The read-only tool get_game_view_size reads render_size in pixels and current_size from an open Game View "
    "in the selected Editor. Resource clients can read mcpforunity://editor/game-view-size."
)
SHADER_GRAPH_READ: Final = (
    "The read-only tool inspect_shader_graph and this tool's inspect_graph action read the "
    "saved .shadergraph file through the selected Editor. "
    "Unsaved node and edge counts are available through inspect_open_shader_graph or inspect_open_graph. "
    "Resource clients can read the saved graph at "
    "mcpforunity://shader/graph/{encoded_path}{?page_size,page_number}. "
    "Encode the full asset path as one URI segment."
)
_SCENE_OBSERVATION_IMPORT: Final = (
    "Before observation, preflight may import pending external asset changes when refresh "
    "is permitted. Changed scripts can trigger compilation and domain reload."
)
UI_CLICK_PURPOSE: Final = (
    "ui_click dispatches pointer events directly to a uGUI target such as a button, using its "
    "scene instance ID or exact root hierarchy path, independently of the raw input backend."
)
UI_CLICK_LIMIT: Final = "Direct dispatch does not test raycast occlusion. Observe the handler's gameplay effect separately."
UGUI_RAYCAST_PURPOSE: Final = (
    "diagnose_raycast inspects pointer hit order and raycast occlusion at screen_position [x,y] "
    "in Game View pixels from the bottom left. Supply a scene UI GameObject with a RectTransform "
    "as target to compare its handler with the first hit."
)
UGUI_RAYCAST_LIMIT: Final = (
    "No click is sent. An unknown result provides no confirmed hit order. "
    "Even a known hit order does not establish event receipt. Custom filters are refused by "
    "default; explicit allow_custom_filters may run user code without rollback or a hard deadline."
)
_PREFAB_CONTENTS: Final = ToolGuidance(
    actions={
        "modify_contents": ActionGuidance(
            "Edit objects and components in prefab_path and save the asset. With preview_only=true, load and edit isolated contents to inspect changes without saving; this may run load/edit callbacks and returns no apply token. preview_scope=components restricts observation to the named component_properties.",
            ("prefab_path",),
        ),
        "preview_modify_contents": ActionGuidance(
            "Preview only prefab root position, rotation, scale and set_active changes for prefab_path without saving. Return a one-attempt preview_id valid for 10 minutes; loading prefab contents may run callbacks.",
            ("prefab_path",),
        ),
        "apply_preview": ActionGuidance(
            "Recheck and save the retained prefab root changes using only preview_id from preview_modify_contents. Consume the one-attempt token and reject drift before applying; prefab load/save callbacks may run.",
            ("preview_id",),
        ),
    }
)
TOOL_GUIDANCE: Final = {
    **{item.name: _PHYSICS for item in CAPABILITIES if item.group == "physics"},
    **{item.name: _UITK for item in CAPABILITIES if item.group == "ui"},
    "manage_physics": _PHYSICS,
    "manage_ui": _UITK,
    "manage_ugui": ToolGuidance(
        skills=("ui-ugui",),
        actions={
            "get_hierarchy": ActionGuidance(
                "Inspect the existing Canvas hierarchy and UI components.",
                ("target",),
                intent="observe",
            ),
            "diagnose_raycast": ActionGuidance(
                UGUI_RAYCAST_PURPOSE,
                ("target", "screen_position"),
                evidence_limit=UGUI_RAYCAST_LIMIT,
                observation=POINTER_ORDER_OBSERVATION,
                intent="observe",
            ),
            "diagnose_projection": ActionGuidance(
                "Inspect current Screen Camera or World Space UI geometry and clipping without rendering or rebuilding layout.",
                intent="observe",
            ),
            "diagnose_text": ActionGuidance(
                "Inspect existing generated TextMeshPro layout for text overflow and missing layout data.",
                intent="observe",
            ),
            "diagnose_device_profiles": ActionGuidance(
                "Assess UI geometry across explicit device resolutions, safe areas and touch thresholds.",
                intent="observe",
            ),
        },
    ),
    "manage_scene": ToolGuidance(
        actions={
            "create": ActionGuidance(
                "Create and open a new .unity scene, replacing the current scene setup. "
                "Specify name and optionally a folder under Assets/ (default Assets/Scenes/) "
                "and template (empty, default, 3d_basic or 2d_basic). Existing destination files "
                "and unsaved scenes block creation.",
                ("name",),
                intent="edit",
            ),
            "load": ActionGuidance(
                "Open an existing scene by full path, name with an optional folder path, "
                "or build_index. Use additive=true to keep the current scenes. Single mode "
                "refuses loaded scenes with unsaved changes. Opening can run Editor scene "
                "lifecycle callbacks. Returns the opened scene's scene_state.",
                evidence_limit=(
                    "verify=true observes one bounded validation slice of the affected scene. "
                    "Check scan coverage before relying on a clean result."
                ),
                intent="edit",
            ),
            "save": ActionGuidance(
                "Save the active scene or select the loaded source with scene_handle, scene_path "
                "or scene_name. Use a destination .unity path or a folder and name to Save As. "
                "Replacing an existing Save As destination requires allow_overwrite=true.",
                evidence_limit=(
                    "scene_state identifies the affected scene. verify=true adds one bounded "
                    "validation slice, whose results may be incomplete."
                ),
                intent="edit",
            ),
            "get_hierarchy": ActionGuidance(
                "List one page of root GameObjects in the current Prefab Stage or active scene, "
                "or immediate children of parent. Pass next_cursor as cursor for the next page. "
                "Use include_transform for local transforms.",
                evidence_limit=(
                    "Each page covers one hierarchy level and reflects current state. "
                    + _SCENE_OBSERVATION_IMPORT
                ),
                intent="observe",
            ),
            "get_active": ActionGuidance(
                "Read the active scene's path, build index, root count and loaded/dirty flags.",
                observation=ACTIVE_SCENE_OBSERVATION,
                evidence_limit=_SCENE_OBSERVATION_IMPORT,
                intent="observe",
            ),
            "get_build_settings": ActionGuidance(
                "Read the Editor Build Settings scene list with enabled flags and build indexes. "
                "Use manage_build scenes to replace the list.",
                evidence_limit=_SCENE_OBSERVATION_IMPORT,
                intent="observe",
            ),
            "scene_view_frame": ActionGuidance(
                "Reposition the active Scene View camera to frame scene_view_target or the "
                "scene bounds. Use manage_camera to capture screenshots.",
            ),
            "close_scene": ActionGuidance(
                "Unload a scene identified by scene_path or a unique scene_name "
                "(name is also accepted). Use remove_scene=true to remove its hierarchy entry. "
                "Refuses a dirty scene or the last loaded scene. Closing can run Editor "
                "scene lifecycle callbacks.",
                intent="edit",
            ),
            "set_active_scene": ActionGuidance(
                "Set the active scene to one that is already loaded. Select it by scene_path "
                "or a unique scene_name (name is also accepted).",
                intent="edit",
                search_examples=("Switch to the scene I named and make it the active scene.",),
            ),
            "get_loaded_scenes": ActionGuidance(
                "List all scenes currently open in the Editor with their names, paths and "
                "loaded, active and dirty flags. "
                "Use a loaded scene's scene_handle to identify it exactly for save or validate.",
                evidence_limit=_SCENE_OBSERVATION_IMPORT,
                intent="observe",
                search_examples=("Which scenes are currently open?",),
            ),
            "move_to_scene": ActionGuidance(
                "Move a root GameObject to an already loaded scene selected by scene_path "
                "or a unique scene_name (name is also accepted). Use an instance ID to disambiguate "
                "the target. In Edit Mode the move uses Undo.",
                ("target",),
                intent="edit",
            ),
            "validate": ActionGuidance(
                "Scan the active scene or the specified scene_handle for missing scripts and "
                "broken prefab instances. Resume bounded read-only scans with continuation. "
                "auto_repair=true removes missing scripts without Undo after a complete new scan.",
                evidence_limit=(
                    "Check scanComplete. A partial scan cannot establish that the whole scene "
                    "is clean. Use validate_references for broken serialized object references."
                ),
            ),
            "validate_references": ActionGuidance(
                "Page broken serialized object references in the active scene, all loaded scenes, "
                "or one prefab or ScriptableObject asset. Asset scopes require path and load "
                "that asset. Use reference_revision to reject drift on a live rescan, or "
                "reference_snapshot and the returned referenceCursor for captured pages.",
                evidence_limit=(
                    "Check scanComplete. Ordinary nulls are excluded and target GUID/fileID are "
                    "unavailable. Captured pages report past observations. No repairs are applied."
                ),
                intent="observe",
            ),
        },
    ),
    "manage_shader": ToolGuidance(
        actions={
            "create": ActionGuidance(
                "Create and import a .shader file in the selected directory. Give name without "
                "an extension and optionally supply contents instead of the default shader.",
                ("name",),
                evidence_limit=(
                    "Success confirms the save/import. Check compilation.status for Unity's "
                    "compiler observation, which can be pending or unavailable."
                ),
                intent="edit",
            ),
            "read": ActionGuidance(
                "Read a bounded .shader source page by extensionless name and directory path. "
                "Offsets count source bytes after the BOM. While hasMore is true, pass nextOffset "
                "as read_offset to continue.",
                ("name",),
                READ_ONLY_EFFECTS,
                evidence_limit=(
                    "Use read_encoding=text for contents or base64 for encodedContents. "
                    "nextOffset is populated only when another source page exists."
                ),
                observation={
                    "source": "asset_file",
                    "values": ["contents", "encodedContents", "nextOffset"],
                    "unavailable": ["shader_graph_structure", "unsaved_source"],
                    "mutually_exclusive_values": [["contents", "encodedContents"]],
                },
            ),
            "update": ActionGuidance(
                "Replace the entire existing .shader source with contents and import it. "
                "Use name without an extension and path for the containing directory.",
                ("name", "contents"),
                evidence_limit=(
                    "Success confirms the save/import. Unity can still report compiler errors "
                    "or pending compilation. Inspect compilation.status."
                ),
                intent="edit",
            ),
            "delete": ActionGuidance(
                "Delete the .shader asset selected by extensionless name and directory path.",
                ("name",),
                intent="edit",
            ),
            "inspect_graph": ActionGuidance(
                "Read paged serialized Shader Graph nodes, edges, properties and keywords "
                "from a full .shadergraph asset path. " + SHADER_GRAPH_READ,
                ("path",),
                READ_ONLY_EFFECTS,
                evidence_limit=(
                    "Covers saved MultiJSON GraphData v0-3. Node and property summaries include "
                    "identifiers and names. Unsaved graph structure and property values are "
                    "unavailable. Use diagnostics for imported pass and compiler information."
                ),
                observation=GRAPH_FILE_OBSERVATION,
            ),
            "inspect_open_graph": ActionGuidance(
                "Read live node and edge counts, including unsaved edits, from an existing Shader Graph window.",
                ("path",),
                READ_ONLY_EFFECTS,
                evidence_limit="Counts require one matching window with status=open_model.",
                observation=OPEN_GRAPH_OBSERVATION,
            ),
            "diagnostics": ActionGuidance(
                "Read cached compiler messages and list imported subshader/pass indexes, stage "
                "names and compiler platforms for a full .shader or .shadergraph asset path.",
                ("path",),
                READ_ONLY_EFFECTS,
                evidence_limit=(
                    "Cached messages describe Unity's current observation. Check for pending or "
                    "unavailable compilation evidence and truncated pass lists."
                ),
                observation={
                    "source": "imported_asset",
                    "values": ["compilation", "passes", "subshader_count"],
                    "unavailable": ["unsaved_graph_structure", "shader_source"],
                },
            ),
            "compile_variant": ActionGuidance(
                "Invoke Unity's compiler in idle Edit Mode for one variant of a full .shader "
                "or .shadergraph asset path. Select shader_type, platform and build_target in "
                "variant, with optional subshader_index, pass_index and keywords.",
                ("path", "variant"),
                evidence_limit=(
                    "Check outcome and bytecode_size. no_program means empty bytecode. "
                    "The observation covers one variant and provides no Player build result."
                ),
            ),
        },
    ),
    "manage_editor": ToolGuidance(
        actions={
            "telemetry_status": ActionGuidance(
                "Read whether server tool-usage telemetry is enabled.",
                evidence_limit=(
                    "This can start background telemetry initialization and create local "
                    "telemetry storage. An initial false can reflect initialization in progress."
                ),
                intent="observe",
            ),
            "telemetry_ping": ActionGuidance(
                "Submit a server telemetry diagnostic ping through the configured recorder.",
                evidence_limit="The acknowledgement provides no delivery confirmation.",
            ),
            "play": ActionGuidance(
                "Request entry into Play Mode. Supply wait_until=scene_loaded or first_frame "
                "to observe a readiness job within timeout_seconds.",
                evidence_limit=(
                    "Without a readiness wait, pending acknowledges the transition request. "
                    "Observe mcpforunity://editor/state for stable entry. first_frame observes "
                    "simulation progress only. Rendering and application initialization require "
                    "their own evidence."
                ),
                intent="interact",
            ),
            "pause": ActionGuidance(
                "Pause the running game at its current state or resume the paused game in Play Mode. "
                "This action toggles the current pause state.",
                intent="interact",
            ),
            "stop": ActionGuidance(
                "Request exit from Play Mode and cancel pending play readiness monitoring.",
                evidence_limit=(
                    "pending acknowledges the transition request. Observe "
                    "mcpforunity://editor/state for stable Edit Mode."
                ),
                intent="interact",
            ),
            "set_active_tool": ActionGuidance(
                "Choose the active Unity transform tool using tool_name.", ("tool_name",)
            ),
            "add_tag": ActionGuidance(
                "Create a new tag in the project with the supplied name.",
                ("tag_name",),
                intent="edit",
                search_examples=("Add a new tag to the project.",),
            ),
            "remove_tag": ActionGuidance(
                "Delete an existing tag from the project by name.",
                ("tag_name",),
                intent="edit",
                search_examples=("Remove an existing tag from the project.",),
            ),
            "add_layer": ActionGuidance(
                "Create a new layer with the supplied name in the first available user layer slot.",
                ("layer_name",),
                intent="edit",
                search_examples=("Add a new layer to the project.",),
            ),
            "remove_layer": ActionGuidance(
                "Delete a named user layer from the project by clearing its slot.",
                ("layer_name",),
                intent="edit",
                search_examples=("Remove an existing layer from the project.",),
            ),
            "deploy_package": ActionGuidance(
                "Replace the Unity MCP package from its stored source. Requires an enabled "
                "explicit manage_packages consent grant in the Tools panel."
            ),
            "restore_package": ActionGuidance(
                "Restore the last Unity MCP package backup. Requires an enabled explicit "
                "manage_packages consent grant in the Tools panel."
            ),
            "undo": ActionGuidance(
                "Perform Unity Undo on the current group and return its recorded name if known.",
                evidence_limit="The Undo stack may be empty. Observe affected state to confirm.",
            ),
            "redo": ActionGuidance(
                "Perform Unity Redo and return the resulting Undo group name if known.",
                evidence_limit="Observe affected state to confirm the edit was reapplied.",
            ),
            "get_play_mode_job": ActionGuidance(
                "Poll the Play Mode readiness job returned by play with wait_until.",
                ("job_id",),
                evidence_limit=(
                    "Use the job status to establish readiness. A late observation can establish "
                    "current readiness after the original deadline."
                ),
                intent="observe",
            ),
            "cancel_play_mode_job": ActionGuidance(
                "Cancel monitoring for the given Play Mode readiness job. Use stop to request "
                "exit from Play Mode.",
                ("job_id",),
            ),
            "get_game_view_size": ActionGuidance(
                GAME_VIEW_READ,
                effects=READ_ONLY_EFFECTS,
                evidence_limit=(
                    "Inspect render_size for screen coordinates. current_size describes the "
                    "selected preset, which may specify an aspect ratio. The read requires an "
                    "existing Game View and reports its view_instance_id."
                ),
                observation=GAME_VIEW_OBSERVATION,
            ),
            "set_game_view_size": ActionGuidance(
                "Change the open Game View to a fixed width and height, an aspect_ratio W:H "
                "or a unique preset label. Returns the resulting size and a restore_token.",
            ),
            "restore_game_view_size": ActionGuidance(
                "Use the saved restore token to return the open Game View to its previous preset. "
                "The token comes from set_game_view_size. "
                "The original Game View, build-target group and preset must still be available.",
                ("restore_token",),
            ),
        },
    ),
    "manage_input": ToolGuidance(
        actions={
            "ui_click": ActionGuidance(
                UI_CLICK_PURPOSE,
                ("target",),
                evidence_limit=UI_CLICK_LIMIT,
                intent="interact",
            ),
            "mouse": ActionGuidance(
                "In Play Mode, move the simulated mouse to screen coordinates, then press the button there through the Input System. Observe the visible response separately.",
                intent="interact",
            ),
            "touch": ActionGuidance(
                "In Play Mode, press or move a simulated touch at a screen position through the Input System. Observe the receiving UI and resulting game state.",
                intent="interact",
            ),
            "status": ActionGuidance(
                "Read the available input backends and currently held controls before choosing a simulation method.",
                effects=READ_ONLY_EFFECTS,
            ),
        },
    ),
    "prefab_contents": _PREFAB_CONTENTS,
    "manage_prefabs": _PREFAB_CONTENTS,
    "manage_build": ToolGuidance(
        actions={
            "plan": ActionGuidance(
                "Observe one ordinary non-profile build using target, output_path, scenes, development, options, subtarget and scripting_backend; omitted inputs use current defaults. Return plan_sha256 without saving, switching targets, changing settings or starting a build. The hash covers declared observations, not every project input or toolchain readiness.",
                effects=READ_ONLY_EFFECTS,
            ),
            "build_from_plan": ActionGuidance(
                "Repeat the plan's ordinary build inputs with expected_plan_sha256 from a fresh plan. Reobserve and reject a mismatched hash before scheduling an asynchronous build; admitted builds save assets/scenes, may persist scripting_backend changes and run build callbacks. Observe the returned job_id with status.",
                ("expected_plan_sha256",),
            ),
            "build": ActionGuidance(
                "Schedule an asynchronous ordinary or profile build without a plan hash guard. Build preparation saves assets/scenes, may persist scripting_backend changes and runs build callbacks; target/output_path/scenes/options or profile select the build. Observe the returned job_id with status."
            ),
            "status": ActionGuidance(
                "Observe a single or batch build by job_id. With no job_id, report the active "
                "or latest available build observation.",
                effects=READ_ONLY_EFFECTS,
                evidence_limit=(
                    "A successful status query can report a failed or cancelled build. "
                    "Check state and result, and use report for retained report details."
                ),
            ),
            "platform": ActionGuidance(
                "Read the active build target and subtarget when target is omitted. Supplying "
                "target switches the Editor to an installed platform and may reimport assets."
            ),
            "settings": ActionGuidance(
                "Read the selected build property when value is omitted; providing value writes the persistent setting, including an explicit empty string where supported. product_name, company_name and version are project-wide PlayerSettings; bundle_id, scripting_backend, defines and architecture use the selected named build target.",
                ("property",),
            ),
            "scenes": ActionGuidance(
                "Read the current build scene list when scenes is omitted. Providing scenes replaces the list; a JSON empty array clears it."
            ),
            "profiles": ActionGuidance(
                "List Build Profiles when profile is omitted, or load the selected profile's scene details. With profile and activate=true, change the active Build Profile when the Editor exposes activation."
            ),
            "batch": ActionGuidance(
                "Queue player builds using targets or supported Build Profile assets, with optional output_dir. "
                "Queued target builds can switch the active build target and reimport assets. "
                "Build preparation saves assets and dirty scenes that have a path. "
                "Unity can run callbacks during each build.",
                evidence_limit=(
                    "The queue acknowledgement confirms scheduling. Poll status with the returned "
                    "job_id for build outcomes. Preparation skips dirty scenes without a saved path."
                ),
            ),
            "cancel": ActionGuidance(
                "Request cancellation for the supplied build or batch job_id.",
                ("job_id",),
                evidence_limit=(
                    "Queued work can be cancelled. Unity cannot interrupt an invoked native "
                    "build through this action."
                ),
            ),
            "clean_output": ActionGuidance(
                "Preview removal of the explicit output_path under Builds/ by default. Only dry_run=false deletes that selected descendant; links and active builds are refused.",
                ("output_path",),
            ),
            "report_history": ActionGuidance(
                "Page the retained build report inventory to find report_id values for report "
                "and compare_reports. Pass history_revision to reject inventory drift.",
                effects=READ_ONLY_EFFECTS,
                evidence_limit="Reports expire after 30 days or bounded eviction.",
            ),
            "report": ActionGuidance(
                "Read a retained build report by report_id. Select summary, steps or packed_assets "
                "with report_section. Pass report_revision to reject drift on later pages.",
                ("report_id",),
                READ_ONLY_EFFECTS,
                evidence_limit="Check for partial, missing or corrupt evidence.",
            ),
            "compare_reports": ActionGuidance(
                "Compare retained baseline and candidate build reports by their IDs. Select "
                "summary, steps or packed_assets with report_section.",
                ("baseline_report_id", "candidate_report_id"),
                READ_ONLY_EFFECTS,
                evidence_limit=(
                    "Comparison covers retained observations. Check partial evidence before "
                    "interpreting differences."
                ),
            ),
        },
    ),
    "manage_scene_edits": ToolGuidance(
        actions={
            "undo": ActionGuidance(
                "Restore recorded scene or prefab fields that still match their applied values. "
                "Inspect reported before/applied/current conflicts.",
                ("receipt_id",),
            ),
            "preview": ActionGuidance(
                "Observe before/after values for 1-1000 edits to allowlisted native Transform, BoxCollider, SphereCollider, CapsuleCollider or Rigidbody serialized fields in stable Edit Mode. Retain a one-attempt preview_id valid for 10 minutes without applying the edits.",
                ("edits",),
            ),
            "apply": ActionGuidance(
                "Apply native serialized scene field changes from preview_id, or direct edits containing expected_old for every field. Reject drift before mutation and record one named Undo group; no general transaction or dirty-flag rollback is promised."
            ),
            "preview_structure": ActionGuidance(
                "Preview 1-256 ordered create/delete/reparent operations within an integer scene-root target scope. Only ordinary Transform-only subtrees are supported; reparent preserves local transforms. Retain preview_id without applying hierarchy changes.",
                ("target", "operations"),
            ),
            "apply_structure": ActionGuidance(
                "Apply the retained scene hierarchy changes using only preview_id from preview_structure. Reject hierarchy/state drift before mutation and record one Undo group; consume the one-attempt token.",
                ("preview_id",),
            ),
        },
    ),
    "manage_profiler": ToolGuidance(
        actions={
            "get_frame_timing": ActionGuidance(
                "Read current FrameTimingManager CPU/GPU frame timing data."
            ),
            "get_counters": ActionGuidance(
                "Read profiler counters by category and optional counter names after a one-frame wait."
            ),
            "window_start": ActionGuidance(
                "Start bounded per-frame profiler sampling to investigate slow frames, hitches and spikes, returning a window_id."
            ),
            "window_status": ActionGuidance(
                "Inspect progress of an existing profiler sampling window.", ("window_id",)
            ),
            "window_result": ActionGuidance(
                "Read terminal per-frame statistics from an existing profiler sampling window.",
                ("window_id",),
            ),
            "window_cancel": ActionGuidance(
                "Dispose an existing profiler sampling window.", ("window_id",)
            ),
            "memory_compare_snapshots": ActionGuidance("Compare legacy snapshot file metadata."),
            "memory_analyze_snapshots": ActionGuidance(
                "Compare typed native-object counts, bytes and identities in memory snapshots using the supported Memory Profiler reader."
            ),
        },
    ),
    "manage_render_volumes": ToolGuidance(skills=("urp-postprocessing",)),
    "manage_renderer_features": ToolGuidance(
        skills=("validate-urp-render-graph-renderer-feature",),
        requirements=("com.unity.render-pipelines.universal; renderer features require URP",),
    ),
    "manage_render_pipeline": ToolGuidance(
        skills=("validate-urp-render-graph-renderer-feature",),
    ),
    "manage_audio": ToolGuidance(skills=("audio-setup-mixers",)),
    "manage_localization": ToolGuidance(
        skills=("localization",),
        requirements=(
            "com.unity.localization; availability must be inspected in the selected project",
        ),
    ),
    "manage_packages": ToolGuidance(skills=("unity-package-management",)),
    "manage_probuilder": ToolGuidance(requirements=("com.unity.probuilder",)),
    "manage_cinemachine": ToolGuidance(
        requirements=("ping checks availability; other actions require com.unity.cinemachine",)
    ),
    "manage_project_auditor": ToolGuidance(
        skills=("project-auditor-fixes",),
        requirements=(
            "Call availability to check the installed Project Auditor package or Editor module and its rules.",
        ),
    ),
    "manage_navigation": ToolGuidance(
        requirements=(
            "capabilities: no optional package required; inspect module/package availability first",
            "Native navigation operations require com.unity.modules.ai",
            "surface/link operations, bake and sample_position/calculate_path require com.unity.ai.navigation; "
            "agent/obstacle operations use the built-in AI module",
            "Scene operations require an explicit loaded scene_handle; "
            "sample_position/calculate_path also require a configured agent_type_id and baked NavMesh data",
            "bake_status: job_id is optional; omit it to list retained jobs",
            "cancel_bake requires an existing job_id",
            "add/configure/bake require idle Edit Mode",
        ),
    ),
    "manage_netcode": ToolGuidance(
        requirements=(
            "capabilities: no optional package required; inspect package availability first",
            "preflight requires com.unity.netcode.gameobjects and an explicit loaded scene_handle; "
            "inspects local configuration without starting a network session",
        ),
    ),
    "manage_input_actions": ToolGuidance(
        requirements=(
            "capabilities: no optional package required; inspect package availability first",
            "read and authoring operations require the installed Input System asset API "
            "and an Assets .inputactions path",
            "Authoring operations require idle Edit Mode; edits to existing assets require expected_revision",
        ),
    ),
}
# Focused adapters share the source tool's dispatcher and server preflight.
_REVIEWED_DISPATCHERS: Final = EDITOR_ACTIONS | INTEGRATION_ACTIONS
for source, actions in _REVIEWED_DISPATCHERS.items():
    TOOL_GUIDANCE[source] = replace(TOOL_GUIDANCE.get(source, ToolGuidance()), actions=actions)
for capability in CAPABILITIES:
    if capability.source in _REVIEWED_DISPATCHERS:
        TOOL_GUIDANCE[capability.name] = replace(
            TOOL_GUIDANCE.get(capability.name, ToolGuidance()),
            actions={
                name: _REVIEWED_DISPATCHERS[capability.source][name]
                for name in capability.actions
                if name in _REVIEWED_DISPATCHERS[capability.source]
            },
        )

# Both reads forward to manage_scene. Keep the focused inventory's original
# identifier searchable on its own alongside the newly shared description.
_SCENE_INVENTORY: Final = TOOL_GUIDANCE["manage_scene"].actions["get_loaded_scenes"]
TOOL_GUIDANCE["scene_inspect"] = ToolGuidance(
    actions={
        "get_active": TOOL_GUIDANCE["manage_scene"].actions["get_active"],
        "get_loaded_scenes": replace(
            _SCENE_INVENTORY,
            search_examples=(*_SCENE_INVENTORY.search_examples, "scene inspect get loaded scenes"),
        ),
    }
)
_SCENE_ACTIVATION: Final = TOOL_GUIDANCE["manage_scene"].actions["set_active_scene"]
TOOL_GUIDANCE["scene_lifecycle"] = ToolGuidance(
    actions={
        "set_active_scene": replace(
            _SCENE_ACTIVATION,
            purpose=f"Make this the destination for new GameObjects. {_SCENE_ACTIVATION.purpose}",
        )
    }
)
_CAPABILITIES: Final = {item.name: item for item in CAPABILITIES}


def operation_owner(name: str, selector: str) -> str:
    """Resolve only selectors actually forwarded by a registered capability."""
    capability = _CAPABILITIES.get(name)
    return capability.source if capability and selector in capability.actions else name


def action_maturity(name: str) -> dict[str, ToolMaturity]:
    """Expose reviewed action overrides on both focused and source declarations."""
    return {
        action: "experimental"
        for capability in CAPABILITIES
        if name in (capability.name, capability.source)
        for action in capability.experimental_actions
    }


def declared_actions(entry: CatalogEntry) -> list[str]:
    """Reuse Literal types or Annotated catalog metadata, preserving legacy str admission."""
    capability = _CAPABILITIES.get(entry["name"])
    if capability is not None:
        return list(capability.actions)
    function = entry.get("func")
    if function is None:
        selector = entry.get("input_schema", {}).get("properties", {}).get("action", {})
        if not isinstance(selector, dict):
            return []
        if isinstance(selector.get("const"), str):
            return [selector["const"]]
        return [value for value in selector.get("enum", []) if isinstance(value, str)]
    parameter = inspect.signature(function, eval_str=True).parameters.get("action")
    if parameter is None:
        return []

    def literals(annotation) -> list[str]:
        if get_origin(annotation) is Literal:
            return [value for value in get_args(annotation) if isinstance(value, str)]
        return [value for argument in get_args(annotation) for value in literals(argument)]

    return list(dict.fromkeys(literals(parameter.annotation)))


def guidance_for(name: str) -> ToolGuidance:
    return TOOL_GUIDANCE.get(name, ToolGuidance())


def action_observations(name: str) -> dict[str, Observation]:
    return {
        action: guidance.observation
        for action, guidance in guidance_for(name).actions.items()
        if guidance.observation
    }


def resource_observation(item: CatalogResource) -> dict:
    meta = item.get("kwargs", {}).get("meta") or {}
    observation = meta.get(OBSERVATION_META)
    return {"observation": observation} if observation else {}


def _required_tool_parameters(entry: CatalogEntry) -> list[str]:
    function = entry.get("func")
    parameters = inspect.signature(function).parameters.values() if function else ()
    return [
        name for name in entry.get("input_schema", {}).get("required", ()) if name != "action"
    ] + [
        parameter.name
        for parameter in parameters
        if parameter.name not in {"ctx", "action"}
        and parameter.default is inspect.Parameter.empty
        and parameter.kind not in {inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD}
    ]


_DOCUMENTED_ACTION = re.compile(
    r"^[^\S\r\n]*[-*][^\S\r\n]+`?(\w+)`?[^\S\r\n]*:[^\S\r\n]*(\S[^\r\n]*)", re.MULTILINE
)


@lru_cache(maxsize=1)
def documented_actions(description: str) -> Mapping[str, str]:
    """Reuse the current dispatcher's declared action lines across its individual cards."""
    return MappingProxyType(dict(_DOCUMENTED_ACTION.findall(description)))


def action_guidance(
    entry: CatalogEntry, action: str | None, *, inherit: bool = True
) -> ActionGuidance | None:
    """Use reviewed action evidence or the tool's own wire schema alternative."""
    if action is None:
        observation = (entry.get("kwargs", {}).get("meta") or {}).get(OBSERVATION_META)
        if observation:
            return ActionGuidance(
                tool_description(entry),
                tuple(_required_tool_parameters(entry)),
                observation=observation,
            )
        return None
    guidance = TOOL_GUIDANCE.get(entry["name"])
    if guidance and action in guidance.actions:
        return guidance.actions[action]
    contract = ACTION_CONTRACTS.get(entry["name"], {}).get(action)
    if contract:
        return ActionGuidance(
            contract.purpose,
            effects=READ_ONLY_EFFECTS if contract.read_only else CapabilityEffects(),
            intent="observe" if contract.read_only else "edit",
        )
    owner = operation_owner(entry["name"], action)
    source = TOOL_GUIDANCE.get(owner) if inherit and owner != entry["name"] else None
    shared = source.actions.get(action) if source else None
    for variant in entry.get("input_schema", {}).get("oneOf", ()):
        selector = (
            variant.get("properties", {}).get("action") if isinstance(variant, dict) else None
        )
        if isinstance(selector, dict) and selector.get("const") == action:
            purpose = variant.get("description")
            if not isinstance(purpose, str) or not purpose.strip():
                purpose = documented_actions(tool_description(entry)).get(action, "")
            return ActionGuidance(
                purpose or (shared.purpose if shared else ""),
                tuple(name for name in variant.get("required", ()) if name != "action"),
                intent=shared.intent if shared else None,
            )
    # A focused adapter forwards this exact selector. Its public schema still
    # owns parameters, evidence and permissions. Share the underlying operation's
    # meaning so selecting a catalog profile cannot turn a declared edit into an
    # unknown action or drop its description from search.
    if shared:
        return ActionGuidance(
            shared.purpose, intent=shared.intent, search_examples=shared.search_examples
        )
    return None


def tool_description(entry: CatalogEntry) -> str:
    capability = _CAPABILITIES.get(entry["name"])
    return entry.get("description") or (capability.description if capability else "")


def tool_capability(entry: CatalogEntry, *, visible_names: set[str] | None = None) -> CatalogTool:
    """Shared metadata for lexical search, browsing and exact descriptions."""
    name = entry["name"]
    capability = _CAPABILITIES.get(name)
    guidance = guidance_for(name)
    annotations = ToolAnnotations.model_validate(entry.get("kwargs", {}).get("annotations") or {})
    return {
        "name": name,
        "group": entry.get("group"),
        "title": (annotations.title or (capability.title if capability else name))[:256],
        "description": tool_description(entry)[:1200],
        "aliases": [capability.source] if capability else [],
        "actions": declared_actions(entry),
        "maturity": entry.get("maturity", "stable"),
        "action_maturity": action_maturity(name),
        "advertised": name in visible_names if visible_names is not None else None,
        "readiness": "unobserved",
        "requirements": list(guidance.requirements),
        "effects": {
            "readOnlyHint": annotations.read_only_hint is True,
            "destructiveHint": annotations.destructive_hint is not False,
            "idempotentHint": annotations.idempotent_hint is True,
            "openWorldHint": annotations.open_world_hint is not False,
        },
        "official_skills": [
            {"name": skill, "url": f"{_SKILL_BASE}{skill}/SKILL.md"} for skill in guidance.skills
        ],
    }


def action_intent(guidance: ActionGuidance | None, tool: CatalogTool) -> ActionIntent | None:
    """Classify reviewed purposes or declared read-only effects, never admission."""
    if guidance and guidance.intent:
        return guidance.intent
    read_only = (
        guidance.effects.read_only
        if guidance and guidance.effects
        else tool["effects"]["readOnlyHint"]
    )
    return "observe" if read_only is True else None


def action_card(entry: CatalogEntry, action: str | None) -> dict:
    """Describe one declared operation, retaining conservative tool-wide effects."""
    declared = declared_actions(entry) or [None]
    if action not in declared:
        raise ValueError(f"Unknown action {action!r} for {entry['name']}.")
    tool = tool_capability(entry)
    guidance = action_guidance(entry, action)
    rules = next(
        (rule for rule in ACTION_RULES.get(entry["name"], ()) if rule.action == action), None
    )
    contract = ACTION_CONTRACTS.get(entry["name"], {}).get(action)
    required = _required_tool_parameters(entry)
    effects = tool["effects"]
    if guidance and guidance.effects:
        effects = {
            "readOnlyHint": guidance.effects.read_only,
            "destructiveHint": guidance.effects.destructive,
            "idempotentHint": guidance.effects.idempotent,
            "openWorldHint": guidance.effects.open_world,
        }
    graph_options = (
        GRAPH_ACTION_OPTIONS[action] if entry["name"] == "manage_reference_graph" else None
    )
    if graph_options and (graph_options.required or graph_options.exactly_one):
        required.append("options")
    return {
        "tool": entry["name"],
        "action": action,
        "intent": action_intent(guidance, tool),
        "purpose": guidance.purpose
        if guidance and guidance.purpose
        else (action.replace("_", " ") if action else tool["description"]),
        "purpose_source": "declared"
        if (guidance and guidance.purpose) or action is None
        else "identifier",
        **({"observation": guidance.observation} if guidance and guidance.observation else {}),
        **(
            {"evidence_limit": guidance.evidence_limit}
            if guidance and guidance.evidence_limit
            else {}
        ),
        "required_parameters": list(
            dict.fromkeys(
                [
                    *required,
                    *(rules.required if rules else ()),
                    *(guidance.required if guidance else ()),
                ]
            )
        ),
        "forbidden_parameters": list(rules.forbidden) if rules else [],
        **({"alias_of": contract.alias_of} if contract and contract.alias_of else {}),
        **(
            {"success_data_schema": rules.success_data_schema()}
            if rules and rules.success_fields
            else {}
        ),
        **(
            {
                "required_permissions": sorted(
                    permission.value
                    for permission in required_permissions(entry["name"], rules.example_arguments())
                )
            }
            if rules and (contract or rules.success_fields)
            else {}
        ),
        **({"options_schema": graph_options.schema()} if graph_options else {}),
        "input_contract": "Inspect the full tool schema and parameter descriptions for conditional requirements.",
        "effects": effects,
        "effects_scope": "action" if guidance and guidance.effects else "tool",
        "maturity": tool["action_maturity"].get(action, tool["maturity"]),
        "invoke": {"tool": entry["name"], "arguments": {"action": action} if action else {}},
    }


class CatalogRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    group: StrictStr | None = Field(default=None, min_length=1, max_length=128)
    tool: StrictStr | None = Field(default=None, min_length=1, max_length=128)
    intent: ActionIntent | None = None
    offset: StrictInt = Field(default=0, ge=0)
    limit: StrictInt = Field(default=20, ge=1, le=20)

    @field_validator("group", "tool")
    @classmethod
    def nonempty_selector(cls, value: str | None) -> str | None:
        if value is not None:
            value = value.strip()
            if not value:
                raise ValueError("Catalog selectors must contain a non-whitespace character.")
        return value

    @model_validator(mode="after")
    def validate_scope(self):
        if (
            self.intent
            and not self.tool
            and (self.group is None or self.group in {"resources", "resource_templates"})
        ):
            raise ValueError("Select a tool or tool group to browse actions by intent.")
        if self.group is not None and self.tool is not None:
            raise ValueError("Select one catalog group or tool.")
        if self.group is None and self.tool is None and self.offset:
            raise ValueError("The complete group index uses offset 0.")
        return self

    @property
    def includes_resources(self) -> bool:
        return self.tool is None and self.group in (None, "resources", "resource_templates")


def concrete_resources(resources: list[CatalogResource]) -> list[CatalogResource]:
    """Only fixed Unity resource URIs can be read directly."""
    return sorted(
        (
            item
            for item in resources
            if item["uri"].startswith("mcpforunity://") and "{" not in item["uri"]
        ),
        key=lambda item: item["uri"],
    )


def resource_templates(resources: list[CatalogResource]) -> list[CatalogResourceTemplate]:
    """Project template variables, keeping their spelling and order for URI expansion."""
    templates: list[CatalogResourceTemplate] = []
    for item in resources:
        uri = item["uri"]
        if not uri.startswith("mcpforunity://"):
            continue
        expressions = re.findall(r"\{([^{}]+)\}", uri)
        if not expressions:
            continue
        declared_required = set(item.get("schema_required", ()))
        if function := item.get("func"):
            declared_required.update(
                parameter.name
                for parameter in inspect.signature(function).parameters.values()
                if parameter.default is inspect.Parameter.empty
            )
        variables: dict[str, bool] = {}
        for expression in expressions:
            operator = expression[0] if expression[0] in "+#./;?&" else ""
            for spec in expression[len(operator) :].split(","):
                name = spec.removesuffix("*").partition(":")[0]
                # A simple path value occupies a required segment. Prefixed expansions
                # such as {/child} can disappear unless the handler requires the variable.
                variables[name] = (
                    variables.get(name, False)
                    or operator in ("", "+")
                    or name in declared_required
                    or name.replace("-", "_") in declared_required
                )
        templates.append(
            {
                "kind": "resource_template",
                "name": item["name"],
                "uri_template": uri,
                "description": item["description"] or "",
                **resource_observation(item),
                "required_parameters": [name for name, required in variables.items() if required],
                "optional_parameters": [
                    name for name, required in variables.items() if not required
                ],
            }
        )
    return sorted(templates, key=lambda item: item["uri_template"])


def _resource_branch(total: int) -> dict:
    return {
        "purpose": "Read published Unity Editor, project and scene observations.",
        "total": total,
        "browse": {"action": "catalog", "group": "resources"},
    }


def _resource_template_branch(total: int) -> dict:
    return {
        "purpose": "Read parameterized Unity observations, such as one GameObject or prefab.",
        "total": total,
        "browse": {"action": "catalog", "group": "resource_templates"},
    }


def catalog_navigation(
    entries: list[CatalogEntry],
    resources: list[CatalogResource],
    templates: list[CatalogResourceTemplate] | None = None,
) -> dict:
    """An agent can choose a domain by purpose without translating or matching query words."""
    groups = sorted({item["group"] for item in entries if item.get("group")})
    return {
        "groups": [
            {
                "group": group,
                "purpose": TOOL_GROUPS.get(group, group),
                "browse": {"action": "catalog", "group": group},
            }
            for group in groups
        ],
        "server_tools": sorted(item["name"] for item in entries if not item.get("group")),
        "resources": _resource_branch(len(resources)),
        "resource_templates": _resource_template_branch(len(templates or ())),
    }


def browse_catalog(
    request: CatalogRequest,
    entries: list[CatalogEntry],
    *,
    visible_names: set[str] | None = None,
    resources: list[CatalogResource] | None = None,
) -> dict:
    """Traverse all declarations without a query, ranking, connection or mutation."""
    ordered = sorted(entries, key=lambda entry: entry["name"])
    response = {
        "scope": "mcp_context" if visible_names is not None else "local_catalog",
        "selection": "agent",
        "catalog_language": "en",
        "readiness": "unobserved",
        "hint": "Interpret the user's intent in its original language. Compare purposes, targets and effects, then describe the exact tool and operation before calling it. Follow next_offset when the current page does not resolve the task.",
        "note": "Declarations describe capabilities. Advertisement, project readiness and execution permission require their own checks. Identifier-derived purposes use the enclosing tool description as context.",
    }
    if request.intent is not None:
        selector = "name" if request.tool else "group"
        selected = [
            entry for entry in ordered if entry.get(selector) == (request.tool or request.group)
        ]
        if not selected and (request.tool or request.group not in TOOL_GROUPS):
            raise ValueError("Unknown tool or group for intent-based browsing.")
        candidates = []
        for entry in selected:
            summary = tool_capability(entry, visible_names=visible_names)
            for operation in summary["actions"] or [None]:
                if action_intent(action_guidance(entry, operation), summary) == request.intent:
                    candidates.append((entry, operation, summary["advertised"]))
        response.update(view="intent_actions", group=request.group, intent=request.intent)
        rows = [
            {**action_card(entry, operation), "advertised": advertised, "readiness": "unobserved"}
            for entry, operation, advertised in candidates[
                request.offset : request.offset + request.limit
            ]
        ]
        total = len(candidates)
        key = "actions"
    elif request.group == "resources":
        available = concrete_resources(resources or [])
        response.update(view="resources", group="resources")
        response["resource_templates"] = _resource_template_branch(
            len(resource_templates(resources or []))
        )
        total = len(available)
        rows = [
            {
                "name": item["name"],
                "uri": item["uri"],
                "description": item["description"],
                **resource_observation(item),
                "advertised": True if visible_names is not None else None,
                "readiness": "unobserved",
            }
            for item in available[request.offset : request.offset + request.limit]
        ]
        key = "resources"
    elif request.group == "resource_templates":
        templates = resource_templates(resources or [])
        response.update(view="resource_templates", group="resource_templates")
        response["input_contract"] = RESOURCE_TEMPLATE_EXPANSION
        total = len(templates)
        rows = [
            {
                **item,
                "advertised": True if visible_names is not None else None,
                "readiness": "unobserved",
            }
            for item in templates[request.offset : request.offset + request.limit]
        ]
        key = "resource_templates"
    elif request.tool is not None:
        entry = next((entry for entry in ordered if entry["name"] == request.tool), None)
        if entry is None:
            raise ValueError(f"Unknown tool {request.tool!r}.")
        response["view"] = "actions"
        response["tool"] = tool_capability(entry, visible_names=visible_names)
        # Retain action-specific constraints anywhere in the declared description.
        response["tool"]["description"] = tool_description(entry)
        actions = declared_actions(entry) or [None]
        total = len(actions)
        rows = [
            action_card(entry, action)
            for action in actions[request.offset : request.offset + request.limit]
        ]
        key = "actions"
    elif request.group is not None:
        selected = [entry for entry in ordered if entry.get("group") == request.group]
        if not selected and request.group not in TOOL_GROUPS:
            raise ValueError(f"Unknown group {request.group!r}.")
        response.update(view="tools", group=request.group)
        total = len(selected)
        rows = [
            tool_capability(entry, visible_names=visible_names)
            for entry in selected[request.offset : request.offset + request.limit]
        ]
        key = "tools"
    else:
        response["view"] = "groups"
        response["resources"] = _resource_branch(len(concrete_resources(resources or [])))
        response["resource_templates"] = _resource_template_branch(
            len(resource_templates(resources or []))
        )
        groups = sorted({entry.get("group") for entry in ordered}, key=lambda value: value or "")
        response["groups"] = [
            {
                "name": group,
                "description": TOOL_GROUPS.get(
                    group, "Always-visible server discovery and routing tools."
                ),
                "tools": [entry["name"] for entry in ordered if entry.get("group") == group],
            }
            for group in groups
        ]
        response["total_tools"] = len(ordered)
        response["next_offset"] = None
        return response
    end = request.offset + request.limit
    response.update(total=total, offset=request.offset, next_offset=end if end < total else None)
    response[key] = rows
    return response


def describe_action(
    entry: CatalogEntry,
    descriptor: dict,
    operation: str | None = None,
    *,
    visible_names: set[str] | None = None,
) -> dict:
    """Bind a selected action while preserving the actual SDK schema and constraints."""
    descriptor = deepcopy(descriptor)
    card = None
    if operation is not None:
        card = action_card(entry, operation)
        schema = descriptor.get("inputSchema", descriptor.get("input_schema"))
        action_schema = schema["properties"]["action"]
        action_schema["const"] = operation
        action_schema.pop("default", None)
        schema["required"] = list(
            dict.fromkeys([*schema.get("required", ()), "action", *card["required_parameters"]])
        )
        if "options_schema" in card:
            option_schema = card["options_schema"]
            options = schema["properties"]["options"]
            options.update(option_schema)
            if option_schema["type"] == "object":
                if "options" not in schema["required"]:
                    schema["required"].append("options")
                options.pop("default", None)
    elif not declared_actions(entry):
        card = action_card(entry, None)
    catalog = tool_capability(entry, visible_names=visible_names)
    return {
        "tool": descriptor,
        "catalog": catalog,
        "operation": card,
        "maturity": catalog["maturity"],
        "action_maturity": catalog["action_maturity"],
        "note": "The full declared schema retains conditional requirements. Catalog effects are hints. Selection, readiness and permissions are evaluated independently.",
    }
