"""Separate the menu wording contrast from the ranker's retrieval aliases."""

import json
from pathlib import Path
import sys

import pytest
from fastmcp import Client, FastMCP

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.experiments.menu_discovery_probe import probe
from tools.experiments.blind_tool_factors import (
    EARLIER_MENU_PREFIX,
    MENU_PREFIX,
    earlier_menu_prose,
    install_factor,
)
from services.resources import register_all_resources
from services.tools import register_all_tools


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
