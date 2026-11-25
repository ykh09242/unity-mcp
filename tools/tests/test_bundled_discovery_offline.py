"""Public agent selection uses declarations without loading either bundled encoder."""

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
from services.registry import semantic_coverage, semantic_encoder
from services.resources import register_all_resources
from services.tools import register_all_tools

_QUERY = "  광고 중인 도구 그룹과 등록된 작업을 확인해 주세요.  "
_MODELS = {"static-multilingual-v1.q8", "potion-256.q2"}


def test_only_pretrained_model_assets_are_packaged():
    model_dir = files("services.registry").joinpath("model")
    assert {item.name for item in model_dir.iterdir()} == _MODELS | {
        "LICENSE.txt",
        "README.md",
        "POTION-LICENSE.txt",
        "POTION.md",
    }


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
def _offline(monkeypatch, previous_install, *, deny_models=False):
    attempts = []

    def deny(channel):
        def denied(*_args, **_kwargs):
            attempts.append(channel)
            raise AssertionError(f"Offline discovery tried to use {channel}")

        return denied

    open_path = Path.open

    def open_without_weights(path, *args, **kwargs):
        if path.is_relative_to(previous_install) or (deny_models and path.name in _MODELS):
            deny(path.name)()
        return open_path(path, *args, **kwargs)

    with monkeypatch.context() as offline:
        offline.setattr(socket, "getaddrinfo", deny("DNS"))
        offline.setattr(socket.socket, "connect", deny("a network connection"))
        offline.setattr(socket.socket, "connect_ex", deny("a network connection"))
        offline.setattr(subprocess, "Popen", deny("a child process"))
        offline.setattr(Path, "open", open_without_weights)
        if deny_models:
            offline.setattr(semantic_encoder, "_model", deny("the WordPiece encoder"))
            offline.setattr(semantic_coverage, "_load", deny("the Unigram encoder"))
        yield
    assert attempts == []


@pytest.mark.asyncio
async def test_public_search_uses_only_packaged_assets(
    monkeypatch, previous_install, discovery_server
):
    query = "Unity Editor에 등록된 MenuItem 선언 목록을 읽어 주세요."
    arguments = {"query": query, "route_surfaces": "tools", "limit": 3}
    async with Client(discovery_server, mode="legacy", cache=False) as client:
        with _offline(monkeypatch, previous_install):
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
        assert result["query"] == query and result["semantic_model"]
        tools = {group["representative"]["tool"] for group in result["route_groups"]}
        assert "list_menu_declarations" in tools


@pytest.mark.asyncio
@pytest.mark.parametrize("surface", ("search_unity_tools", "manage_tools", "cli"))
async def test_public_agent_selection_requires_no_encoder(
    monkeypatch, previous_install, discovery_server, surface
):
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

        with _offline(monkeypatch, previous_install, deny_models=True):
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
