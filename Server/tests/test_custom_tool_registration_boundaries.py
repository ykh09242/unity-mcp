"""Exercise custom registration boundaries through the installed MCP SDK."""

import os
from pathlib import Path
import subprocess
import sys
import textwrap

import pytest


def _run_sdk_scenario(source: str, evidence_dir: Path) -> None:
    env = dict(os.environ)
    env["UNITY_MCP_DISABLE_TELEMETRY"] = "true"
    for name in ("APPDATA", "XDG_DATA_HOME", "UNITY_MCP_LOG_DIR"):
        env[name] = str(evidence_dir / name.lower())
    result = subprocess.run(
        [sys.executable, "-B", "-W", "error", "-c", textwrap.dedent(source)],
        cwd=Path(__file__).resolve().parents[1],
        env=env, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("project_scoped", [False, True])
def test_project_registration_preserves_builtin_schema_and_execution(tmp_path, project_scoped):
    _run_sdk_scenario('''
        import asyncio, socket, sys
        sys.path.insert(0, "src")
        import httpx
        from fastmcp import Client, FastMCP
        from core.config import config
        from models.models import ToolDefinitionModel
        from services.custom_tool_service import CustomToolService
        from services.registry import mcp_for_unity_tool

        def deny_outbound(*args, **kwargs):
            raise AssertionError("Unexpected outbound socket connection")

        async def scenario():
            socket.socket.connect = deny_outbound
            socket.socket.connect_ex = deny_outbound
            config.http_remote_hosted = False
            mcp = FastMCP("custom-builtin-collision")

            @mcp_for_unity_tool(name="builtin_probe", group=None, unity_target=None)
            async def builtin(value: int) -> dict:
                return {"builtin_value": value}

            mcp.tool(name="builtin_probe")(builtin)
            service = CustomToolService(mcp, project_scoped_tools=PROJECT_SCOPED)
            service.register_global_tools([ToolDefinitionModel(name="builtin_probe")])
            assert "builtin_probe" not in service._global_tools
            async with Client(mcp) as client:
                before = next(tool for tool in await client.list_tools() if tool.name == "builtin_probe")
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=mcp.http_app()), base_url="http://audit") as registration:
                    payload = {"project_id": "project", "project_hash": "HASH", "tools": [
                        {"name": "builtin_probe", "parameters": [{"name": "replacement", "type": "string"}]},
                        {"name": "custom_probe"},
                    ]}
                    for expected_replaced in ([], ["builtin_probe", "custom_probe"]):
                        response = await registration.post("/register-tools", json=payload)
                        assert response.status_code == 200, response.text
                        body = response.json()
                        assert body["registered"] == ["builtin_probe", "custom_probe"]
                        assert body["replaced"] == expected_replaced
                    empty = await registration.post("/register-tools", json={"project_id": "project", "tools": []})
                    assert empty.json()["registered"] == []
                after = next(tool for tool in await client.list_tools() if tool.name == "builtin_probe")
                assert after.input_schema == before.input_schema, (before.input_schema, after.input_schema)
                result = await client.call_tool("builtin_probe", {"value": 7})
                assert result.structured_content == {"builtin_value": 7}
                assert "builtin_probe" not in service._global_tools
                assert ("custom_probe" in service._global_tools) is (not PROJECT_SCOPED)
                assert service._project_tools["project"]["builtin_probe"].parameters[0].name == "replacement"
                assert service.get_project_id_for_hash("hash") == "project"
        asyncio.run(scenario())
    '''.replace("PROJECT_SCOPED", repr(project_scoped)), tmp_path)


@pytest.mark.parametrize("invalid_number", ["NaN", "Infinity", "-Infinity"])
def test_nonfinite_registration_input_returns_validation_error(tmp_path, invalid_number):
    _run_sdk_scenario('''
        import asyncio, socket, sys
        sys.path.insert(0, "src")
        import httpx
        from fastmcp import FastMCP
        from core.config import config
        from services.custom_tool_service import CustomToolService

        def deny_outbound(*args, **kwargs):
            raise AssertionError("Unexpected outbound socket connection")

        async def scenario():
            socket.socket.connect = deny_outbound
            socket.socket.connect_ex = deny_outbound
            config.http_remote_hosted = False
            mcp = FastMCP("custom-malformed-number")
            service = CustomToolService(mcp)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=mcp.http_app(), raise_app_exceptions=False), base_url="http://audit") as client:
                body = '{"project_id":"project","tools":[{"name":"invalid","max_poll_seconds":INVALID_NUMBER}]}'
                response = await client.post("/register-tools", content=body)
                assert response.status_code == 400, (response.status_code, response.text)
                error = response.json()
                assert error["success"] is False
                assert isinstance(error["error"], list) and error["error"]
                assert error["error"][0]["loc"] == ["tools", 0, "max_poll_seconds"]
                assert error["error"][0]["type"] == "finite_number"
                assert error["error"][0]["msg"]
                assert "input" not in error["error"][0]
                assert service._project_tools == {}
                valid = await client.post("/register-tools", json={"project_id":"project","tools":[{"name":"valid","max_poll_seconds":0}]})
                assert valid.status_code == 200
                assert valid.json()["registered"] == ["valid"]
        asyncio.run(scenario())
    '''.replace("INVALID_NUMBER", invalid_number), tmp_path)
