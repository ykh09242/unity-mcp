"""Public discovery works from packaged declarations without models or external I/O."""

import json
import socket
import subprocess
from contextlib import contextmanager
from importlib.resources import files
from pathlib import Path

import pytest
from cli.main import cli
from click.testing import CliRunner
from fastmcp import Client, FastMCP
from services.resources import register_all_resources
from services.tools import register_all_tools

_QUERY = "  광고 중인 도구 그룹과 등록된 작업을 확인해 주세요.  "


def test_no_model_assets_are_packaged():
    assert not files("services.registry").joinpath("model").is_dir()


async def _read_pages(request, view, *, limit, family=None):
    offset = 0
    rows = []
    while True:
        options = {"limit": limit, "offset": offset}
        if family is not None:
            options["family"] = family
        page = await request(options)
        assert page["selection"] == "agent" and page["matching"] == "catalog"
        assert page["view"] == view and page["query"] == _QUERY
        assert page["offset"] == offset and len(page[view]) <= limit
        rows.extend(page[view])
        if page["next_offset"] is None:
            assert len(rows) == page["total"]
            return rows
        assert page["next_offset"] == len(rows) > offset
        offset = page["next_offset"]


@pytest.fixture
def previous_install(monkeypatch, tmp_path):
    cache = tmp_path / "previous-install"
    cache.mkdir()
    monkeypatch.setenv("UNITY_MCP_SEARCH_MODEL_DIR", str(cache))
    monkeypatch.setenv("UNITY_MCP_TOOL_PROFILE", "core")
    monkeypatch.delenv("UNITY_MCP_TOOL_GROUPS", raising=False)
    return cache


@pytest.fixture
def discovery_server(previous_install):
    server = FastMCP("offline-bundled-discovery")
    register_all_tools(server)
    register_all_resources(server)
    return server


@contextmanager
def _offline(monkeypatch):
    attempts = []

    def deny(channel):
        def denied(*_args, **_kwargs):
            attempts.append(channel)
            raise AssertionError(f"Offline discovery tried to use {channel}")

        return denied

    with monkeypatch.context() as offline:
        offline.setattr(socket, "getaddrinfo", deny("DNS"))
        offline.setattr(socket.socket, "connect", deny("a network connection"))
        offline.setattr(socket.socket, "connect_ex", deny("a network connection"))
        offline.setattr(subprocess, "Popen", deny("a child process"))
        offline.setattr(Path, "open", deny("an extra file"))
        yield
    assert attempts == []


@pytest.mark.asyncio
async def test_public_search_matches_literal_terms_offline(monkeypatch, discovery_server):
    query = "managed menu item declarations"
    arguments = {"query": query, "route_surfaces": "tools", "limit": 3}
    async with Client(discovery_server, mode="legacy", cache=False) as client:
        with _offline(monkeypatch):
            direct = await client.call_tool("search_unity_tools", arguments)
            managed = await client.call_tool("manage_tools", {"action": "search", **arguments})
            local = CliRunner().invoke(
                cli,
                [
                    "--format",
                    "json",
                    "tool",
                    "search",
                    query,
                    "--route-surfaces",
                    "tools",
                    "--limit",
                    "3",
                ],
            )
    assert local.exit_code == 0, local.output
    for result in (direct.structured_content, managed.structured_content, json.loads(local.stdout)):
        assert result["query"] == query and result["matching"] == "lexical"
        assert not result.get("semantic_model")
        tools = {group["representative"]["tool"] for group in result["route_groups"]}
        assert "list_menu_declarations" in tools


@pytest.mark.asyncio
@pytest.mark.parametrize("surface", ("search_unity_tools", "manage_tools", "cli"))
async def test_public_agent_selection_works_offline(monkeypatch, discovery_server, surface):
    async with Client(discovery_server, mode="legacy", cache=False) as client:

        async def request(options):
            if surface != "cli":
                arguments = {
                    "selection": "agent",
                    "query": _QUERY,
                    "route_surfaces": "tools",
                    **options,
                }
                if surface == "manage_tools":
                    arguments["action"] = "search"
                return (await client.call_tool(surface, arguments)).structured_content

            arguments = [
                "tool",
                "search",
                _QUERY,
                "--selection",
                "agent",
                "--route-surfaces",
                "tools",
            ]
            for key, value in options.items():
                arguments.extend((f"--{key}", str(value)))
            result = CliRunner().invoke(cli, arguments)
            assert result.exit_code == 0, result.output
            return json.loads(result.stdout)

        with _offline(monkeypatch):
            families = await _read_pages(request, "families", limit=20)
            (tool_family,) = (row for row in families if row["family"] == "manage_tools")
            operations = await _read_pages(
                request, "operations", limit=3, family=tool_family["family"]
            )

    actions = {operation["action"] for operation in operations}
    assert len(actions) == len(operations) == tool_family["count"]
    assert {"catalog", "describe", "list_groups"} <= actions
    (list_groups,) = (row for row in operations if row["action"] == "list_groups")
    assert list_groups["invoke"] == {"tool": "manage_tools", "arguments": {"action": "list_groups"}}
    assert list_groups["advertised"] is (True if surface != "cli" else None)
