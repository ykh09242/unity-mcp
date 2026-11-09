"""Bounded local task matching over declarations; never an admission mechanism."""

from dataclasses import dataclass
import inspect
import re
from typing import Callable, Final, Literal, NotRequired, TypedDict, get_args, get_origin

from mcp.types import ToolAnnotations
from pydantic import BaseModel, ConfigDict, Field, JsonValue, StrictInt, StrictStr, field_validator

from services.registry.capability_catalog import CAPABILITIES
from services.registry.tool_registry import ToolMaturity


class SearchRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    query: StrictStr = Field(min_length=1, max_length=512)
    limit: StrictInt = Field(default=5, ge=1, le=20)

    @field_validator("query")
    @classmethod
    def nonempty_query(cls, value: str) -> str:
        """Normalize surrounding whitespace once at the public boundary."""
        value = value.strip()
        if not value:
            raise ValueError("Query must contain a non-whitespace character.")
        return value


class SearchEntry(TypedDict):
    name: str
    group: str | None
    description: str | None
    kwargs: dict[str, ToolAnnotations | dict[str, JsonValue]]
    maturity: NotRequired[ToolMaturity]
    func: NotRequired[Callable]


class SkillLink(TypedDict):
    name: str
    url: str


class SearchResult(TypedDict):
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


class SearchResponse(TypedDict):
    scope: Literal["local_catalog", "mcp_context"]
    query: str
    results: list[SearchResult]
    total_matches: int
    truncated: bool
    note: str


@dataclass(frozen=True, slots=True)
class TaskPurpose:
    keywords: str = ""
    skills: tuple[str, ...] = ()
    requirements: tuple[str, ...] = ()


