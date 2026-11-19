"""Exercise the public search schemas after the calling agent chooses evidence fields."""

import json

from click.testing import CliRunner
from fastmcp import Client, FastMCP
import pytest

from cli.main import cli


PIXELS = ["render_size.width", "render_size.height"]
COUNTS = ["nodeCount", "edgeCount"]


def targets(payload):
    return {
        (route["kind"], route.get("tool") or route["name"], route.get("operation"))
        for route in payload["routes"]
    }


@pytest.mark.asyncio
async def test_mcp_first_search_exposes_selectors_and_reaches_all_advertised_read_surfaces(
    monkeypatch,
):
    from services.tools import register_all_tools
    from services.resources import register_all_resources

    monkeypatch.setenv("UNITY_MCP_TOOL_PROFILE", "full")
    server = FastMCP("declared-observations")
    register_all_tools(server, project_scoped_tools=False)
    register_all_resources(server, project_scoped_tools=False)
    async with Client(server) as client:
        before = {t.name: t for t in await client.list_tools()}
        properties = before["manage_tools"].input_schema["properties"]
        assert {"editor_live", "asset_file", "imported_asset", "compile_job"} == set(
            next(t["enum"] for t in properties["observation_source"]["anyOf"] if "enum" in t)
        )
        values = next(t for t in properties["required_values"]["anyOf"] if t.get("type") == "array")
        assert values["minItems"] == 1 and values["maxItems"] <= 32
        assert set(PIXELS + COUNTS) <= set(values["items"]["enum"])
        for source, facts, expected in [
            (
                "editor_live",
                PIXELS,
                {
                    ("tool", "manage_editor", "get_game_view_size"),
                    ("tool", "get_game_view_size", None),
                    ("resource", "game_view_size", None),
                },
            ),
            (
                "asset_file",
                COUNTS,
                {
                    ("tool", "manage_shader", "inspect_graph"),
                    ("tool", "inspect_shader_graph", None),
                    ("resource_template", "shader_graph_structure", None),
                },
            ),
        ]:
            result = (
                await client.call_tool(
                    "manage_tools",
                    {
                        "action": "search",
                        "observation_source": source,
                        "required_values": facts,
                    },
                )
            ).structured_content
            assert targets(result) == expected
            assert result["matching"] == "observation" and result["matched_query"] is None
            assert result["match_basis"] == "declared_observation"
            assert all(r["readiness"] == "unobserved" and r["advertised"] for r in result["routes"])
        assert {t.name for t in await client.list_tools()} == before.keys()


@pytest.mark.parametrize(
    "query",
    [
        None,
        "What are the current pixel dimensions of the game display?",
        "현재 게임 화면이 가로세로 몇 픽셀인지 알려줘",
        "ゲーム画面は今、横と縦が何ピクセルですか",
        "¿Cuántos píxeles de ancho y de alto tiene ahora la vista del juego?",
        "كم يبلغ عرض شاشة اللعبة الحالية وارتفاعها بالبكسل؟",
        "Mikä on pelinäkymän nykyinen leveys ja korkeus pikseleinä?",
    ],
)
def test_cli_structured_first_search_is_independent_of_optional_original_text(query):
    response = CliRunner().invoke(
        cli,
        [
            "--format",
            "json",
            "tool",
            "search",
            *([query] if query else []),
            "--observation-source",
            "editor_live",
            "--required-value",
            PIXELS[0],
            "--required-value",
            PIXELS[1],
        ],
    )
    assert response.exit_code == 0, response.output
    result = json.loads(response.stdout)
    assert result["query"] == query
    assert targets(result) == {
        ("tool", "get_game_view_size", None),
        ("tool", "manage_editor", "get_game_view_size"),
        ("resource", "game_view_size", None),
    }
    assert all(r["advertised"] is None for r in result["routes"])


@pytest.mark.parametrize(
    ("source", "facts"),
    [
        ("editor_live", COUNTS),
        ("asset_file", ["nodeCount", "contents"]),
        ("asset_file", ["contents", "encodedContents"]),
        ("asset_file", ["unsaved_graph_structure"]),
    ],
)
def test_structural_miss_cannot_substitute_best_lexical_match(source, facts):
    args = [
        "--format",
        "json",
        "tool",
        "search",
        "inspect_shader_graph",
        "--observation-source",
        source,
    ]
    for value in facts:
        args += ["--required-value", value]
    response = CliRunner().invoke(cli, args)
    assert response.exit_code == 0, response.output
    result = json.loads(response.stdout)
    assert result["routes"] == result["results"] == []
    assert result["match_basis"] == "none"


def test_structured_search_excludes_unadvertised_tool_and_undeclared_action():
    from services.registry.task_search import SearchRequest, search_catalog
    from services.registry.action_catalog import GAME_VIEW_OBSERVATION, OBSERVATION_META

    entries = [
        {
            "name": "hidden_read",
            "group": None,
            "kwargs": {"meta": {OBSERVATION_META: GAME_VIEW_OBSERVATION}},
        },
        {
            "name": "manage_editor",
            "group": "core",
            "kwargs": {},
            "input_schema": {"properties": {"action": {"const": "pause"}}},
        },
    ]
    result = search_catalog(
        SearchRequest(required_values=PIXELS), entries, visible_names={"manage_editor"}
    )
    assert result["routes"] == result["results"] == []
    assert "hidden_read" not in result["navigation"]["server_tools"]


