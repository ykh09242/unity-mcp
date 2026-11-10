"""Bounded physics query admission and CLI forwarding, without Unity dispatch."""

import importlib
import os
import subprocess
import sys
import textwrap
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from click.testing import CliRunner

from services.tools.manage_physics import manage_physics


@pytest.fixture
def query_dispatch(monkeypatch):
    receiver = AsyncMock(return_value={"success": True, "data": {"buffer_saturated": True}})
    selection = AsyncMock(return_value="fixture")
    monkeypatch.setattr("services.tools.manage_physics.get_unity_instance_from_context", selection)
    monkeypatch.setattr("services.tools.manage_physics.send_with_unity_instance", receiver)
    return selection, receiver


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["overlap", "raycast_all"])
@pytest.mark.parametrize("limit", [1, 200, 1000])
async def test_query_limit_is_forwarded_without_losing_native_metadata(
    query_dispatch, action, limit
):
    _, receiver = query_dispatch
    result = await manage_physics(SimpleNamespace(), action=action, max_results=limit)
    assert result == {"success": True, "data": {"buffer_saturated": True}}
    assert receiver.await_args.args[3] == {"action": action, "max_results": limit}


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["overlap", "raycast_all"])
@pytest.mark.parametrize("limit", [0, -1, 1001, True, 1.0, 1.5, "2"])
async def test_invalid_query_limit_fails_before_selecting_or_dispatching_unity(
    query_dispatch, action, limit
):
    selection, receiver = query_dispatch
    result = await manage_physics(SimpleNamespace(), action=action, max_results=limit)
    assert result["success"] is False
    assert "max_results" in result["message"]
    selection.assert_not_awaited()
    receiver.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["overlap", "raycast_all", "raycast"])
async def test_omitted_query_limit_preserves_wire_defaults(query_dispatch, action):
    _, receiver = query_dispatch
    await manage_physics(SimpleNamespace(), action=action)
    assert receiver.await_args.args[3] == {"action": action}


@pytest.fixture
def cli_dispatch(monkeypatch):
    module = importlib.import_module("cli.commands.physics")
    calls = []
    monkeypatch.setattr(module, "get_config", lambda: SimpleNamespace(format="json"))
    monkeypatch.setattr(
        module,
        "run_command",
        lambda tool, params, config: calls.append((tool, params)) or {"success": True},
    )
    return module.physics, calls


CLI_QUERIES = [
    ["overlap", "--shape", "sphere", "--position", "0,0,0", "--size", "2"],
    ["raycast-all", "--origin", "0,0,0", "--direction", "1,0,0"],
]


@pytest.mark.parametrize("arguments", CLI_QUERIES)
@pytest.mark.parametrize("limit", [1, 200, 1000])
def test_cli_query_limit_forwards(cli_dispatch, arguments, limit):
    command, calls = cli_dispatch
    result = CliRunner().invoke(command, [*arguments, "--max-results", str(limit)])
    assert result.exit_code == 0, result.output
    assert calls[0][1]["max_results"] == limit


@pytest.mark.parametrize("arguments", CLI_QUERIES)
@pytest.mark.parametrize("limit", ["0", "1001", "1.5", "true"])
def test_cli_query_limit_rejected_before_dispatch(cli_dispatch, arguments, limit):
    command, calls = cli_dispatch
    result = CliRunner().invoke(command, [*arguments, "--max-results", limit])
    assert result.exit_code == 2
    assert "--max-results" in result.output
    assert calls == []


def test_real_sdk_query_alias_preserves_strict_capacity_and_native_metadata():
    code = textwrap.dedent("""
        import importlib
        import anyio
        from fastmcp import Client, FastMCP
        from services.registry.capability_adapter import register_capability
        from services.registry.capability_catalog import CAPABILITIES

        module = importlib.import_module("services.tools.manage_physics")
        sent = []
        async def instance(ctx):
            return "fixture"
        async def send(fn, instance, command, params):
            sent.append(params)
            return {"success": True, "data": {"buffer_saturated": True, "max_results": params["max_results"]}}
        module.get_unity_instance_from_context = instance
        module.send_with_unity_instance = send
        capability = next(item for item in CAPABILITIES if item.name == "query_physics")
        server = FastMCP("bounded-physics")
        server.tool(name="manage_physics")(module.manage_physics)
        server.tool(name="query_physics")(register_capability(capability, module.manage_physics))

        async def main():
            for mode in ("2026-07-28", "legacy"):
                async with Client(server, mode=mode) as client:
                    for name in ("manage_physics", "query_physics"):
                        for action in ("overlap", "raycast_all"):
                            for capacity in (1, 200, 1000):
                                result = await client.call_tool(name, {"action": action, "max_results": capacity})
                                assert sent[-1] == {"action": action, "max_results": capacity}
                                assert result.structured_content == {"success": True, "data": {"buffer_saturated": True, "max_results": capacity}}
                            for invalid in (0, 1001, True, 1.0, 1.5, "2"):
                                count = len(sent)
                                result = await client.call_tool(name, {"action": action, "max_results": invalid}, raise_on_error=False)
                                assert result.is_error and len(sent) == count, (name, action, invalid, result)
            print("real SDK bounded physics queries passed")
        anyio.run(main)
    """)
    result = subprocess.run(
        [sys.executable, "-B", "-c", code],
        capture_output=True,
        text=True,
        timeout=45,
        env={**os.environ, "UNITY_MCP_DISABLE_TELEMETRY": "true"},
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "real SDK bounded physics queries passed" in result.stdout
