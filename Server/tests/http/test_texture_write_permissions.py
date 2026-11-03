"""Remote texture writes must reach an Editor that checks its destination."""

import importlib
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from core.config import config
from services.credential_lease import bind_credential_lease
from services.operation_permissions import required_permissions
from tests.http.test_remote_permissions import permission_service
from tests.test_http_command_outcome import command_hub  # pylint: disable=unused-import
from transport.plugin_hub import PluginHub
from transport.remote_auth_middleware import AUTHENTICATED_LEASE_STATE


@pytest.mark.parametrize(
    "action",
    [
        "create",
        "create_sprite",
        "apply_pattern",
        "apply_gradient",
        "apply_noise",
        "modify",
        "delete",
    ],
)
def test_remote_texture_writes_use_distinct_wire_actions(action):
    name = "manage_texture"
    raw = {"action": action}
    checked = {"action": action + "_checked"}
    assert required_permissions(name, raw) == {"edit"}
    assert required_permissions(name, raw, native=True) == {"edit", "run"}
    assert required_permissions(name, checked, native=True) == {"edit"}
    for batch in ("batch_execute", "batch_validate", "batch_apply_preview"):
        original = {"commands": [{"tool": name, "params": raw}]}
        guarded = {"commands": [{"tool": name, "params": checked}]}
        assert required_permissions(batch, original, native=True) == {"edit", "run"}
        assert required_permissions(batch, guarded, native=True) == {"edit"}
    assert required_permissions("batch_execute", original) == {"edit"}


@pytest.mark.asyncio
@pytest.mark.parametrize("batch", [False, True])
@pytest.mark.parametrize(
    "action,request_grants,plugin_grants,allowed",
    [
        ("create", ["edit"], ["edit", "run"], False),
        ("delete", ["edit", "run"], ["edit"], False),
        ("create_checked", ["edit"], ["edit"], True),
        ("delete", ["edit", "run"], ["edit", "run"], True),
    ],
)
async def test_native_texture_policy_intersects_both_principals(
    command_hub, monkeypatch, batch, action, request_grants, plugin_grants, allowed
):
    monkeypatch.setattr(config, "http_remote_hosted", True)
    await command_hub.registry.unregister("session")
    await command_hub.registry.register("session", "Fixture", "hash", "6000", user_id="alice")
    claims = {
        "request": {"unity_mcp_permissions": request_grants},
        "plugin": {"unity_mcp_permissions": plugin_grants},
    }
    name, params = "manage_texture", {"action": action, "path": "Assets/Editor/Victim.cs"}
    if batch:
        name, params = "batch_execute", {"commands": [{"tool": name, "params": params}]}
    async with permission_service(monkeypatch, claims) as fixture:
        requester = (await fixture.service.validate("request")).lease
        editor = (await fixture.service.validate("plugin")).lease
        command_hub.websocket.state.user_id = "alice"
        setattr(command_hub.websocket.state, AUTHENTICATED_LEASE_STATE, editor)
        with bind_credential_lease(requester):
            response = await PluginHub.send_command("session", name, params)
        assert response["success"] is allowed
        assert len(command_hub.sent) == int(allowed)
        if not allowed:
            assert response["error"] == "permission_denied"


@pytest.mark.asyncio
async def test_public_texture_and_batch_forward_guards_before_dispatch(monkeypatch):
    textures = importlib.import_module("services.tools.manage_texture")
    batch = importlib.import_module("services.tools.batch_execute")
    monkeypatch.setattr(config, "http_remote_hosted", True)
    send = AsyncMock(return_value={"success": True})
    for module in (textures, batch):
        monkeypatch.setattr(module, "get_unity_instance_from_context", AsyncMock(return_value=None))
        monkeypatch.setattr(module, "send_with_unity_instance", send)
    monkeypatch.setattr(textures, "preflight", AsyncMock(return_value=None))
    monkeypatch.setattr(batch, "_get_max_commands_from_editor_state", AsyncMock(return_value=5))
    ctx = SimpleNamespace()
    args = {"action": "create", "path": "Assets/picture.png"}
    async with permission_service(
        monkeypatch, {"key": {"unity_mcp_permissions": ["edit"]}}
    ) as fixture:
        lease = (await fixture.service.validate("key")).lease
        with bind_credential_lease(lease):
            rejected = await textures.manage_texture(ctx, "create", path="Assets/Victim.cs")
            assert rejected["success"] is False
            send.assert_not_awaited()
            await textures.manage_texture(ctx, **args)
            assert send.await_args.args[3]["action"] == "create_checked"
            for options, wire in (
                ({}, "batch_execute"),
                ({"validate_only": True}, "batch_validate"),
                ({"preview": {}}, "batch_apply_preview"),
            ):
                await batch.batch_execute(
                    ctx, [{"tool": "manage_texture", "params": args}], **options
                )
                assert send.await_args.args[2] == wire
                assert (
                    send.await_args.args[3]["commands"][0]["params"]["action"] == "create_checked"
                )
    assert args["action"] == "create"
