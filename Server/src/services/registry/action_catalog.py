"""One discovery projection of registered tools, actions and input contracts.

Catalog hints describe capabilities. Existing invocation and permission checks
remain authoritative and never consult these hints for admission or retries.
"""

from copy import deepcopy
from dataclasses import dataclass, field
import inspect
from typing import Callable, Final, Literal, NotRequired, TypedDict, get_args, get_origin

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

from services.registry.action_contracts import ACTION_RULES
from services.registry.capability_catalog import CAPABILITIES, CapabilityEffects, READ_ONLY_EFFECTS
from services.registry.tool_registry import TOOL_GROUPS, ToolMaturity


class CatalogEntry(TypedDict):
    name: str
    group: str | None
    description: str | None
    kwargs: dict[str, ToolAnnotations | dict[str, JsonValue]]
    maturity: NotRequired[ToolMaturity]
    func: NotRequired[Callable]


class SkillLink(TypedDict):
    name: str
    url: str


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


@dataclass(frozen=True, slots=True)
class ActionGuidance:
    purpose: str
    required: tuple[str, ...] = ()
    effects: CapabilityEffects | None = None


@dataclass(frozen=True, slots=True)
class ToolGuidance:
    keywords: str = ""
    skills: tuple[str, ...] = ()
    requirements: tuple[str, ...] = ()
    actions: dict[str, ActionGuidance] = field(default_factory=dict)


