"""Dynamic custom metadata survives real FastMCP registration and invocation."""
import os
from pathlib import Path
import subprocess
import sys
import textwrap


def _run_sdk_scenario(source: str, evidence_dir: Path) -> None:
    # Some legacy test modules replace SDK imports during collection.
    server_root = Path(__file__).resolve().parents[1]
    env = dict(os.environ)
    env["UNITY_MCP_DISABLE_TELEMETRY"] = "true"
    for name in ("APPDATA", "XDG_DATA_HOME", "UNITY_MCP_LOG_DIR"):
        env[name] = str(evidence_dir / name.lower())
    result = subprocess.run(
        [sys.executable, "-c", textwrap.dedent(source)],
        cwd=server_root, env=env, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_container_defaults_and_descriptions_reach_sdk_schema_and_calls(tmp_path):
    _run_sdk_scenario('''
        import asyncio, socket, sys
        from unittest.mock import AsyncMock
        sys.path.insert(0, "src")
        from fastmcp import FastMCP, Client
        from models.models import MCPResponse, ToolDefinitionModel, ToolParameterModel
        import services.custom_tool_service as module

        def deny_outbound(*args, **kwargs):
            raise AssertionError("Unexpected outbound socket connection")

        async def scenario():
            # Let Windows asyncio create its socket pair before blocking outbound.
            socket.socket.connect = deny_outbound
            socket.socket.connect_ex = deny_outbound
            mcp = FastMCP("custom-container-metadata")
            service = module.CustomToolService(mcp)
            module.get_unity_instance_from_context = AsyncMock(return_value="Project@hash")
            module.resolve_project_id_for_unity_instance = lambda instance: "project"
            service.execute_tool = AsyncMock(return_value=MCPResponse(success=True))
            cases = [
                ("array", "[1,2]", [1, 2], [3]),
                ("list", "[]", [], [4]),
                ("object", '{"quality":2}', {"quality": 2}, {"quality": 7}),
                ("dict", "{}", {}, {"quality": 8}),
            ]
            definitions = [ToolDefinitionModel(name=f"metadata_{kind}", parameters=[
                ToolParameterModel(name="payload", type=kind, required=False,
                    default_value=raw, description="Options or items to process"),
                ToolParameterModel(name="label", required=True, description="Target label"),
            ]) for kind, raw, expected, override in cases]
            service.register_global_tools(definitions)
            async with Client(mcp) as client:
                tools = {tool.name: tool for tool in await client.list_tools()}
                for kind, raw, expected, override in cases:
                    name = f"metadata_{kind}"
                    schema = tools[name].input_schema
                    assert schema["required"] == ["label"]
                    assert schema["properties"]["payload"]["default"] == expected
                    assert schema["properties"]["payload"]["description"] == "Options or items to process"
                    assert schema["properties"]["label"]["description"] == "Target label"
                    assert not (await client.call_tool(name, {"label": "target"})).is_error
                    assert service.execute_tool.call_args.args[3] == {"payload": expected, "label": "target"}
                    assert not (await client.call_tool(name, {"label": "target", "payload": override})).is_error
                    assert service.execute_tool.call_args.args[3] == {"payload": override, "label": "target"}
        asyncio.run(scenario())
    ''', tmp_path)


def test_scalar_aliases_and_absent_defaults_keep_existing_sdk_behavior(tmp_path):
    _run_sdk_scenario('''
        import asyncio, socket, sys
        from unittest.mock import AsyncMock
        sys.path.insert(0, "src")
        from fastmcp import FastMCP, Client
        from models.models import MCPResponse, ToolDefinitionModel, ToolParameterModel
        import services.custom_tool_service as module

        def deny_outbound(*args, **kwargs):
            raise AssertionError("Unexpected outbound socket connection")

        async def scenario():
            socket.socket.connect = deny_outbound
            socket.socket.connect_ex = deny_outbound
            mcp = FastMCP("custom-scalar-compatibility")
            service = module.CustomToolService(mcp)
            module.get_unity_instance_from_context = AsyncMock(return_value="Project@hash")
            module.resolve_project_id_for_unity_instance = lambda instance: "project"
            service.execute_tool = AsyncMock(return_value=MCPResponse(success=True))
            cases = [("integer", "3", 3), ("int", "4", 4),
                ("number", "1.5", 1.5), ("float", "2.5", 2.5), ("double", "3.5", 3.5),
                ("boolean", "true", True), ("bool", "false", False), ("string", "hello", "hello")]
            definitions = [ToolDefinitionModel(name=f"scalar_{kind}", parameters=[
                ToolParameterModel(name="value", type=kind, required=False, default_value=raw),
                ToolParameterModel(name="absent", type=kind, required=False),
            ]) for kind, raw, expected in cases]
            service.register_global_tools(definitions)
            async with Client(mcp) as client:
                tools = {tool.name: tool for tool in await client.list_tools()}
                for kind, raw, expected in cases:
                    name = f"scalar_{kind}"
                    schema = tools[name].input_schema
                    assert "description" not in schema["properties"]["value"]
                    assert schema["properties"]["value"]["default"] == expected
                    assert schema["properties"]["absent"]["default"] is None
                    assert not (await client.call_tool(name, {})).is_error
                    assert service.execute_tool.call_args.args[3] == {"value": expected}
        asyncio.run(scenario())
    ''', tmp_path)


def test_invalid_container_defaults_keep_existing_string_fallback(tmp_path):
    _run_sdk_scenario('''
        import sys
        sys.path.insert(0, "src")
        from fastmcp import FastMCP
        from services.custom_tool_service import CustomToolService
        service = CustomToolService(FastMCP("custom-default-fallback"))
        for kind in ("array", "list", "object", "dict"):
            for raw in ("invalid-json", "null", "true", "3", '"text"'):
                assert service._coerce_default(raw, kind) == raw
            assert service._coerce_default(None, kind) is None
        assert service._coerce_default("{}", "array") == "{}"
        assert service._coerce_default("[]", "object") == "[]"
        assert service._coerce_default("", "string") == ""
        assert service._coerce_default("invalid-number", "integer") == "invalid-number"
    ''', tmp_path)


def test_registration_route_rejects_malformed_json_without_server_errors(tmp_path):
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
            mcp = FastMCP("custom-registration-input")
            service = CustomToolService(mcp)
            transport = httpx.ASGITransport(app=mcp.http_app(), raise_app_exceptions=False)
            async with httpx.AsyncClient(transport=transport, base_url="http://audit") as client:
                for body in (b"{", bytes([255])):
                    response = await client.post("/register-tools", content=body)
                    assert response.status_code == 400, response.text
                    assert response.json()["success"] is False
                    assert isinstance(response.json()["error"], str)
                invalid = await client.post("/register-tools", json={"project_id": "project"})
                assert invalid.status_code == 400
                assert isinstance(invalid.json()["error"], list)
                valid = await client.post("/register-tools", json={"project_id": "project", "tools": []})
                assert valid.status_code == 200
                assert valid.json()["success"] is True
            config.http_remote_hosted = True
            hosted = FastMCP("custom-registration-hosted")
            CustomToolService(hosted)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=hosted.http_app()), base_url="http://audit") as client:
                response = await client.post("/register-tools", content=b"{")
                assert response.status_code == 404
        asyncio.run(scenario())
    ''', tmp_path)
