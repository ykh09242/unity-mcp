"""Separate the menu wording contrast from the ranker's retrieval aliases."""

import json
from pathlib import Path
import sys

import pytest
from fastmcp import Client, FastMCP
from fastmcp.server.middleware import Middleware

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.experiments.menu_discovery_probe import probe
from tools.experiments.blind_tool_factors import (
    EARLIER_MENU_PREFIX,
    MENU_PREFIX,
    earlier_menu_prose,
    install_factor,
)
from services.registry.action_catalog import SEARCH_TERMS_META
from services.registry.capability_catalog import MENU_DECLARATIONS_URI
from services.resources import register_all_resources
from services.tools import register_all_tools


@pytest.mark.asyncio
@pytest.mark.parametrize("profile", ["core", "full"])
@pytest.mark.parametrize("prompt_file", ["blind_tool_tasks.json", "blind_tool_tasks_natural.json"])
async def test_raw_localized_menu_requests_find_each_advertised_binding(
    monkeypatch, profile, prompt_file
):
    monkeypatch.delenv("UNITY_MCP_TOOL_GROUPS", raising=False)
    monkeypatch.setenv("UNITY_MCP_TOOL_PROFILE", profile)
    prompts = json.loads(
        (Path(__file__).resolve().parents[1] / "experiments" / prompt_file).read_text(
            encoding="utf-8"
        )
    )["menu-declaration"]
    server = FastMCP("raw-menu-requests")
    register_all_tools(server)
    register_all_resources(server)
    async with Client(server) as client:
        for locale in ("ko", "ja", "es", "ar", "tr"):
            query = prompts[locale]
            for surface, target in (
                ("tools", "list_menu_declarations"),
                ("resources", "mcpforunity://menu-items"),
                ("both", "list_menu_declarations"),
            ):
                reply = await client.call_tool(
                    "search_unity_tools", {"query": query, "limit": 3, "route_surfaces": surface}
                )
                result = reply.structured_content
                assert result["query"] == result["matched_query"] == query
                groups = [group["representative"] for group in result["route_groups"]]
                assert target in [r.get("tool") or r.get("uri") for r in groups], (locale, surface)
                assert "list_menu_declarations" in [r["name"] for r in result["results"]]
                assert result["recommended_action"] == "inspect_candidates"


def test_wording_preserves_query_arguments_and_equally_worded_unrelated_descriptions():
    payload = {
        "query": MENU_PREFIX,
        "tools": [
            {"name": "other", "description": MENU_PREFIX},
            {"name": "execute_menu_item", "arguments": {"menu_path": MENU_PREFIX}},
        ],
    }
    assert earlier_menu_prose(payload) == payload


@pytest.mark.asyncio
@pytest.mark.parametrize("tool", ["search_unity_tools", "manage_tools"])
async def test_ranking_factor_withholds_only_menu_search_metadata(monkeypatch, tool):
    monkeypatch.setenv("UNITY_MCP_TOOL_PROFILE", "core")
    listings = []

    class CaptureResources(Middleware):
        async def on_list_resources(self, context, call_next):
            resources = await call_next(context)
            listings.append({str(row.uri): row.model_dump() for row in resources})
            return resources

    server = FastMCP("menu-ranking-metadata")
    register_all_tools(server)
    register_all_resources(server)
    install_factor(server, "menu-ranking-baseline")
    server.middleware.insert(0, CaptureResources())

    query = "照明ウィンドウはどのメニューに登録されている？"
    args = {"query": query, "limit": 3, "route_surfaces": "resources"}
    if tool == "manage_tools":
        args["action"] = "search"
    async with Client(server, mode="legacy", cache=False) as client:
        await client.list_resources()
        response = await client.call_tool(tool, args)
        await client.list_resources()
    assert response.structured_content["matched_query"] == query
    assert len(listings) == 3
    before = listings[0]
    assert before == listings[2]
    assert before[MENU_DECLARATIONS_URI]["meta"].pop(SEARCH_TERMS_META)
    assert listings[1] == before


