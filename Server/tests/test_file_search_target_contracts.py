"""Registered SDK searches must never silently switch a requested file extension."""

import os
from pathlib import Path
import subprocess
import sys

import pytest


PROGRAM = r"""
import copy
import json
import os
from pathlib import Path
import socket
import sys
Path.home = classmethod(lambda cls: Path(os.environ["HOME"]))
def denied(*args, **kwargs):
    raise AssertionError("Application network prohibited")
socket.socket.connect_ex = denied
socket.create_connection = denied
import anyio
from fastmcp import Client, FastMCP
from fastmcp.server.middleware import Middleware
from core.config import config
from services.tools.find_in_file import find_in_file
from transport.plugin_hub import PluginHub

class Selected(Middleware):
    async def on_call_tool(self, context, call_next):
        await context.fastmcp_context.set_state("unity_instance", "Selected@fixture")
        return await call_next(context)

async def main():
    def denied(*args, **kwargs):
        raise AssertionError("Application network prohibited")
    socket.socket.connect = denied
    socket.socket.connect_ex = denied
    socket.create_connection = denied
    config.transport_mode = "http"
    config.http_remote_hosted = False
    app = FastMCP("file-search-target-contract")
    app.add_middleware(Selected())
    app.tool(name="find_in_file")(find_in_file)
    async with Client(app, mode=sys.argv[1]) as client:
        # URI decode is once; dots and literal percent escapes in directories
        # do not become filename extensions or trigger another decode.
        supported = [
            ("Assets/Foo.cs", "Assets"),
            ("Assets/Foo.CS", "Assets"),
            ("Assets/Foo", "Assets"),
            ("file:///C:/Owned/Assets/Version.1/Foo.cs", "Assets/Version.1"),
            ("mcpforunity://path/Assets/Literal%2520/Foo.cs", "Assets/Literal%20"),
        ]
        unsupported = ["Assets/Foo.json", "Assets/Foo.shader", "Assets/Foo.cs.meta",
                       "file:///C:/Owned/Assets/Foo%2Ejson",
                       "mcpforunity://path/Assets/Foo.txt"]
        requests = []
        reply = {"success": True, "data": {"uri": "mcpforunity://path/Assets/Foo.cs", "path": "Assets/Foo.cs", "contents": "// fixture\n", "encodedContents": None, "contentsEncoded": False}}
        async def send(instance, command, params, **kwargs):
            assert instance == "Selected@fixture"
            assert command == "manage_script"
            assert params["action"] == "read" and params["name"] == "Foo"
            requests.append(copy.deepcopy(params))
            return copy.deepcopy(reply)
        PluginHub.send_command_for_instance = send
        for uri in unsupported:
            requests.clear()
            result = await client.call_tool("find_in_file", {"uri": uri, "pattern": "fixture"})
            response = json.loads(result.content[0].text)
            assert response.get("success") is False, (uri, response)
            assert ".cs" in response.get("message", ""), response
            assert not requests, (uri, requests)
        for uri, directory in supported:
            requests.clear()
            result = await client.call_tool("find_in_file", {"uri": uri, "pattern": "fixture"})
            response = json.loads(result.content[0].text)
            assert response.get("success") is True and response["data"]["count"] == response["data"]["total_matches"] == 1, response
            assert requests == [{"action": "read", "name": "Foo", "path": directory}], (uri, requests)
        reply = {"success": False, "error": "read_failed", "hint": "fixture hint", "data": {"path": "Assets/Foo.cs"}}
        result = await client.call_tool("find_in_file", {"uri": "Assets/Foo.cs", "pattern": "fixture"})
        assert json.loads(result.content[0].text) == reply
    print("PASS", sys.argv[1], "11 public calls")
anyio.run(main)
"""


@pytest.mark.parametrize("protocol", ["2026-07-28", "legacy"])
def test_search_rejects_wrong_file_extension_before_transport(tmp_path, protocol):
    env = {key: value for key, value in os.environ.items() if not key.startswith("UNITY_MCP_")}
    for key in (
        "HOME",
        "USERPROFILE",
        "APPDATA",
        "XDG_DATA_HOME",
        "UNITY_MCP_LOG_DIR",
        "TEMP",
        "TMP",
    ):
        env[key] = str(tmp_path)
    env["UNITY_MCP_DISABLE_TELEMETRY"] = "true"
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
    env.pop("PYTEST_CURRENT_TEST", None)
    result = subprocess.run(
        [sys.executable, "-B", "-c", PROGRAM, protocol],
        env=env,
        capture_output=True,
        text=True,
        timeout=90,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "11 public calls" in result.stdout
