"""Public search must work without a provisioned cache, downloads, or worker processes."""

import json
from pathlib import Path
import socket
import subprocess

from click.testing import CliRunner
from fastmcp import Client, FastMCP
import pytest

from cli.main import cli
from services.resources import register_all_resources
from services.tools import register_all_tools


@pytest.mark.asyncio
async def test_public_search_uses_only_packaged_assets(monkeypatch, tmp_path):
    prompts = Path(__file__).resolve().parents[1] / "experiments" / "blind_tool_tasks.json"
    query = json.loads(prompts.read_bytes())["menu-declaration"]["ko"]
    legacy_cache = tmp_path / "previous-install"
    legacy_cache.mkdir()
    monkeypatch.setenv("UNITY_MCP_SEARCH_MODEL_DIR", str(legacy_cache))
    monkeypatch.setenv("UNITY_MCP_TOOL_PROFILE", "core")
    monkeypatch.delenv("UNITY_MCP_TOOL_GROUPS", raising=False)
    server = FastMCP("offline-bundled-discovery")
    register_all_tools(server)
    register_all_resources(server)
    attempts = []

    def denied(*args, **kwargs):
        attempts.append("external I/O")
        raise AssertionError("Search tried to resolve a host, connect, or start a worker")

    open_path = Path.open

    def open_packaged(path, *args, **kwargs):
        if path.is_relative_to(legacy_cache):
            attempts.append("legacy cache")
            raise AssertionError("Search tried to use a separately provisioned model")
        return open_path(path, *args, **kwargs)

    async with Client(server, mode="legacy", cache=False) as client:
        with monkeypatch.context() as offline:
            offline.setattr(socket, "getaddrinfo", denied)
            offline.setattr(socket.socket, "connect", denied)
            offline.setattr(subprocess, "Popen", denied)
            offline.setattr(Path, "open", open_packaged)
            arguments = {"query": query, "route_surfaces": "tools", "limit": 3}
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
    assert attempts == []
    for result in (direct.structured_content, managed.structured_content, json.loads(local.stdout)):
        assert result["query"] == query and result["semantic_model"]
        tools = [group["representative"]["tool"] for group in result["route_groups"]]
        assert "list_menu_declarations" in tools
