"""Scoped snapshot authority covers callback-capable UI dispatch."""

import pytest
from fastmcp import Client, Context, FastMCP
from fastmcp.exceptions import ToolError
from mcp.shared.exceptions import MCPError

from core.config import config
from models.models import ToolDefinitionModel
from services.credential_lease import bind_credential_lease
from services.operation_permissions import Permission, required_permissions
from tests.http.test_remote_permissions import permission_service
from tests.http.test_remote_catalog_gating import RequesterMiddleware
from tests.test_http_command_outcome import command_hub  # pylint: disable=unused-import
from transport.plugin_hub import PluginHub
from transport.remote_auth_middleware import AUTHENTICATED_LEASE_STATE


FULL = {Permission.READ, Permission.EDIT, Permission.RUN}


@pytest.mark.parametrize("native", [False, True])
@pytest.mark.parametrize("outer", [None, "batch_execute", "batch_validate", "batch_apply_preview"])
@pytest.mark.parametrize("framework", ["ugui", "uitoolkit", None, "UGUI", "", 0, [], {}])
def test_capture_requires_callback_authority(native, outer, framework):
    name, params = "capture_ui_snapshot", {"framework": framework, "target": 7}
    if outer:
        name, params = outer, {"commands": [{"tool": name, "params": params}]}
    assert required_permissions(name, params, native=native) == FULL


def test_missing_framework_cannot_claim_read_only_mode():
    assert required_permissions("capture_ui_snapshot", {"target": 7}, native=True) == FULL


@pytest.mark.asyncio
@pytest.mark.parametrize("outer", [None, "batch_execute", "batch_validate", "batch_apply_preview"])
@pytest.mark.parametrize("restricted", ["request", "plugin", None])
@pytest.mark.parametrize("missing", ["read", "edit", "run"])
@pytest.mark.parametrize("framework", ["ugui", "uitoolkit"])
async def test_snapshot_checks_both_authorities_before_dispatch(
    command_hub, monkeypatch, outer, restricted, missing, framework
):
    monkeypatch.setattr(config, "http_remote_hosted", True)
    await command_hub.registry.unregister("session")
    await command_hub.registry.register("session", "Fixture", "hash", "6000", user_id="alice")
    claims = {
        side: {
            "unity_mcp_permissions": [
                grant for grant in ("read", "edit", "run") if side != restricted or grant != missing
            ]
        }
        for side in ("request", "plugin")
    }
    async with permission_service(monkeypatch, claims) as fixture:
        requester = (await fixture.service.validate("request")).lease
        plugin = (await fixture.service.validate("plugin")).lease
        command_hub.websocket.state.user_id = "alice"
        setattr(command_hub.websocket.state, AUTHENTICATED_LEASE_STATE, plugin)
        name, params = "capture_ui_snapshot", {"framework": framework, "target": 7}
        if outer:
            name, params = outer, {"commands": [{"tool": name, "params": params}]}
        with bind_credential_lease(requester):
            result = await PluginHub.send_command("session", name, params)
        assert result["success"] is (restricted is None)
        assert len(command_hub.sent) == int(restricted is None)
        if restricted:
            assert result["error"] == "permission_denied"