# Paths and frontmatter verified in the official tree at this revision. Links are
# guidance only: a skill's prerequisites do not establish a tool's runtime support.
OFFICIAL_SKILLS_REVISION: Final = "089bedebab64e9e75943b19a364c7aec2162a7e7"
_SKILL_BASE: Final = (
    "https://github.com/Unity-Technologies/skills/blob/" + OFFICIAL_SKILLS_REVISION + "/skills/"
)
_PHYSICS: Final = TaskPurpose(
    "collision collide trigger rigidbody raycast 충돌 물리 트리거",
    ("create-2d-physics", "physics-3d-collision"),
)
_UITK: Final = TaskPurpose("ui toolkit uxml uss layout text 레이아웃", ("ui-uitk",))
_PURPOSES: Final = {
    **{item.name: _PHYSICS for item in CAPABILITIES if item.group == "physics"},
    **{item.name: _UITK for item in CAPABILITIES if item.group == "ui"},
    "manage_physics": _PHYSICS,
    "manage_ui": _UITK,
    "manage_ugui": TaskPurpose("canvas recttransform ui layout 캔버스", ("ui-ugui",)),
    "manage_render_volumes": TaskPurpose(
        "bloom volume postprocessing urp 블룸 후처리", ("urp-postprocessing",)
    ),
    "manage_renderer_features": TaskPurpose(
        "urp render graph renderer feature 렌더러",
        ("validate-urp-render-graph-renderer-feature",),
        ("com.unity.render-pipelines.universal; renderer features require URP",),
    ),
    "manage_render_pipeline": TaskPurpose(
        "render pipeline quality urp hdrp pink magenta rendering diagnostics 파이프라인 분홍 보라",
        ("validate-urp-render-graph-renderer-feature",),
    ),
    "manage_audio": TaskPurpose("sound mixer routing 소리 오디오 믹서", ("audio-setup-mixers",)),
    "manage_localization": TaskPurpose(
        "translate language locale table 번역 언어 현지화",
        ("localization",),
        ("com.unity.localization; availability must be inspected in the selected project",),
    ),
    "manage_packages": TaskPurpose(
        "install package upm 패키지 설치", ("unity-package-management",)
    ),
    "manage_probuilder": TaskPurpose("model mesh modeling 모델링", (), ("com.unity.probuilder",)),
    "manage_cinemachine": TaskPurpose("camera follow blend 카메라", (), ("com.unity.cinemachine",)),
    "manage_project_auditor": TaskPurpose(
        "project auditor static analysis audit diagnostics 정적 분석 감사",
        ("project-auditor-fixes",),
        ("com.unity.project-auditor; reviewed API 1.0.1, Unity >=2021.3; call availability",),
    ),
    "manage_navigation": TaskPurpose(
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
    "manage_netcode": TaskPurpose(
        "netcode network multiplayer preflight",
        (),
        (
            "capabilities: no optional package required; inspect package availability first",
            "preflight requires com.unity.netcode.gameobjects and an explicit loaded scene_handle; "
            "inspects local configuration without starting a network session",
        ),
    ),
    "manage_input_actions": TaskPurpose(
        "input actions binding inputsystem",
        (),
        (
            "capabilities: no optional package required; inspect package availability first",
            "read and authoring operations require com.unity.inputsystem >=1.7.0 "
            "and an Assets .inputactions path",
            "Authoring operations require idle Edit Mode; edits to existing assets require expected_revision",
        ),
    ),
    "read_response": TaskPurpose("large response truncated retrieve page 응답 페이지"),
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


def _declared_actions(entry: SearchEntry) -> list[str]:
    """Reuse Literal types or Annotated catalog metadata, preserving legacy str admission."""
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


def _words(value: str) -> set[str]:
    return set(re.findall(r"[^\W_]+", value.casefold()))


def search_catalog(
    request: SearchRequest,
    entries: list[SearchEntry],
    *,
    visible_names: set[str] | None = None,
) -> SearchResponse:
    """Rank names, aliases, descriptions and curated intent words without side effects."""
    matches: list[tuple[int, str, SearchResult]] = []
    query = request.query.casefold()
    query_words = _words(query)
    for entry in entries:
        name = entry["name"]
        capability = _CAPABILITIES.get(name)
        purpose = _PURPOSES.get(name, TaskPurpose())
        annotations = ToolAnnotations.model_validate(
            entry.get("kwargs", {}).get("annotations") or {}
        )
        description = entry.get("description") or (capability.description if capability else "")
        aliases = [capability.source] if capability else []
        actions = list(capability.actions) if capability else _declared_actions(entry)
        title = annotations.title or (capability.title if capability else name)
        words = _words(
            " ".join(
                (
                    name,
                    title,
                    description,
                    entry.get("group") or "",
                    purpose.keywords,
                    *aliases,
                    *actions,
                )
            )
        )
        score = len(query_words.intersection(words))
        score += 1000 if query == name.casefold() else 0
        score += 100 if query in name.casefold() else 0
        score += 20 if query in purpose.keywords.casefold() else 0
        if not score:
            continue
        effects = {
            "readOnlyHint": annotations.read_only_hint is True,
            "destructiveHint": annotations.destructive_hint is not False,
            "idempotentHint": annotations.idempotent_hint is True,
            "openWorldHint": annotations.open_world_hint is not False,
        }
        result: SearchResult = {
            "name": name,
            "group": entry.get("group"),
            "title": title[:256],
            "description": description[:1200],
            "aliases": aliases,
            "actions": actions,
            "maturity": entry.get("maturity", "stable"),
            "action_maturity": action_maturity(name),
            "advertised": name in visible_names if visible_names is not None else None,
            "readiness": "unobserved",
            "requirements": list(purpose.requirements),
            "effects": effects,
            "official_skills": [
                {"name": skill, "url": f"{_SKILL_BASE}{skill}/SKILL.md"} for skill in purpose.skills
            ],
        }
        matches.append((-score, name, result))
    matches.sort(key=lambda item: (item[0], item[1]))
    return {
        "scope": "mcp_context" if visible_names is not None else "local_catalog",
        "query": request.query,
        "results": [item[2] for item in matches[: request.limit]],
        "total_matches": len(matches),
        "truncated": len(matches) > request.limit,
        "note": (
            "Suggestions only; search never changes selection, permissions or Unity state. "
            "Advertised reflects this MCP context only, or is null for an offline catalog. "
            "Readiness is unobserved; package requirements and official skills are guidance. "
            "Maturity is support status, independent of enabled state and runtime readiness."
        ),
    }
