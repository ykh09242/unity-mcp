"""Canonical instance names containing @ retain project-only custom tool lookup."""

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
from types import SimpleNamespace
Path.home = classmethod(lambda cls: Path(os.environ["HOME"]))
def denied(*args, **kwargs):
    raise AssertionError("Application network prohibited")
socket.socket.connect_ex = denied
socket.create_connection = denied
import anyio
from fastmcp import Client, FastMCP
from fastmcp.server.middleware import Middleware
from core.config import config
from models.models import ToolDefinitionModel, UnityInstanceInfo
import services.custom_tool_service as module
from services.resources.unity_instances import unity_instances
from services.tools.execute_custom_tool import execute_custom_tool
from transport.models import RegisterMessage
from transport.plugin_hub import PluginHub
from transport.plugin_registry import PluginRegistry

selected = [None]
class Selected(Middleware):
    async def on_call_tool(self, context, call_next):
        await context.fastmcp_context.set_state("unity_instance", selected[0])
        return await call_next(context)

async def main():
    socket.socket.connect = denied
    config.transport_mode = "http"
    config.http_remote_hosted = False
    app = FastMCP("canonical-custom-identity")
    app.add_middleware(Selected())
    app.resource("mcpforunity://instances")(unity_instances)
    app.tool(name="execute_custom_tool")(execute_custom_tool)
    service = module.CustomToolService(app, project_scoped_tools=True)
    registry = PluginRegistry()
    PluginHub.configure(registry)
    calls = 0
    async with Client(app, mode=sys.argv[1]) as client:
        for name in ("Plain", "Team@Group", "Team@Group@Sub"):
            message = RegisterMessage(project_name=name, project_hash="fixturehash", unity_version="fixture")
            await registry.register("owned-session", message.project_name, message.project_hash, message.unity_version)
            await registry.register_tools_for_session("owned-session", [ToolDefinitionModel(name="project_only")])
            result = await client.read_resource("mcpforunity://instances")
            producer = json.loads(result[0].text)
            row = producer["instances"][0]
            assert row["id"] == name + "@fixturehash"
            typed = UnityInstanceInfo(id=row["id"], name=row["name"], path=str(Path.home() / "Assets"), hash=row["hash"], port=6501, status="running")
            for pool_present in (False, True):
                module.get_unity_connection_pool = lambda: SimpleNamespace(discover_all_instances=lambda: [typed] if pool_present else [])
                for selection in (row["id"], row["hash"]):
                    selected[0] = selection
                    requests = []
                    async def send(instance, command, params, **kwargs):
                        assert instance == selection and command == "project_only"
                        requests.append({"instance": instance, "command": command, "params": copy.deepcopy(params)})
                        return {"success": True, "data": {"project_hash": "fixturehash"}}
                    PluginHub.send_command_for_instance = send
                    call = await client.call_tool("execute_custom_tool", {"tool_name": "project_only"})
                    response = json.loads(call.content[0].text)
                    assert response.get("success") is True and response.get("data") == {"project_hash": "fixturehash"}, (selection, pool_present, response)
                    assert requests == [{"instance": selection, "command": "project_only", "params": {}}]
                    calls += 1
    await PluginHub.shutdown()
    assert calls == 12
    print("PASS", sys.argv[1], "12 execution calls; 3 actual producer resource reads")
anyio.run(main)
"""


@pytest.mark.parametrize("protocol", ["2026-07-28", "legacy"])
def test_project_only_custom_tool_uses_final_canonical_hash(tmp_path, protocol):
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
    assert "12 execution calls" in result.stdout