def test_wire_fact_vocabulary_matches_the_reviewed_observations():
    from typing import get_args
    from services.registry.action_catalog import ObservationValue, TOOL_GUIDANCE

    observations = [
        guidance.observation
        for tool in TOOL_GUIDANCE.values()
        for guidance in tool.actions.values()
        if guidance.observation
    ]
    from services.registry.action_catalog import COMPILE_JOB_OBSERVATION, EDITOR_STATE_OBSERVATION

    observations.extend([COMPILE_JOB_OBSERVATION, EDITOR_STATE_OBSERVATION])
    assert set(get_args(ObservationValue)) == {
        value for record in observations for value in [*record["values"], *record["unavailable"]]
    }


@pytest.mark.parametrize(
    "arguments",
    [
        {},
        {"intent": "observe"},
        {"required_values": []},
        {"required_values": ["unknown"]},
        {"required_values": ["nodeCount"] * 33},
        {"observation_source": "live-ish"},
    ],
)
def test_missing_or_invalid_selector_fails_before_loading_inventory(arguments):
    from pydantic import ValidationError
    from services.registry.task_search import SearchRequest

    with pytest.raises(ValidationError):
        SearchRequest(**arguments)


def test_fact_only_search_accepts_only_declared_evidence_including_resource_only_inventory():
    from services.registry.action_catalog import GRAPH_FILE_OBSERVATION, OBSERVATION_META
    from services.registry.task_search import SearchRequest, search_catalog

    resources = [
        {
            "name": "saved",
            "uri": "mcpforunity://saved",
            "description": "Read graph",
            "kwargs": {"meta": {OBSERVATION_META: GRAPH_FILE_OBSERVATION}},
        }
    ]
    result = search_catalog(
        SearchRequest(required_values=COUNTS), [], visible_names=set(), resources=resources
    )
    assert targets(result) == {("resource", "saved", None)}
    assert result["results"] == [] and result["next_action"] == "inspect_candidates"
    assert result["routes"][0]["readiness"] == "unobserved"


def test_source_only_search_reports_matching_actions_and_respects_intent():
    from services.registry.task_search import SearchRequest, search_catalog
    from services.registry import get_registered_tools
    from services.tools import manage_shader

    entries = [e for e in get_registered_tools() if e["func"] == manage_shader.manage_shader]
    result = search_catalog(
        SearchRequest(observation_source="asset_file", intent="observe"), entries
    )
    assert result["results"][0]["actions"] == ["read", "inspect_graph"]
    assert targets(result) == {
        ("tool", "manage_shader", "read"),
        ("tool", "manage_shader", "inspect_graph"),
    }
    other = search_catalog(SearchRequest(observation_source="asset_file", intent="edit"), entries)
    assert other["routes"] == other["results"] == []


@pytest.mark.asyncio
async def test_search_reports_wire_evidence_with_its_binding_and_prerequisites(monkeypatch):
    from services.resources import register_all_resources
    from services.tools import register_all_tools

    monkeypatch.setenv("UNITY_MCP_TOOL_PROFILE", "full")
    server = FastMCP("conditional-evidence")
    register_all_tools(server, project_scoped_tools=False)
    register_all_resources(server, project_scoped_tools=False)
    async with Client(server) as client:

        async def groups(value, **fields):
            response = await client.call_tool(
                "search_unity_tools", {"required_values": [value], **fields}
            )
            return response.structured_content["route_groups"]

        (pointer,) = await groups("pointer_hit_order")
        route = pointer["representative"]
        assert (route["tool"], route["arguments"]) == (
            "manage_ugui",
            {"action": "diagnose_raycast", "allow_custom_filters": False},
        )
        assert route["required_parameters"] == ["target", "screen_position"]
        assert {"status=observed", "policy.native_query_attempted=true"} <= set(
            route["observation"]["conditions"].split()
        )
        assert route["observation"]["wire_paths"]["pointer_hit_order"] == "hits[].rank"
        (scene,) = await groups("active_scene.name", route_surfaces="tools")
        assert [r["tool"] for r in [scene["representative"], *scene["alternatives"]]] == [
            "scene_inspect",
            "manage_scene",
        ]
        assert scene["representative"]["observation"]["wire_paths"]["active_scene.name"] == "name"
        (state,) = await groups("compilation.is_compiling")
        assert state["representative"]["uri"] == "mcpforunity://editor/state"
        assert "staleness.is_stale=false" in state["representative"]["observation"]["conditions"]
        assert await groups("compile_job.outcome", observation_source="editor_live") == []
        (job,) = await groups("compile_job.outcome", observation_source="compile_job")
        assert job["representative"]["tool"] == "get_compile_job"
        assert job["representative"]["required_parameters"] == ["compile_id"]