@pytest.mark.asyncio
async def test_menu_lookup_respects_explicit_search_and_host_filters(monkeypatch):
    monkeypatch.setenv("UNITY_MCP_TOOL_PROFILE", "core")
    server = FastMCP("menu-lookup-controls")
    register_all_tools(server)
    register_all_resources(server)
    async with Client(server) as client:
        for override in (
            {"catalog_query": "find assets by name"},
            {"intent": "edit"},
            {"required_values": ["nodeCount"]},
            {"host_tool_names": ["manage_tools"]},
        ):
            args = {
                "action": "search",
                "query": "메뉴 경로를 알려줘.",
                "route_surfaces": "tools",
                **override,
            }
            result = (await client.call_tool("manage_tools", args)).structured_content
            assert all(
                group["representative"].get("tool") != "list_menu_declarations"
                for group in result["route_groups"]
            )
            assert "list_menu_declarations" not in [tool["name"] for tool in result["results"]]


@pytest.mark.asyncio
@pytest.mark.parametrize("surface", ["tools", "resources", "both"])
async def test_menu_factors_change_only_the_assigned_surface(monkeypatch, surface):
    monkeypatch.delenv("UNITY_MCP_TOOL_GROUPS", raising=False)
    monkeypatch.setenv("UNITY_MCP_TOOL_PROFILE", "core")
    original = "Frame Debugger 어디 메뉴에 등록돼 있어?"
    alias = "find registered editor menu command path"
    result = await probe(original, alias, surface)
    assert result["search"]["query"] == original
    assert result["search"]["catalog_query"] == alias
    bb, bc, cb, cc = result["arms"]
    assert bb["tool_count"] == bc["tool_count"] == cb["tool_count"] == cc["tool_count"]
    assert bb["advertised_menu_description"] == bc["advertised_menu_description"]
    assert cb["advertised_menu_description"] == cc["advertised_menu_description"]
    assert bb["advertised_menu_description"] != cb["advertised_menu_description"]
    assert bb["top3"] == cb["top3"] and bc["top3"] == cc["top3"]
    assert cc["inventory_rank"] == 1
    if surface != "resources":
        assert bb["inventory_rank"] != 1
    assert (
        cc["top3"][0]
        == {
            "tools": ["list_menu_declarations"],
            "resources": ["mcpforunity://menu-items"],
            "both": ["list_menu_declarations", "mcpforunity://menu-items"],
        }[surface]
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "query", ["find asset by name", "find asset items by name", "find project items by name"]
)
async def test_retrieval_phrase_does_not_promote_unrelated_inventory(monkeypatch, query):
    monkeypatch.setenv("UNITY_MCP_TOOL_PROFILE", "core")
    result = await probe(query, None, "both")
    assert all(row["top3"] == result["arms"][0]["top3"] for row in result["arms"])


@pytest.mark.asyncio
async def test_exact_path_still_ranks_declaration_inspection_first(monkeypatch):
    monkeypatch.setenv("UNITY_MCP_TOOL_PROFILE", "core")
    result = await probe("inspect exact menu path", None, "tools")
    assert all(row["top3"][0] == ["execute_menu_item.inspect"] for row in result["arms"])


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["legacy", "2026-07-28"])
async def test_wire_menu_wording_keeps_all_other_discovery_fields(monkeypatch, mode):
    monkeypatch.setenv("UNITY_MCP_TOOL_PROFILE", "core")
    requests = [
        (
            "manage_tools",
            {"action": "describe", "tool": "execute_menu_item", "operation": "inspect"},
        ),
        ("manage_tools", {"action": "catalog", "group": "core", "limit": 20}),
        ("manage_tools", {"action": "catalog", "tool": "execute_menu_item"}),
        ("search_unity_tools", {"query": "execute_menu_item", "limit": 3}),
        ("manage_tools", {"action": "search", "query": "execute_menu_item", "limit": 3}),
    ]
    arms = []
    for baseline in (False, True):
        server = FastMCP("menu-wording")
        register_all_tools(server)
        register_all_resources(server)
        if baseline:
            install_factor(server, "menu-wording-baseline")
        async with Client(server, mode=mode, cache=False) as client:
            listings = [tool.model_dump(mode="json") for tool in await client.list_tools()]
            rows = [json.dumps(listings)]
            for name, args in requests:
                result = await client.call_tool(name, args)
                assert json.loads(result.content[0].text) == result.structured_content
                rows.append(
                    json.dumps(
                        [
                            result.structured_content,
                            [item.model_dump(mode="json") for item in result.content[1:]],
                        ]
                    )
                )
            arms.append(rows)
    for candidate, baseline in zip(*arms, strict=True):
        assert MENU_PREFIX in candidate
        assert candidate.replace(MENU_PREFIX, EARLIER_MENU_PREFIX) == baseline
