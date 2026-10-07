"""Registering another server must not add wrappers to the shared resource registry."""

import os
from pathlib import Path
import subprocess
import sys
import textwrap

import pytest


@pytest.mark.parametrize("mode", ["2026-07-28", "legacy"])
def test_repeated_registration_preserves_handlers_and_single_usage_events(mode, tmp_path):
    env = {**os.environ, "UNITY_MCP_DISABLE_TELEMETRY": "true"}
    for key in ("APPDATA", "XDG_DATA_HOME", "UNITY_MCP_LOG_DIR"):
        directory = tmp_path / key
        directory.mkdir()
        env[key] = str(directory)
    env.pop("UNITY_MCP_DEFAULT_INSTANCE", None)
    source = r"""
import asyncio
import json
import logging
import os
from pathlib import Path
import socket
import sys

home = Path(os.environ["APPDATA"]).parent / "owned-home"
home.mkdir()
Path.home = lambda: home
def deny_network(*args, **kwargs):
    raise AssertionError("Resource registration tests forbid application network access")
original_connect = socket.socket.connect
original_socketpair = socket.socketpair
def internal_socketpair(*args, **kwargs):
    current = socket.socket.connect
    socket.socket.connect = original_connect
    try:
        return original_socketpair(*args, **kwargs)
    finally:
        socket.socket.connect = current
socket.socketpair = internal_socketpair
socket.socket.connect = deny_network
socket.create_connection = deny_network
sys.path.insert(0, "src")

from fastmcp import FastMCP, Client
from fastmcp.exceptions import ResourceError
from mcp.shared.exceptions import MCPError
from models.models import MCPResponse
import core.telemetry_decorator as telemetry
import services.resources as resources
from services.registry.resource_registry import (
    clear_resource_registry, get_registered_resources, mcp_for_unity_resource,
)

clear_resource_registry()
resources.discover_modules = lambda *args: ()
events = []
telemetry.record_resource_usage = lambda name, success, duration, error: events.append((name, success))
started = []
class Starts(logging.Handler):
    def emit(self, record):
        if record.msg == "%s '%s' started":
            started.append(record.args[1])
logger = logging.getLogger("mcp-for-unity-server")
logger.setLevel(logging.INFO)
logger.addHandler(Starts())
calls = []

@mcp_for_unity_resource("mcpforunity://registration/static", name="owned_static")
async def static_resource():
    calls.append("owned_static")
    return MCPResponse(success=True, data={"kind": "static"})

@mcp_for_unity_resource("mcpforunity://registration/{item}/page{?cursor}", name="owned_page")
async def page_resource(item: str, cursor: int = 0):
    calls.append("owned_page")
    return {"item": item, "cursor": cursor}

@mcp_for_unity_resource("mcpforunity://registration/failure", name="owned_failure")
async def failing_resource():
    calls.append("owned_failure")
    raise ValueError("owned fixture failure")

originals = {entry["name"]: entry["func"] for entry in get_registered_resources()}
observations = []
async def read_all(app, label):
    async with Client(app, mode=MODE) as client:
        for name, uri, expected in (
            ("owned_static", "mcpforunity://registration/static", {"kind": "static"}),
            ("owned_page", "mcpforunity://registration/sample/page?cursor=3", {"item": "sample", "cursor": 3}),
            ("owned_failure", "mcpforunity://registration/failure", None),
        ):
            events.clear()
            started.clear()
            calls.clear()
            try:
                content = await client.read_resource(uri)
            except (ResourceError, MCPError):
                assert name == "owned_failure"
            else:
                assert expected is not None, "Fixture failure was swallowed"
                data = json.loads(content[0].text)
                assert (data["data"] if name == "owned_static" else data) == expected
            assert calls == [name], calls
            assert all(event == (name, expected is not None) for event in events)
            observations.append({"server": label, "resource": name,
                                 "usage_events": len(events), "start_logs": len(started)})

async def scenario():
    first = None
    for index in range(3):
        app = FastMCP("registration-lifecycle-" + str(index))
        resources.register_all_resources(app)
        if first is None:
            first = app
        await read_all(app, str(index))
    # Earlier servers keep their own registered handlers after another is created.
    await read_all(first, "first-again")
    print(json.dumps({"mode": MODE, "observations": observations}), flush=True)
    assert all(row["usage_events"] == 1 and row["start_logs"] == 1 for row in observations), observations
    assert all(entry["func"] is originals[entry["name"]] for entry in get_registered_resources())

asyncio.run(scenario())
"""
    result = subprocess.run(
        [sys.executable, "-c", f"MODE = {mode!r}\n" + textwrap.dedent(source)],
        cwd=Path(__file__).resolve().parents[1],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
