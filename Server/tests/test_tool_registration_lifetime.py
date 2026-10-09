"""Production registration owns wrappers per server, preserving declarations."""

import os
from pathlib import Path
import subprocess
import sys
import textwrap

import pytest


SETUP = r"""
import asyncio, gc, sys, weakref
from typing import Literal
sys.path.insert(0, "src")
from fastmcp import Client, FastMCP
from core.config import config
import core.telemetry_decorator as telemetry
import services.registry.tool_registry as registry
import services.tools as tools
config.transport_mode = "stdio"
config.http_remote_hosted = False
tools.discover_modules = lambda *_args: []
registry._tool_registry = []
records = []
telemetry.record_tool_usage = lambda *_a, **_k: records.append(1)
telemetry.record_milestone = lambda *_a, **_k: None
telemetry.register_tool_actions = lambda *_a, **_k: None
telemetry.tool_action_label = lambda _name, action: action
@registry.mcp_for_unity_tool()
async def lifetime_probe(action: Literal["get"] = "get", enabled: bool = True) -> dict:
    return {"success": True, "enabled": enabled, "action": action}
"""


def run_scenario(source, tmp_path):
    env = {
        **os.environ,
        "UNITY_MCP_DISABLE_TELEMETRY": "1",
        "APPDATA": str(tmp_path / "appdata"),
        "XDG_DATA_HOME": str(tmp_path / "data"),
    }
    result = subprocess.run(
        [sys.executable, "-c", textwrap.dedent(SETUP) + textwrap.dedent(source)],
        cwd=Path(__file__).resolve().parents[1],
        env=env,
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("mode", ["2026-07-28", "legacy"])
def test_repeated_registration_records_once_and_preserves_sdk_validation(tmp_path, mode):
    run_scenario(
        r"""
        async def scenario():
            for generation in range(5):
                server = FastMCP("registration-" + str(generation))
                tools.register_all_tools(server)
                async with Client(server, mode=MODE) as client:
                    schema = (await client.list_tools())[0].input_schema
                    assert schema["properties"]["enabled"]["type"] == "boolean"
                    before = len(records)
                    result = await client.call_tool("lifetime_probe", {"enabled": False})
                    assert result.data == {"success": True, "enabled": False, "action": "get"}
                    assert len(records) - before == 1, (generation, len(records) - before)
                    invalid = await client.call_tool("lifetime_probe", {"enabled": 0}, raise_on_error=False)
                    assert invalid.is_error
                    assert len(records) - before == 1, "Validation must precede tool execution"
        asyncio.run(scenario())
        """.replace("mode=MODE", "mode=" + repr(mode)),
        tmp_path,
    )


def test_registry_does_not_retain_disposed_server_wrapper_chain(tmp_path):
    run_scenario(
        r"""
        references = []
        decorate = tools.telemetry_tool
        def observe(name):
            def apply(func):
                wrapped = decorate(name)(func)
                references.append(weakref.ref(wrapped))
                return wrapped
            return apply
        tools.telemetry_tool = observe
        async def register_once():
            server = FastMCP("owned-wrapper")
            tools.register_all_tools(server)
            component = await server.get_tool("lifetime_probe")
            await component.run({})
        async def scenario():
            for _ in range(5):
                await register_once()
            gc.collect()
            # SDK schema caches may still own these weakly observed wrappers.
            # Check the global registry root rather than asserting SDK eviction.
            declaration = registry.get_registered_tools()[0]["func"]
            chain = []
            while declaration is not None:
                chain.append(declaration)
                declaration = getattr(declaration, "__wrapped__", None)
            assert not any(ref() in chain for ref in references if ref() is not None)
        asyncio.run(scenario())
        """,
        tmp_path,
    )


def test_registry_keeps_original_declaration_and_servers_get_distinct_wrappers(tmp_path):
    run_scenario(
        r"""
        async def scenario():
            servers = [FastMCP("first"), FastMCP("second")]
            for server in servers:
                tools.register_all_tools(server)
            assert registry.get_registered_tools()[0]["func"] is lifetime_probe
            first, second = [await server.get_tool("lifetime_probe") for server in servers]
            assert first.fn is not second.fn
            assert first.fn.__wrapped__.__wrapped__ is lifetime_probe
            assert second.fn.__wrapped__.__wrapped__ is lifetime_probe
        asyncio.run(scenario())
        """,
        tmp_path,
    )
