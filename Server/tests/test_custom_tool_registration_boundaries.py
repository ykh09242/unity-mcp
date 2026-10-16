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
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("failure", ["preparation", "provider_conflict", "after_insert"])
def test_late_registration_failure_restores_public_provider_generation(tmp_path, failure):
    _run_sdk_scenario(
        """
        import asyncio, sys
        from unittest.mock import AsyncMock
        sys.path.insert(0, "src")
        import httpx
        from fastmcp import Client, FastMCP
        from core.config import config
        import services.custom_tool_service as module

        async def scenario():
            # Given: custom and unrelated SDK tools already have usable definitions.
            config.http_remote_hosted = False
            mcp = FastMCP("custom-late-publication-failure")
            service = module.CustomToolService(mcp, project_scoped_tools=False)
            module.get_unity_instance_from_context = AsyncMock(return_value="Project@oldhash")
            module.resolve_project_id_for_unity_instance = lambda instance: "project"
            module.send_with_unity_instance = AsyncMock(return_value={"success": True, "data": {"generation": "old"}})
            @mcp.tool(name="foreign_probe")
            async def foreign(value: int) -> dict:
                return {"foreign": value * 2}
            if FAILURE == "provider_conflict":
                @mcp.tool(name="blocked_probe", version="1")
                async def blocked() -> dict:
                    return {"blocked": "original"}
            transport = httpx.ASGITransport(app=mcp.http_app(), raise_app_exceptions=False)
            async with httpx.AsyncClient(transport=transport, base_url="http://audit") as registration:
                assert (await registration.post("/register-tools", json={"project_id": "project", "project_hash": "OLDHASH", "tools": [{"name": "stable_probe"}]})).status_code == 200
                if FAILURE == "preparation":
                    build = service._build_global_tool_handler
                    def fail_last_definition(definition):
                        if definition.name == "blocked_probe":
                            raise RuntimeError("Injected last handler preparation failure")
                        return build(definition)
                    service._build_global_tool_handler = fail_last_definition
                elif FAILURE == "after_insert":
                    add = mcp._local_provider.add_tool
                    def fail_after_insert(tool):
                        result = add(tool)
                        if result.name == "blocked_probe":
                            raise RuntimeError("Injected failure after SDK insertion")
                        return result
                    mcp._local_provider.add_tool = fail_after_insert
                async with Client(mcp) as client:
                    before = {tool.name: tool.input_schema for tool in await client.list_tools()}
                    # When: the final definition fails after earlier valid definitions.
                    response = await registration.post("/register-tools", json={"project_id": "project", "project_hash": "NEWHASH", "tools": [
                        {"name": "stable_probe", "parameters": [{"name": "changed", "required": True}]},
                        {"name": "partial_probe"}, {"name": "foreign_probe"}, {"name": "blocked_probe"},
                    ]})
                    # Then: SDK list/call, project execution and hash routing keep the old generation.
                    assert response.status_code == (400 if FAILURE == "provider_conflict" else 500), response.text
                    assert {tool.name: tool.input_schema for tool in await client.list_tools()} == before
                    assert list(service._global_tools) == ["stable_probe"]
                    assert [tool.name for tool in await service.list_registered_tools("project")] == ["stable_probe"]
                    assert service.get_project_id_for_hash("newhash") is None
                    assert service.get_project_id_for_hash("oldhash") == "project"
                    assert (await client.call_tool("stable_probe", {})).structured_content["data"] == {"generation": "old"}
                    assert (await client.call_tool("foreign_probe", {"value": 9})).structured_content == {"foreign": 18}
        asyncio.run(scenario())
    """.replace("FAILURE", repr(failure)),
        tmp_path,
    )


@pytest.mark.parametrize("project_scoped", [False, True])
def test_project_registration_preserves_builtin_schema_and_execution(tmp_path, project_scoped):
    _run_sdk_scenario(
        """
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
    """.replace("PROJECT_SCOPED", repr(project_scoped)),
        tmp_path,
    )


