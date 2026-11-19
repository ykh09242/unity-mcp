"""Grouped search exposes executable bindings without spending slots on equivalent reads."""

import json

from click.testing import CliRunner
from fastmcp import Client
import pytest

from cli.main import cli
from services.registry import get_registered_resources, get_registered_tools
from services.registry.task_search import SearchRequest, search_catalog
from services.resources import editor_observations as observation_resources
from services.tools import editor_observations, manage_editor, manage_shader, manage_ui
from tests.test_editor_observation_resources import (
    GAME_VIEW,
    GRAPH_PATH,
    GRAPH_TEMPLATE,
    TARGET,
    observation_server,
)
from tests.test_http_command_outcome import command_hub  # pylint: disable=unused-import


def route_key(route):
    return (
        route.get("uri")
        or route.get("uri_template")
        or (
            route["tool"],
            route.get("operation"),
        )
    )


@pytest.fixture
def observation_inventory():
    tool_modules = {
        m.__name__ for m in (manage_editor, manage_shader, manage_ui, editor_observations)
    }
    tools = [entry for entry in get_registered_tools() if entry["func"].__module__ in tool_modules]
    resources = [
        entry
        for entry in get_registered_resources()
        if entry["func"].__module__ == observation_resources.__name__
    ]
    return tools, resources


@pytest.mark.parametrize("limit", [1, 2])
@pytest.mark.parametrize(
    "selectors,first,alternatives,second,raw",
    [
        (
            {"query": "current Game View render size"},
            ("get_game_view_size", None),
            {("manage_editor", "get_game_view_size"), GAME_VIEW},
            ("manage_editor", "restore_game_view_size"),
            [("manage_editor", "get_game_view_size"), GAME_VIEW],
        ),
        (
            {"observation_source": "asset_file"},
            ("inspect_shader_graph", None),
            {("manage_shader", "inspect_graph"), GRAPH_TEMPLATE},
            ("manage_shader", "read"),
            [("inspect_shader_graph", None), ("manage_shader", "inspect_graph")],
        ),
    ],
)
def test_equivalent_routes_take_one_slot_when_high_scoring_routes_repeat(
    observation_inventory, selectors, first, alternatives, second, raw, limit
):
    tools, resources = observation_inventory

    result = search_catalog(SearchRequest(limit=limit, **selectors), tools, resources=resources)

    assert [route_key(group["representative"]) for group in result["route_groups"]] == [
        first,
        second,
    ][:limit]
    assert {route_key(route) for route in result["route_groups"][0]["alternatives"]} == alternatives
    assert result["total_route_groups"] >= 2
    assert result["route_groups_truncated"] == (result["total_route_groups"] > limit)
    assert [route_key(route) for route in result["routes"]] == raw[:limit]