@pytest.mark.asyncio
@pytest.mark.parametrize("restricted", ["request", "plugin", None])
@pytest.mark.parametrize("framework", ["ugui", "uitoolkit"])
async def test_public_tool_rejects_insufficient_authority_before_handler(
    command_hub, monkeypatch, restricted, framework
):
    monkeypatch.setattr(config, "http_remote_hosted", True)
    await command_hub.registry.unregister("session")
    await command_hub.registry.register("session", "Fixture", "hash", "6000", user_id="alice")
    await command_hub.registry.register_tools_for_session(
        "session", [ToolDefinitionModel(name="capture_ui_snapshot")]
    )
    monkeypatch.setattr(
        "transport.unity_instance_middleware.get_registered_tools",
        lambda: [{"name": "capture_ui_snapshot", "unity_target": "capture_ui_snapshot"}],
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
        server = FastMCP("snapshot-admission")
        server.add_middleware(RequesterMiddleware(requester))
        calls = []

        @server.tool(name="capture_ui_snapshot")
        async def capture(ctx: Context, framework: str, target: int) -> dict:
            calls.append((framework, target))
            return {"success": True}

        async with Client(server) as client:
            arguments = {"framework": framework, "target": 7, "unity_instance": "Fixture@hash"}
            if restricted:
                with pytest.raises((ToolError, MCPError)):
                    await client.call_tool("capture_ui_snapshot", arguments)
            else:
                assert (await client.call_tool("capture_ui_snapshot", arguments)).data["success"]
        assert calls == ([] if restricted else [(framework, 7)])
        assert command_hub.sent == []


@pytest.mark.parametrize("name", ["manage_ui", "ui_inspect"])
@pytest.mark.parametrize("native", [False, True])
@pytest.mark.parametrize("outer", [None, "batch_execute", "batch_validate", "batch_apply_preview"])
def test_visual_tree_requires_callback_authority(name, native, outer):
    params = {"action": "get_visual_tree", "target": "Fixture"}
    if outer:
        name, params = outer, {"commands": [{"tool": name, "params": params}]}
    assert required_permissions(name, params, native=native) == FULL


@pytest.mark.parametrize(
    "name,action", [("manage_ui", "ping"), ("manage_ui", "read"), ("ui_inspect", "diagnose")]
)
def test_other_ui_reads_remain_read_only(name, action):
    assert required_permissions(name, {"action": action}) == {Permission.READ}


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["manage_ui", "ui_inspect"])
@pytest.mark.parametrize("outer", [None, "batch_execute", "batch_validate", "batch_apply_preview"])
@pytest.mark.parametrize("restricted", ["request", "plugin", None])
async def test_visual_tree_checks_both_authorities_before_dispatch(
    command_hub, monkeypatch, name, outer, restricted
):
    monkeypatch.setattr(config, "http_remote_hosted", True)
    await command_hub.registry.unregister("session")
    await command_hub.registry.register("session", "Fixture", "hash", "6000", user_id="alice")
    claims = {
        side: {"unity_mcp_permissions": ["read"] if side == restricted else ["read", "edit", "run"]}
        for side in ("request", "plugin")
    }
    async with permission_service(monkeypatch, claims) as fixture:
        requester = (await fixture.service.validate("request")).lease
        plugin = (await fixture.service.validate("plugin")).lease
        command_hub.websocket.state.user_id = "alice"
        setattr(command_hub.websocket.state, AUTHENTICATED_LEASE_STATE, plugin)
        params = {"action": "get_visual_tree", "target": "Fixture"}
        if outer:
            name, params = outer, {"commands": [{"tool": name, "params": params}]}
        with bind_credential_lease(requester):
            result = await PluginHub.send_command("session", name, params)
        assert result["success"] is (restricted is None)
        assert len(command_hub.sent) == int(restricted is None)
        if restricted:
            assert result["error"] == "permission_denied"


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["manage_ui", "ui_inspect"])
@pytest.mark.parametrize("restricted", ["request", "plugin", None])
async def test_public_visual_tree_denies_before_handler(command_hub, monkeypatch, name, restricted):
    monkeypatch.setattr(config, "http_remote_hosted", True)
    await command_hub.registry.unregister("session")
    await command_hub.registry.register("session", "Fixture", "hash", "6000", user_id="alice")
    await command_hub.registry.register_tools_for_session(
        "session", [ToolDefinitionModel(name=name)]
    )
    monkeypatch.setattr(
        "transport.unity_instance_middleware.get_registered_tools",
        lambda: [{"name": name, "unity_target": name}],
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
        server = FastMCP("visual-tree-admission")
        server.add_middleware(RequesterMiddleware(requester))
        calls = []

        @server.tool(name=name)
        async def inspect(ctx: Context, action: str, target: str) -> dict:
            calls.append((action, target))
            return {"success": True}

        async with Client(server) as client:
            arguments = {
                "action": "get_visual_tree",
                "target": "Fixture",
                "unity_instance": "Fixture@hash",
            }
            if restricted:
                with pytest.raises((ToolError, MCPError)):
                    await client.call_tool(name, arguments)
            else:
                assert (await client.call_tool(name, arguments)).data["success"]
        assert calls == ([] if restricted else [("get_visual_tree", "Fixture")])
        assert command_hub.sent == []