def test_successful_batch_preserves_project_collisions_and_additive_empty_semantics(tmp_path):
    _run_sdk_scenario(
        """
        import asyncio, sys
        from unittest.mock import AsyncMock
        sys.path.insert(0, "src")
        import httpx
        from fastmcp import Client, FastMCP
        from core.config import config
        import services.custom_tool_service as module

        async def scenario():
            # Given: two projects share a tool name but keep distinct runtime contracts.
            config.http_remote_hosted = False
            mcp = FastMCP("custom-successful-generations")
            service = module.CustomToolService(mcp, project_scoped_tools=False)
            active = AsyncMock(return_value="A@a")
            module.get_unity_instance_from_context = active
            module.resolve_project_id_for_unity_instance = lambda instance: instance.rsplit("@", 1)[1]
            async def send(_sender, instance, tool, params, **kwargs):
                return {"success": True, "data": {"instance": instance, "params": params}}
            module.send_with_unity_instance = send
            transport = httpx.ASGITransport(app=mcp.http_app())
            async with httpx.AsyncClient(transport=transport, base_url="http://audit") as registration:
                assert (await registration.post("/register-tools", json={"project_id": "a", "tools": [
                    {"name": "shared_probe", "parameters": [{"name": "count", "type": "integer", "required": True}]},
                    {"name": "first_probe"},
                ]})).status_code == 200
                async with Client(mcp) as client:
                    before = next(tool.input_schema for tool in await client.list_tools() if tool.name == "shared_probe")
                    # When: another complete valid generation and then an empty additive batch arrive.
                    response = await registration.post("/register-tools", json={"project_id": "b", "tools": [
                        {"name": "shared_probe", "parameters": [{"name": "count", "type": "number", "required": True}]},
                        {"name": "second_probe"},
                    ]})
                    assert response.status_code == 200, response.text
                    assert response.json()["registered"] == ["shared_probe", "second_probe"]
                    assert (await registration.post("/register-tools", json={"project_id": "b", "tools": []})).json()["registered"] == []
                    # Then: every new tool is callable, first global schema wins, and projects stay isolated.
                    tools = {tool.name: tool.input_schema for tool in await client.list_tools()}
                    assert set(tools) == {"shared_probe", "first_probe", "second_probe"}
                    assert tools["shared_probe"] == before
                    assert {tool.name for tool in await service.list_registered_tools("a")} == {"shared_probe", "first_probe"}
                    assert {tool.name for tool in await service.list_registered_tools("b")} == {"shared_probe", "second_probe"}
                    assert (await client.call_tool("shared_probe", {"count": 7})).structured_content["data"] == {"instance": "A@a", "params": {"count": 7}}
                    assert not (await client.call_tool("first_probe", {})).is_error
                    active.return_value = "B@b"
                    result = await client.call_tool("shared_probe", {"count": 7})
                    assert result.structured_content["data"] == {"instance": "B@b", "params": {"count": 7.0}}
                    assert not (await client.call_tool("second_probe", {})).is_error
        asyncio.run(scenario())
    """,
        tmp_path,
    )


@pytest.mark.parametrize("invalid_number", ["NaN", "Infinity", "-Infinity"])
def test_nonfinite_registration_input_returns_validation_error(tmp_path, invalid_number):
    _run_sdk_scenario(
        """
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
                assert error["error"][0]["type"] == "int_type"
                assert error["error"][0]["msg"]
                assert "input" not in error["error"][0]
                assert service._project_tools == {}
                valid = await client.post("/register-tools", json={"project_id":"project","tools":[{"name":"valid","max_poll_seconds":0}]})
                assert valid.status_code == 200
                assert valid.json()["registered"] == ["valid"]
        asyncio.run(scenario())
    """.replace("INVALID_NUMBER", invalid_number),
        tmp_path,
    )


@pytest.mark.parametrize("project_scoped", [False, True])
@pytest.mark.parametrize("invalid_names", [["value", "value"], ["ctx"], ["unity_instance"]])
def test_invalid_last_signature_preserves_public_registration_generation(
    tmp_path, project_scoped, invalid_names
):
    _run_sdk_scenario(
        """
        import asyncio, socket, sys
        from unittest.mock import AsyncMock
        sys.path.insert(0, "src")
        import httpx
        from fastmcp import Client, FastMCP
        from core.config import config
        import services.custom_tool_service as module

        async def scenario():
            # Given: an existing public catalog and working project-specific contract.
            config.http_remote_hosted = False
            mcp = FastMCP("custom-atomic-generation")
            service = module.CustomToolService(mcp, project_scoped_tools=PROJECT_SCOPED)
            module.get_unity_instance_from_context = AsyncMock(return_value="Project@oldhash")
            module.resolve_project_id_for_unity_instance = lambda instance: "project"
            module.send_with_unity_instance = AsyncMock(return_value={"success": True, "data": {"generation": "old"}})
            transport = httpx.ASGITransport(app=mcp.http_app(), raise_app_exceptions=False)
            async with httpx.AsyncClient(transport=transport, base_url="http://audit") as registration:
                old_payload = {"project_id": "project", "project_hash": "OLDHASH", "tools": [
                    {"name": "stable_probe", "parameters": [{"name": "count", "type": "integer", "required": True}]},
                ]}
                assert (await registration.post("/register-tools", json=old_payload)).status_code == 200
                if PROJECT_SCOPED:
                    service.register_global_tools(list(service._project_tools["project"].values()))
                async with Client(mcp) as client:
                    before = {tool.name: tool.input_schema for tool in await client.list_tools()}
                    # When: a valid replacement and new tool precede an invalid last signature.
                    replacement = {"project_id": "project", "project_hash": "NEWHASH", "tools": [
                        {"name": "stable_probe", "parameters": [{"name": "changed", "required": True}]},
                        {"name": "partial_probe"},
                        {"name": "invalid_probe", "parameters": [{"name": name} for name in INVALID_NAMES]},
                    ]}
                    response = await registration.post("/register-tools", json=replacement)
                    # Then: neither catalog, routing nor execution observes any replacement.
                    assert response.status_code == 400, (response.status_code, response.text)
                    assert response.json()["success"] is False
                    assert {tool.name: tool.input_schema for tool in await client.list_tools()} == before
                    assert [tool.name for tool in await service.list_registered_tools("project")] == ["stable_probe"]
                    assert service.get_project_id_for_hash("newhash") is None
                    assert service.get_project_id_for_hash("oldhash") == "project"
                    result = await client.call_tool("stable_probe", {"count": 7})
                    assert result.structured_content["data"] == {"generation": "old"}
                    assert module.send_with_unity_instance.call_args.args[3] == {"count": 7}
        asyncio.run(scenario())
    """.replace("PROJECT_SCOPED", repr(project_scoped)).replace(
            "INVALID_NAMES", repr(invalid_names)
        ),
        tmp_path,
    )