@pytest.mark.parametrize(
    "surface,expected,alternatives",
    [
        ("tools", ("inspect_shader_graph", None), [("manage_shader", "inspect_graph")]),
        ("resources", GRAPH_TEMPLATE, []),
    ],
)
def test_cli_group_selects_callers_surface_and_preserves_legacy_matches(
    surface, expected, alternatives
):
    result = CliRunner().invoke(
        cli,
        [
            "--format",
            "json",
            "tool",
            "search",
            "--required-value",
            "nodeCount",
            "--route-surfaces",
            surface,
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    [group] = payload["route_groups"]
    assert payload["total_route_groups"] == 1
    assert route_key(group["representative"]) == expected
    assert [route_key(route) for route in group["alternatives"]] == alternatives
    assert {route_key(route) for route in payload["routes"]} == {
        ("inspect_shader_graph", None),
        ("manage_shader", "inspect_graph"),
        GRAPH_TEMPLATE,
    }
    representative = group["representative"]
    assert representative["required_parameters"] == [
        "encoded_path" if surface == "resources" else "path"
    ]
    assert representative["optional_parameters"] == ["page_size", "page_number"]


def test_lexical_exact_resource_can_seed_its_equivalent_tools_only_group(observation_inventory):
    tools, resources = observation_inventory

    result = search_catalog(
        SearchRequest(query=GAME_VIEW, route_surfaces="tools", limit=1), tools, resources=resources
    )

    [group] = result["route_groups"]
    assert route_key(group["representative"]) == ("get_game_view_size", None)
    assert route_key(result["routes"][0]) == GAME_VIEW


def test_unrelated_reads_with_the_same_fact_names_stay_independent(observation_inventory):
    tools, resources = observation_inventory
    graph_record = next(resource for resource in resources if resource["uri"] == GRAPH_TEMPLATE)
    another_read = {
        **graph_record,
        "uri": "mcpforunity://shader/graph-history/{encoded_path}{?page_size,page_number}",
        "name": "archived_graph_structure",
    }

    result = search_catalog(
        SearchRequest(required_values=["nodeCount"], limit=2),
        tools,
        resources=[*resources, another_read],
    )

    assert result["total_route_groups"] == 2
    assert [route_key(group["representative"]) for group in result["route_groups"]] == [
        ("inspect_shader_graph", None),
        another_read["uri"],
    ]


def test_conflicting_evidence_on_the_exact_resource_is_a_separate_choice(observation_inventory):
    tools, resources = observation_inventory
    [original] = [resource for resource in resources if resource["uri"] == GRAPH_TEMPLATE]
    conflicting = {
        **original,
        "kwargs": {
            "meta": {
                "io.github.ykh09242.unity-mcp/observation": {
                    "source": "asset_file",
                    "values": ["contents", "nodeCount"],
                    "unavailable": ["edgeCount"],
                },
            },
        },
    }

    result = search_catalog(
        SearchRequest(required_values=["nodeCount"], limit=2), tools, resources=[conflicting]
    )

    assert result["total_route_groups"] == 2
    tool_group, independent_resource = result["route_groups"]
    assert [route_key(route) for route in tool_group["alternatives"]] == [
        ("manage_shader", "inspect_graph")
    ]
    assert route_key(independent_resource["representative"]) == GRAPH_TEMPLATE
    assert independent_resource["alternatives"] == []


@pytest.mark.parametrize(
    "selectors",
    [
        {"observation_source": "editor_live", "required_values": ["nodeCount"]},
        {"observation_source": "asset_file", "required_values": ["unsaved_graph_structure"]},
        {"intent": "edit", "required_values": ["render_size.width"]},
    ],
)
def test_grouping_does_not_widen_evidence_scope(observation_inventory, selectors):
    tools, resources = observation_inventory

    result = search_catalog(SearchRequest(**selectors), tools, resources=resources)

    assert result["route_groups"] == []
    assert result["total_route_groups"] == 0


def test_recommendation_accounts_for_callers_visible_surface(observation_inventory):
    tools, resources = observation_inventory
    result = search_catalog(
        SearchRequest(observation_source="imported_asset", route_surfaces="resources"),
        tools,
        resources=resources,
    )
    assert result["routes"] and not result["route_groups"]
    assert result["next_action"] == result["recommended_action"] == "browse_catalog"


@pytest.mark.asyncio
async def test_live_search_requires_an_advertised_route_and_can_execute_its_read(
    command_hub, monkeypatch
):
    async with Client(observation_server(monkeypatch), mode="legacy", cache=False) as client:
        args = {
            "action": "search",
            "required_values": ["nodeCount", "edgeCount"],
            "route_surfaces": "tools",
            "limit": 1,
            **TARGET,
        }
        visible_before = {tool.name for tool in await client.list_tools()}
        result = (await client.call_tool("manage_tools", args)).structured_content
        assert command_hub.sent == []
        assert {tool.name for tool in await client.list_tools()} == visible_before
        [group] = result["route_groups"]
        best = group["representative"]
        assert route_key(best) == ("inspect_shader_graph", None)
        read = await client.call_tool(
            best["tool"], {"path": GRAPH_PATH, "page_size": 3, "page_number": 2, **TARGET}
        )
        assert read.structured_content["success"]
        [dispatch] = command_hub.sent
        assert dispatch["name"] == "manage_shader"
        assert dispatch["params"] == {
            "action": "inspect_graph",
            "path": GRAPH_PATH,
            "pageSize": 3,
            "pageNumber": 2,
        }
        command_hub.sent.clear()

        await client.call_tool("manage_tools", {"action": "deactivate", "tool": "manage_shader"})
        result = (
            await client.call_tool("manage_tools", {**args, "route_surfaces": "both"})
        ).structured_content
        assert result["route_groups"] == []
        assert route_key(result["routes"][0]) == GRAPH_TEMPLATE
        assert command_hub.sent == []


@pytest.mark.parametrize(
    ("selectors", "reason"),
    [
        ({"required_values": ["contents", "encodedContents"]}, "incompatible_combination"),
        ({"required_values": ["nodeCount", "contents"]}, "incompatible_combination"),
        ({"required_values": ["nodeCount"], "observation_source": "editor_live"}, "wrong_source"),
        ({"required_values": ["active_scene.path"]}, "undeclared_evidence"),
        (
            {"observation_source": "imported_asset", "route_surfaces": "resources"},
            "no_visible_route",
        ),
        ({"query": "검색결과없는어휘"}, "lexical_miss"),
    ],
)
def test_no_match_separates_coverage_source_encoding_and_text(
    observation_inventory, selectors, reason
):
    tools, resources = observation_inventory
    result = search_catalog(SearchRequest(**selectors), tools, resources=resources)
    assert result["route_groups"] == []
    assert result["no_match"]["reason"] == reason


@pytest.mark.parametrize(
    ("surfaces", "binding", "path"),
    [
        ("tools", ("inspect_shader_graph", None), "path"),
        ("resources", GRAPH_TEMPLATE, "encoded_path"),
    ],
)
def test_declared_exclusion_has_bounded_scoped_alternatives(
    observation_inventory, surfaces, binding, path
):
    tools, resources = observation_inventory
    search = SearchRequest(
        required_values=["unsaved_graph_structure"],
        observation_source="editor_live",
        route_surfaces=surfaces,
        limit=1,
    )
    names = {t["name"] for t in tools}
    diagnosis = search_catalog(search, tools, resources=resources, visible_names=names)["no_match"]
    assert diagnosis["reason"] == "explicitly_unavailable"
    assert diagnosis["excluded_values"] == ["unsaved_graph_structure"]
    assert diagnosis["undeclared_values"] == []
    (alternative,) = diagnosis["alternatives"]
    assert route_key(alternative) == binding
    assert alternative["required_parameters"] == [path]
    assert alternative["observation"]["source"] == "asset_file"
    assert {"nodes", "edges"} <= set(alternative["observation"]["values"])
    assert alternative["readiness"] == "unobserved" and alternative["advertised"] is True
    assert diagnosis["alternatives_truncated"] is (surfaces == "tools")
    hidden = search_catalog(search, tools, resources=resources, visible_names=set())["no_match"]
    assert hidden["reason"] == "undeclared_evidence"
    assert hidden["alternatives"] == []
    assert hidden["excluded_values"] == []


@pytest.mark.asyncio
async def test_no_match_wire_and_cli_text_preserve_alternative_source_and_binding(monkeypatch):
    args = {
        "required_values": ["unsaved_graph_structure"],
        "observation_source": "editor_live",
        "limit": 1,
        "route_surfaces": "tools",
    }
    async with Client(observation_server(monkeypatch), mode="legacy") as client:
        wire = (await client.call_tool("search_unity_tools", args)).structured_content
        assert wire["no_match"]["reason"] == "explicitly_unavailable"
    flags = [
        "tool",
        "search",
        "--required-value",
        "unsaved_graph_structure",
        "--observation-source",
        "editor_live",
        "--limit",
        "1",
        "--route-surfaces",
        "tools",
    ]
    result = CliRunner().invoke(cli, ["--format", "json", *flags])
    assert result.exit_code == 0, result.output
    diagnosis = json.loads(result.stdout)["no_match"]
    assert diagnosis["reason"] == wire["no_match"]["reason"]
    assert (
        diagnosis["alternatives"][0]["observation"]
        == wire["no_match"]["alternatives"][0]["observation"]
    )
    assert diagnosis["alternatives"][0]["advertised"] is None
    text = CliRunner().invoke(cli, ["--format", "text", *flags])
    assert text.exit_code == 0, text.output
    assert "explicitly_unavailable" in text.output
    assert "inspect_shader_graph [asset_file]" in text.output
    assert "nodeCount" in text.output and "Requires: path" in text.output
    assert "Readiness: unobserved" in text.output
