"""Custom public entry points preserve guidance produced by the real transport."""

import os
from pathlib import Path
import subprocess
import sys

import pytest


PROGRAM = r"""
import asyncio
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
from models.models import ToolDefinitionModel, ToolParameterModel
import services.custom_tool_service as module
from services.tools.execute_custom_tool import execute_custom_tool
from transport.plugin_hub import InstanceSelectionRequiredError, PluginHub

class Selected(Middleware):
    async def on_call_tool(self, context, call_next):
        await context.fastmcp_context.set_state("unity_instance", "Selected@fixturehash")
        return await call_next(context)

async def main():
    socket.socket.connect = denied
    config.transport_mode = "http"
    config.http_remote_hosted = False
    module.get_unity_connection_pool = lambda: SimpleNamespace(discover_all_instances=lambda: [])
    app = FastMCP("custom-response-contract")
    app.add_middleware(Selected())
    app.tool(name="execute_custom_tool")(execute_custom_tool)
    service = module.CustomToolService(app)
    parameters = [ToolParameterModel(name="count", type="integer", required=False, default_value="3")]
    definitions = [ToolDefinitionModel(name="fixture_custom", parameters=parameters),
                   ToolDefinitionModel(name="fixture_content", parameters=parameters, structured_output=False)]
    service._register_project_tools("fixturehash", definitions, project_hash="fixturehash")
    service.register_global_tools(definitions)
    cases = []
    async with Client(app, mode=sys.argv[1]) as client:
        tools = {tool.name: tool for tool in await client.list_tools()}
        assert tools["fixture_content"].outputSchema is None
        for entry in ("execute_custom_tool", "fixture_custom", "fixture_content"):
            for case in ("transport_timeout", "selection_required", "poll_error", "success_without_hint"):
                target = "fixture_custom" if entry == "execute_custom_tool" else entry
                definition = service._project_tools["fixturehash"][target]
                definition.requires_polling = case == "poll_error"
                definition.max_poll_seconds = 2
                requests = []
                now = [0.0]
                async def sleep(delay):
                    now[0] += delay
                    await asyncio.sleep(0)
                module.time = SimpleNamespace(monotonic=lambda: now[0])
                module.asyncio = SimpleNamespace(sleep=sleep, wait_for=asyncio.wait_for, TimeoutError=asyncio.TimeoutError)
                async def send(instance, command, params, **kwargs):
                    assert instance == "Selected@fixturehash" and command == target
                    requests.append({"instance": instance, "command": command, "params": copy.deepcopy(params), "user_id": kwargs.get("user_id")})
                    if case == "transport_timeout":
                        raise TimeoutError()
                    if case == "selection_required":
                        raise InstanceSelectionRequiredError(available_instances=["OwnedA@a", "OwnedB@b"])
                    if case == "poll_error":
                        if len(requests) == 1:
                            return {"_mcp_status": "pending", "_mcp_poll_interval": .1, "data": {"job_id": "owned-job"}}
                        assert params == {"count": 7 if entry == "execute_custom_tool" else 3, "action": "status", "job_id": "owned-job"}
                        return {"_mcp_status": "error", "success": True, "error": "fixture_failed", "hint": "retry", "data": {"job_id": "owned-job"}}
                    return {"success": True, "message": "fixture completed", "data": {"value": 1}}
                PluginHub.send_command_for_instance = send
                arguments = {"tool_name": target, "parameters": {"count": 7}} if entry == "execute_custom_tool" else {}
                result = await client.call_tool(entry, arguments)
                response = json.loads(result.content[0].text)
                if case == "transport_timeout":
                    passed = response == {"success": False, "message": None, "error": "TimeoutError", "data": None, "hint": "retry"}
                elif case == "selection_required":
                    passed = response.get("success") is False and response.get("hint") == "select_instance" and response.get("data") == {"reason": "instance_selection_required", "available_instances": ["OwnedA@a", "OwnedB@b"]}
                elif case == "poll_error":
                    passed = response == {"success": False, "message": None, "error": "fixture_failed", "data": {"job_id": "owned-job"}, "hint": "retry"} and len(requests) == 2
                else:
                    passed = response == {"success": True, "message": "fixture completed", "error": None, "data": {"value": 1}, "hint": None}
                cases.append({"entry": entry, "case": case, "passed": passed, "response": response, "requests": requests})
    print(json.dumps({"protocol": sys.argv[1], "cases": cases, "failed": sum(not c["passed"] for c in cases)}))
    assert all(c["passed"] for c in cases), [(c["entry"], c["case"], c["response"]) for c in cases if not c["passed"]]
anyio.run(main)
"""


@pytest.mark.parametrize("protocol", ["2026-07-28", "legacy"])
def test_custom_execution_preserves_transport_and_terminal_hints(tmp_path, protocol):
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
