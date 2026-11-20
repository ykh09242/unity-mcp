"""The same input and result corpus runs through the SDK and native Editor dispatch."""

import json
from pathlib import Path
from unittest.mock import AsyncMock

from fastmcp import Client, FastMCP
from jsonschema import Draft202012Validator
import pytest

from core.config import config
from services.credential_lease import bind_credential_lease
from services.tools import manage_asset, register_all_tools
from tests.http.test_remote_permissions import permission_service
from tests.test_http_command_outcome import command_hub  # pylint: disable=unused-import
from transport.plugin_hub import PluginHub
from transport.remote_auth_middleware import AUTHENTICATED_LEASE_STATE


@pytest.fixture
def contract():
    path = (
        Path(__file__).resolve().parents[2]
        / "TestProjects/UnityMCPTests/Assets/Tests/EditMode/Tools/asset-move-contract.json"
    )
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["legacy", "2026-07-28"])
async def test_preview_and_move_agree_with_schema_help_and_catalog(contract, mode, monkeypatch):
    source, destination = "Assets/Shop.prefab", "Assets/Moved/Shop.prefab"
    monkeypatch.setattr(config, "transport_mode", "stdio")
    monkeypatch.setattr(config, "http_remote_hosted", False)
    monkeypatch.setattr(manage_asset, "preflight", AsyncMock(return_value=None))
    monkeypatch.setattr(
        manage_asset, "get_unity_instance_from_context", AsyncMock(return_value="owned")
    )
    send = AsyncMock()
    monkeypatch.setattr(manage_asset, "send_with_unity_instance", send)
    server = FastMCP("asset-move-contract")
    register_all_tools(server, project_scoped_tools=False)
    server.enable(tags={"group:assets"}, components={"tool"})
    async with Client(server, mode=mode) as client:
        tools = {tool.name: tool for tool in await client.list_tools()}
        for key in ("preview", "move"):
            row = contract[key]
            name = row["tool"]
            description = await client.call_tool(
                "manage_tools", {"action": "describe", "tool": name, "operation": row["action"]}
            )
            card = description.structured_content["operation"]
            assert {"path", "destination"} <= set(card["required_parameters"])
            assert card["required_permissions"] == row["permissions"]
            assert card["effects"]["readOnlyHint"] is row["readOnly"]
            schema = card["success_data_schema"]
            assert set(schema["required"]) == row["result"].keys()
            assert {field: spec["type"] for field, spec in schema["properties"].items()} == row[
                "result"
            ]
            for field in row["result"]:
                assert field in tools[name].description
            arguments = {"action": row["action"], "path": source, "destination": destination}
            input_contract = Draft202012Validator(tools[name].input_schema)
            input_contract.validate(arguments)
            for missing in ("path", "destination"):
                invalid = {k: v for k, v in arguments.items() if k != missing}
                assert not input_contract.is_valid(invalid)
                calls = send.await_count
                result = await client.call_tool(name, invalid, raise_on_error=False)
                assert result.is_error or result.structured_content["success"] is False
                assert send.await_count == calls
            data = (
                {
                    "path": source,
                    "destination": destination,
                    "guid": "a" * 32,
                    "referenceRevision": "mcp-ref-v1_" + "b" * 64,
                    "scanComplete": True,
                }
                if row["readOnly"]
                else {"path": destination, "verification": {}}
            )
            send.return_value = {"success": True, "data": data}
            invoked = await client.call_tool(name, arguments)
            Draft202012Validator(schema).validate(invoked.structured_content["data"])
            assert send.await_args.args[2] == name
            wire = send.await_args.args[3]
            assert {k: wire[k] for k in arguments} == arguments
        for case in contract["inputs"]:
            arguments = {
                k: source if v == "SRC" else destination if v == "DST" else v
                for k, v in case["arguments"].items()
            }
            assert input_contract.is_valid(arguments) is case["valid"], case["id"]
            calls = send.await_count
            result = await client.call_tool("asset_edit", arguments, raise_on_error=False)
            accepted = not result.is_error and result.structured_content["success"] is True
            assert accepted is case["valid"], case["id"]
            assert send.await_count == calls + int(accepted)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "key,grants,allowed",
    [
        ("preview", ["read"], True),
        ("move", ["read"], False),
        ("move", ["edit"], False),
        ("move", ["run"], False),
        ("move", ["edit", "run"], True),
    ],
)
async def test_preview_and_move_enforce_grants_at_dispatch(
    command_hub, monkeypatch, contract, key, grants, allowed
):
    monkeypatch.setattr(config, "http_remote_hosted", True)
    await command_hub.registry.unregister("session")
    await command_hub.registry.register("session", "Fixture", "hash", "6000", user_id="alice")
    row = contract[key]
    claims = {
        "request": {"unity_mcp_permissions": grants},
        "plugin": {"unity_mcp_permissions": row["permissions"]},
    }
    async with permission_service(monkeypatch, claims) as fixture:
        requester = (await fixture.service.validate("request")).lease
        plugin = (await fixture.service.validate("plugin")).lease
        command_hub.websocket.state.user_id = "alice"
        setattr(command_hub.websocket.state, AUTHENTICATED_LEASE_STATE, plugin)
        with bind_credential_lease(requester):
            result = await PluginHub.send_command(
                "session",
                row["tool"],
                {
                    "action": row["action"],
                    "path": "Assets/Shop.prefab",
                    "destination": "Assets/Moved.prefab",
                },
            )
        assert result["success"] is allowed
        assert len(command_hub.sent) == int(allowed)
        if not allowed:
            assert result["error"] == "permission_denied"
