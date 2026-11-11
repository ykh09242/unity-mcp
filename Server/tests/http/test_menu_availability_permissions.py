"""Menu declaration reads and callback-capable availability have separate authority."""

import pytest
from fastmcp import Client, FastMCP
from fastmcp.exceptions import ToolError
from mcp.shared.exceptions import MCPError

from core.config import config
from models.models import ToolDefinitionModel
from services.credential_lease import bind_credential_lease
from services.operation_permissions import Permission, required_permissions
from tests.http.test_remote_permissions import permission_service
from tests.http.test_remote_catalog_gating import RequesterMiddleware
from tests.test_http_command_outcome import command_hub  # pylint: disable=unused-import
from services.tools.execute_menu_item import execute_menu_item
from transport.plugin_hub import PluginHub
from transport.remote_auth_middleware import AUTHENTICATED_LEASE_STATE


FULL = {Permission.READ, Permission.EDIT, Permission.RUN}


@pytest.mark.parametrize("action", ["inspect", "exists"])
def test_declaration_reads_preserve_read_only_grants(action):
    assert required_permissions("execute_menu_item", {"action": action}) == {Permission.READ}
    assert required_permissions(
        "inspect_menu_item_declaration", {"action": "evaluate_availability"}, native=True
    ) == {Permission.READ}


@pytest.mark.parametrize("outer", [None, "batch_execute", "batch_validate", "batch_apply_preview"])
@pytest.mark.parametrize("command", ["evaluate_menu_item", "inspect_menu_item"])
@pytest.mark.parametrize("action", [None, "inspect", "exists", "evaluate_availability", "execute"])
def test_availability_requires_combined_callback_grants_even_with_inspect_payload(
    outer, command, action
):
    assert required_permissions("execute_menu_item", {"action": "evaluate_availability"}) == FULL
    name, params = command, {} if action is None else {"action": action}
    if outer:
        name, params = outer, {"commands": [{"tool": name, "params": params}]}
    assert required_permissions(name, params, native=True) == FULL


@pytest.mark.parametrize("command", ["execute_menu_item", "execute_menu_item_observed"])
def test_native_execution_requirements_remain_run(command):
    for action in ("inspect", "exists", "evaluate_availability", "execute", None):
        assert required_permissions(command, {"action": action}, native=True) == {Permission.RUN}


@pytest.mark.asyncio
@pytest.mark.parametrize("outer", [None, "batch_execute", "batch_validate", "batch_apply_preview"])
@pytest.mark.parametrize("restricted", ["request", "plugin", None])
@pytest.mark.parametrize("missing", ["read", "edit", "run", "edit_and_run"])
@pytest.mark.parametrize("command", ["evaluate_menu_item", "inspect_menu_item"])
async def test_availability_intersects_both_authorities_before_dispatch(
    command_hub, monkeypatch, outer, restricted, missing, command
):
    monkeypatch.setattr(config, "http_remote_hosted", True)
    await command_hub.registry.unregister("session")
    await command_hub.registry.register("session", "Fixture", "hash", "6000", user_id="alice")
    old_peer_callbacks = []
    original_send = command_hub.websocket.send_json

    async def old_peer_dispatch(message):
        # The older inspector runs GetEnabled when consent already exists.
        # Model its callback-capable handler at the real post-authorization socket boundary.
        if message["type"] == "execute":
            members = (
                message["params"]["commands"]
                if message["name"] in {"batch_execute", "batch_validate", "batch_apply_preview"}
                else [{"tool": message["name"]}]
            )
            old_peer_callbacks.extend(
                member["tool"] for member in members if member["tool"] == "inspect_menu_item"
            )
        await original_send(message)

    command_hub.websocket.send_json = old_peer_dispatch
    claims = {
        side: {
            "unity_mcp_permissions": [
                grant
                for grant in ("read", "edit", "run")
                if side != restricted
                or grant not in ({"edit", "run"} if missing == "edit_and_run" else {missing})
            ]
        }
        for side in ("request", "plugin")
    }
    async with permission_service(monkeypatch, claims) as fixture:
        requester = (await fixture.service.validate("request")).lease
        plugin = (await fixture.service.validate("plugin")).lease
        command_hub.websocket.state.user_id = "alice"
        setattr(command_hub.websocket.state, AUTHENTICATED_LEASE_STATE, plugin)
        name, params = command, {"menuPath": "Fixture/Owned", "action": "inspect"}
        if outer:
            name, params = outer, {"commands": [{"tool": name, "params": params}]}
        with bind_credential_lease(requester):
            result = await PluginHub.send_command("session", name, params)
        assert result["success"] is (restricted is None), result
        assert len(command_hub.sent) == int(restricted is None)
        assert old_peer_callbacks == (
            ["inspect_menu_item"] if restricted is None and command == "inspect_menu_item" else []
        )
        if restricted:
            assert result["error"] == "permission_denied"


@pytest.mark.asyncio
@pytest.mark.parametrize("restricted", ["request", "plugin", None])
@pytest.mark.parametrize("action", ["inspect", "exists", "evaluate_availability"])
async def test_public_menu_action_authority_precedes_native_dispatch(
    command_hub, monkeypatch, restricted, action
):
    monkeypatch.setattr(config, "http_remote_hosted", True)
    monkeypatch.setattr(config, "transport_mode", "http")
    await command_hub.registry.unregister("session")
    await command_hub.registry.register("session", "Fixture", "hash", "6000", user_id="alice")
    await command_hub.registry.register_tools_for_session(
        "session", [ToolDefinitionModel(name="execute_menu_item")]
    )
    monkeypatch.setattr(
        "transport.unity_instance_middleware.get_registered_tools",
        lambda: [{"name": "execute_menu_item", "unity_target": "execute_menu_item"}],
    )
    claims = {
        side: {"unity_mcp_permissions": ["read"] if side == restricted else ["read", "edit", "run"]}
        for side in ("request", "plugin")
    }
    async with permission_service(monkeypatch, claims) as fixture:
        requester = (await fixture.service.validate("request")).lease
        plugin = (await fixture.service.validate("plugin")).lease
        command_hub.websocket.state.user_id = "alice"
        setattr(command_hub.websocket.state, AUTHENTICATED_LEASE_STATE, plugin)
        server = FastMCP("menu-action-admission")
        server.add_middleware(RequesterMiddleware(requester))
        server.tool(name="execute_menu_item")(execute_menu_item)
        async with Client(server) as client:
            arguments = {
                "menu_path": "Fixture/Owned",
                "action": action,
                "unity_instance": "Fixture@hash",
            }
            if restricted and action == "evaluate_availability":
                with pytest.raises((ToolError, MCPError)):
                    await client.call_tool("execute_menu_item", arguments)
                assert command_hub.sent == []
            else:
                result = await client.call_tool("execute_menu_item", arguments)
                assert result.structured_content["success"] is True, result
                [sent] = command_hub.sent
                assert sent["name"] == (
                    "evaluate_menu_item"
                    if action == "evaluate_availability"
                    else "inspect_menu_item_declaration"
                )
