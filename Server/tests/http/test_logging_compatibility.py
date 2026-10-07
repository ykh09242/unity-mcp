"""Diagnostics must not make tool results depend on deprecated MCP logging."""

import ast
import importlib
import logging
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastmcp import Client, Context, FastMCP

from core.config import config
from services.resources.editor_state import infer_single_instance_id
from services.resources.unity_instances import unity_instances
from transport.plugin_hub import PluginHub


def test_server_handlers_do_not_call_deprecated_context_logging() -> None:
    services = Path(__file__).resolve().parents[2] / "src" / "services"
    deprecated = {"debug", "info", "warning", "warn", "error", "log"}
    calls = []
    for path in services.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id in {"ctx", "context"}
                and node.func.attr in deprecated
            ):
                calls.append(f"{path.relative_to(services)}:{node.lineno}")
    assert calls == [], "Deprecated client logging calls: " + ", ".join(calls)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "module_name, tool_name, arguments",
    [
        ("find_in_file", "find_in_file", {"uri": "Assets/Private.cs", "pattern": "private"}),
        (
            "manage_script",
            "create_script",
            {"path": "Assets/Private.cs", "contents": "private-payload"},
        ),
        ("manage_script", "delete_script", {"uri": "Assets/Private.cs"}),
        ("manage_script", "validate_script", {"uri": "Assets/Private.cs"}),
        ("manage_script", "get_sha", {"uri": "Assets/Private.cs"}),
        ("manage_script", "manage_script", {"action": "read", "name": "Private", "path": "Assets"}),
        (
            "manage_script",
            "apply_text_edits",
            {
                "uri": "Assets/Private.cs",
                "edits": [
                    {
                        "startLine": 1,
                        "startCol": 1,
                        "endLine": 1,
                        "endCol": 1,
                        "newText": "private-payload",
                    },
                ],
            },
        ),
        (
            "manage_asset",
            "manage_asset",
            {"action": "search", "path": "t:Material", "asset_type": "Material"},
        ),
    ],
)
async def test_legacy_tool_errors_preserve_selected_editor_routing(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    module_name: str,
    tool_name: str,
    arguments: dict,
) -> None:
    module = importlib.import_module(f"services.tools.{module_name}")
    response = {"success": False, "message": "private-payload"}
    send = AsyncMock(return_value=response)
    monkeypatch.setattr(
        module, "get_unity_instance_from_context", AsyncMock(return_value="Editor@hash")
    )
    monkeypatch.setattr(module, "send_with_unity_instance", send)
    if module_name == "manage_script":
        monkeypatch.setattr(module, "send_mutation", send)
    if module_name == "manage_asset":
        monkeypatch.setattr(module, "preflight", AsyncMock(return_value=None))
    server = FastMCP("logging-compatibility")
    server.tool(name=tool_name)(getattr(module, tool_name))
    caplog.set_level(logging.INFO, logger="mcp-for-unity-server")

    async with Client(server, mode="legacy") as client:
        result = await client.call_tool(tool_name, arguments)

    assert result.structured_content["success"] is False
    assert result.structured_content["message"] == response["message"]
    assert send.await_count >= 1
    assert all(call.args[1] == "Editor@hash" for call in send.await_args_list)
    messages = [
        record.getMessage() for record in caplog.records if record.name == "mcp-for-unity-server"
    ]
    assert messages
    assert not any("private-payload" in message or "Private.cs" in message for message in messages)
    if module_name == "manage_asset":
        params = send.await_args.args[3]
        assert params["path"] == "Assets"
        assert params["searchPattern"] == "t:Material"
        assert params["filterType"] == "Material"