# Paths and frontmatter verified in the official tree at this revision. Links are
# guidance only: a skill's prerequisites do not establish a tool's runtime support.
OFFICIAL_SKILLS_REVISION: Final = "089bedebab64e9e75943b19a364c7aec2162a7e7"
_SKILL_BASE: Final = (
    "https://github.com/Unity-Technologies/skills/blob/" + OFFICIAL_SKILLS_REVISION + "/skills/"
)
_PHYSICS: Final = ToolGuidance(
    "collision collide trigger rigidbody raycast 충돌 물리 트리거",
    ("create-2d-physics", "physics-3d-collision"),
)
_UITK: Final = ToolGuidance("ui toolkit uxml uss layout text 레이아웃", ("ui-uitk",))
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
_ASSET_REFERENCES: Final = ToolGuidance(
    actions={
        "get_dependencies": ActionGuidance(
            "Find assets referenced by the selected asset.", ("path",)
        ),
        "get_reverse_references": ActionGuidance(
            "Find project assets that refer to the selected asset, including usages of a prefab.",
            ("path",),
        ),
        "preview_move": ActionGuidance(
            "Observe bounded reference impact of moving path to destination without moving it. Return referenceRevision for an optional move with reference_revision; inspect scan coverage before relying on the result.",
            ("path", "destination"),
        ),
        "preview_delete": ActionGuidance(
            "Observe bounded references and deletion impact for path without deleting it. Return referenceRevision for an optional delete with reference_revision; inspect scan coverage before relying on the result.",
            ("path",),
        ),
    }
)
_ASSET_MUTATIONS: Final = {
    "move": ActionGuidance(
        "Move path to destination through the AssetDatabase. Supply reference_revision from preview_move to recheck the same scan options, require complete scan coverage and reject reference drift before mutation; omitting it performs an unguarded move.",
        ("path", "destination"),
    ),
    "delete": ActionGuidance(
        "Delete the asset at path through the AssetDatabase. Supply reference_revision from preview_delete to recheck the same scan options, require complete scan coverage and reject reference drift before mutation; omitting it performs an unguarded delete.",
        ("path",),
    ),
}
# Retain existing aliases for query-only callers. Language-independent discovery
# uses complete catalog traversal and the calling agent's interpretation.
TOOL_GUIDANCE: Final = {
    **{item.name: _PHYSICS for item in CAPABILITIES if item.group == "physics"},
    **{item.name: _UITK for item in CAPABILITIES if item.group == "ui"},
    "manage_physics": _PHYSICS,
    "manage_ui": _UITK,
    "manage_ugui": ToolGuidance(
        "canvas recttransform ui layout 캔버스",
        ("ui-ugui",),
        actions={
            "get_hierarchy": ActionGuidance(
                "Inspect the existing Canvas hierarchy and UI components."
            ),
            "diagnose_raycast": ActionGuidance(
                "Investigate blocked UI clicks using current pointer hit order and input-module/handler facts. Custom filters require explicit opt-in and may run user code."
            ),
            "diagnose_projection": ActionGuidance(
                "Inspect current Screen Camera or World Space UI geometry and clipping without rendering or rebuilding layout."
            ),
            "diagnose_text": ActionGuidance(
                "Inspect existing generated TextMeshPro layout for text overflow and missing layout data."
            ),
            "diagnose_device_profiles": ActionGuidance(
                "Assess UI geometry across explicit device resolutions, safe areas and touch thresholds."
            ),
        },
    ),
    "manage_shader": ToolGuidance(
        "shader graph inspect diagnostics compile 셰이더그래프 셰이더 그래프 검사 컴파일",
        actions={
            "read": ActionGuidance(
                "Read a bounded page of shader source bytes using its name and containing directory.",
                ("name",),
                READ_ONLY_EFFECTS,
            ),
            "inspect_graph": ActionGuidance(
                "Inspect Shader Graph serialized nodes, edges, properties and keywords without compiling or editing the asset.",
                ("path",),
                READ_ONLY_EFFECTS,
            ),
            "diagnostics": ActionGuidance(
                "Read cached compiler messages for a shader or Shader Graph asset.",
                ("path",),
                READ_ONLY_EFFECTS,
            ),
            "compile_variant": ActionGuidance(
                "Compile one explicitly selected shader variant in idle Edit Mode and return its outcome and bounded diagnostics.",
                ("path", "variant"),
            ),
        },
    ),
    "capture_prefab": ToolGuidance("prefab capture preview thumbnail 프리팹 캡처 미리보기"),
    "manage_input": ToolGuidance(
        "ui button click input pointer 버튼 클릭 입력 포인터",
        actions={
            "ui_click": ActionGuidance(
                "Send a UI click through the selected runtime input backend and observe input receipt separately from gameplay effects."
            )
        },
    ),
    "asset_reference_diagnostics": _ASSET_REFERENCES,
    "asset_edit": ToolGuidance(actions=_ASSET_MUTATIONS),
    "manage_asset": ToolGuidance(actions={**_ASSET_REFERENCES.actions, **_ASSET_MUTATIONS}),
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
            "settings": ActionGuidance(
                "Read the selected build property when value is omitted; providing value writes the persistent setting, including an explicit empty string where supported. product_name, company_name and version are project-wide PlayerSettings; bundle_id, scripting_backend, defines and architecture use the selected named build target.",
                ("property",),
            ),
            "scenes": ActionGuidance(
                "Read the current build scene list when scenes is omitted. Providing scenes replaces the list; a JSON empty array clears it."
            ),
            "profiles": ActionGuidance(
                "List Unity 6 Build Profiles when profile is omitted, or load the selected profile's scene details. With profile and activate=true, change the active Build Profile."
            ),
            "clean_output": ActionGuidance(
                "Preview removal of the explicit output_path under Builds/ by default. Only dry_run=false deletes that selected descendant; links and active builds are refused.",
                ("output_path",),
            ),
        },
    ),
    "manage_scene_edits": ToolGuidance(
        actions={
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
    "unity_reflect": ToolGuidance(
        "type member method api reflection 타입 멤버 조회 메서드 리플렉션"
    ),
    "manage_render_volumes": ToolGuidance(
        "bloom volume postprocessing urp 블룸 후처리", ("urp-postprocessing",)
    ),
    "manage_renderer_features": ToolGuidance(
        "urp render graph renderer feature 렌더러",
        ("validate-urp-render-graph-renderer-feature",),
        ("com.unity.render-pipelines.universal; renderer features require URP",),
    ),
    "manage_render_pipeline": ToolGuidance(
        "render pipeline quality urp hdrp pink magenta rendering diagnostics 파이프라인 분홍 보라",
        ("validate-urp-render-graph-renderer-feature",),
    ),
    "manage_audio": ToolGuidance("sound mixer routing 소리 오디오 믹서", ("audio-setup-mixers",)),
    "manage_localization": ToolGuidance(
        "translate language locale table 번역 언어 현지화",
        ("localization",),
        ("com.unity.localization; availability must be inspected in the selected project",),
    ),
    "manage_packages": ToolGuidance(
        "install package upm 패키지 설치", ("unity-package-management",)
    ),
    "manage_probuilder": ToolGuidance("model mesh modeling 모델링", (), ("com.unity.probuilder",)),
    "manage_cinemachine": ToolGuidance(
        "camera follow blend 카메라", (), ("com.unity.cinemachine",)
    ),
    "manage_project_auditor": ToolGuidance(
        "project auditor static analysis audit diagnostics 정적 분석 감사",
        ("project-auditor-fixes",),
        ("com.unity.project-auditor; reviewed API 1.0.1, Unity >=2021.3; call availability",),
    ),
    "manage_navigation": ToolGuidance(
        "navmesh navigation pathfinding surface agent obstacle link",
        (),
        (
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
        "netcode network multiplayer preflight",
        (),
        (
            "capabilities: no optional package required; inspect package availability first",
            "preflight requires com.unity.netcode.gameobjects and an explicit loaded scene_handle; "
            "inspects local configuration without starting a network session",
        ),
    ),
    "manage_input_actions": ToolGuidance(
        "input actions binding inputsystem",
        (),
        (
            "capabilities: no optional package required; inspect package availability first",
            "read and authoring operations require com.unity.inputsystem >=1.7.0 "
            "and an Assets .inputactions path",
            "Authoring operations require idle Edit Mode; edits to existing assets require expected_revision",
        ),
    ),
    "read_response": ToolGuidance("large response truncated retrieve page 응답 페이지"),
}
_CAPABILITIES: Final = {item.name: item for item in CAPABILITIES}


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
        return []
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


def action_card(entry: CatalogEntry, action: str | None) -> dict:
    """Describe one declared operation, retaining conservative tool-wide effects."""
    declared = declared_actions(entry) or [None]
    if action not in declared:
        raise ValueError(f"Unknown action {action!r} for {entry['name']}.")
    tool = tool_capability(entry)
    guidance = guidance_for(entry["name"]).actions.get(action)
    rules = next(
        (rule for rule in ACTION_RULES.get(entry["name"], ()) if rule.action == action), None
    )
    function = entry.get("func")
    parameters = inspect.signature(function).parameters.values() if function else ()
    required = [
        parameter.name
        for parameter in parameters
        if parameter.name not in {"ctx", "action"}
        and parameter.default is inspect.Parameter.empty
        and parameter.kind not in {inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD}
    ]
    effects = tool["effects"]
    if guidance and guidance.effects:
        effects = {
            "readOnlyHint": guidance.effects.read_only,
            "destructiveHint": guidance.effects.destructive,
            "idempotentHint": guidance.effects.idempotent,
            "openWorldHint": guidance.effects.open_world,
        }
    return {
        "tool": entry["name"],
        "action": action,
        "purpose": guidance.purpose
        if guidance
        else (action.replace("_", " ") if action else tool["description"]),
        "purpose_source": "declared" if guidance or action is None else "identifier",
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
        if self.group is not None and self.tool is not None:
            raise ValueError("Select one catalog group or tool.")
        if self.group is None and self.tool is None and self.offset:
            raise ValueError("The complete group index uses offset 0.")
        return self


def browse_catalog(
    request: CatalogRequest, entries: list[CatalogEntry], *, visible_names: set[str] | None = None
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
    if request.tool is not None:
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
        schema["required"] = list(dict.fromkeys([*schema.get("required", ()), "action"]))
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