@pytest.mark.asyncio
async def test_legacy_scriptable_object_response_is_returned_without_logging_payload(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    module = importlib.import_module("services.tools.manage_scriptable_object")
    response = {"success": True, "data": {"value": "private-payload"}}
    send = AsyncMock(return_value=response)
    monkeypatch.setattr(
        module, "get_unity_instance_from_context", AsyncMock(return_value="Editor@hash")
    )
    monkeypatch.setattr(module, "send_with_unity_instance", send)
    server = FastMCP("scriptable-object-logging")
    server.tool(name="manage_scriptable_object")(module.manage_scriptable_object)
    caplog.set_level(logging.INFO, logger="mcp-for-unity-server")

    async with Client(server, mode="legacy") as client:
        result = await client.call_tool(
            "manage_scriptable_object",
            {"action": "modify", "target": {"path": "Assets/Private.asset"}, "patches": []},
        )

    assert result.structured_content == response
    assert send.await_args.args[1] == "Editor@hash"
    assert all(
        "private-payload" not in record.getMessage()
        for record in caplog.records
        if record.name == "mcp-for-unity-server"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "module_name, tool_name, arguments, expected",
    [
        (
            "manage_asset",
            "manage_asset",
            {
                "action": "create",
                "path": "Assets/Private.asset",
                "asset_type": "Material",
                "properties": "private-payload",
            },
            {
                "success": False,
                "message": "properties must be a JSON object (dict), got string that parsed to str",
            },
        ),
        (
            "script_apply_edits",
            "script_apply_edits",
            {"name": "Private", "path": "Assets", "edits": [{"op": "invalid"}]},
            {"success": False, "code": "unsupported_op"},
        ),
        ("manage_script", "manage_script_capabilities", {}, {"success": True}),
    ],
)
async def test_legacy_local_tool_results_do_not_require_client_logging(
    monkeypatch: pytest.MonkeyPatch,
    module_name: str,
    tool_name: str,
    arguments: dict,
    expected: dict,
) -> None:
    module = importlib.import_module(f"services.tools.{module_name}")
    monkeypatch.setattr(
        module, "get_unity_instance_from_context", AsyncMock(return_value="Editor@hash")
    )
    send = AsyncMock(side_effect=AssertionError("Local result must not dispatch to Unity"))
    monkeypatch.setattr(module, "send_with_unity_instance", send)
    server = FastMCP("local-logging")
    server.tool(name=tool_name)(getattr(module, tool_name))

    async with Client(server, mode="legacy") as client:
        result = (await client.call_tool(tool_name, arguments)).structured_content

    assert {key: result[key] for key in expected} == expected
    send.assert_not_awaited()
    if tool_name == "manage_script_capabilities":
        assert result["data"]["guards"] == {"using_guard": True}
        assert result["data"]["extras"] == {"get_sha": True}


@pytest.mark.asyncio
async def test_legacy_documentation_asset_search_warning_preserves_fallback(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    module = importlib.import_module("services.tools.unity_docs")
    monkeypatch.setattr(
        "services.tools.get_unity_instance_from_context", AsyncMock(return_value="Editor@hash")
    )
    send = AsyncMock(side_effect=RuntimeError("private-payload"))
    monkeypatch.setattr("transport.unity_transport.send_with_unity_instance", send)
    server = FastMCP("documentation-logging")

    @server.tool
    async def asset_search_probe(ctx: Context) -> dict:
        return {"assets": await module._search_assets(ctx, "private shader")}

    caplog.set_level(logging.INFO, logger="mcp-for-unity-server")
    async with Client(server, mode="legacy") as client:
        result = (await client.call_tool("asset_search_probe")).structured_content

    assert result == {"assets": None}
    assert send.await_args.args[1] == "Editor@hash"
    messages = [record for record in caplog.records if record.name == "mcp-for-unity-server"]
    assert any(record.levelno == logging.WARNING for record in messages)
    assert all("private-payload" not in record.getMessage() for record in messages)


@pytest.mark.asyncio
@pytest.mark.parametrize("failed", [False, True])
async def test_legacy_instance_diagnostics_preserve_user_scoping_and_errors(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    failed: bool,
) -> None:
    monkeypatch.setattr(config, "transport_mode", "http")
    monkeypatch.setattr(config, "http_remote_hosted", True)
    sessions = SimpleNamespace(
        sessions={
            "owned": SimpleNamespace(
                project="Editor",
                hash="hash",
                unity_version="6000",
                connected_at="fixture",
            )
        }
    )
    get_sessions = AsyncMock(return_value=sessions)
    if failed:
        get_sessions.side_effect = RuntimeError("private-payload")
    monkeypatch.setattr(PluginHub, "get_sessions", get_sessions)
    server = FastMCP("instance-logging")

    @server.tool
    async def instance_probe(ctx: Context) -> dict:
        await ctx.set_state("user_id", "owned-user")
        result = await unity_instances(ctx)
        result["inferred"] = await infer_single_instance_id(ctx)
        return result

    caplog.set_level(logging.INFO, logger="mcp-for-unity-server")
    async with Client(server, mode="legacy") as client:
        result = (await client.call_tool("instance_probe")).structured_content

    assert result["success"] is not failed
    assert result["instance_count"] == (0 if failed else 1)
    assert result["inferred"] == (None if failed else "Editor@hash")
    assert all(call.kwargs == {"user_id": "owned-user"} for call in get_sessions.await_args_list)
    if failed:
        assert result["error"] == "Failed to list Unity instances: private-payload"
        assert any(record.levelno == logging.ERROR for record in caplog.records)
    assert all(
        "private-payload" not in record.getMessage()
        for record in caplog.records
        if record.name == "mcp-for-unity-server"
    )
