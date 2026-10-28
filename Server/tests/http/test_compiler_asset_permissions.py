"""Compiler-sensitive asset admission covers public aliases and final socket dispatch."""

import pytest

from core.config import config
from services.credential_lease import bind_credential_lease
from services.operation_permissions import Permission, required_permissions
from tests.http.test_remote_permissions import permission_service
from tests.test_http_command_outcome import command_hub  # pylint: disable=unused-import
from transport.plugin_hub import PluginHub
from transport.remote_auth_middleware import AUTHENTICATED_LEASE_STATE


@pytest.mark.parametrize("action", "import create modify delete duplicate move rename".split())
@pytest.mark.parametrize("name", ["manage_asset", "asset_edit"])
def test_asset_content_and_tree_mutations_require_compile_authority(name, action):
    # A path/extension cannot prove a folder, dependency or importer is inert.
    params = {"action": action, "path": "Assets/Innocent.png"}
    assert required_permissions(name, params) == {Permission.EDIT, Permission.RUN}
    for outer in ("batch_execute", "batch_validate", "batch_apply_preview"):
        assert required_permissions(outer, {"commands": [{"tool": name, "params": params}]}) == {
            Permission.EDIT,
            Permission.RUN,
        }


@pytest.mark.parametrize("action", ["create_folder", "set_labels", "add_labels", "remove_labels"])
def test_inert_asset_metadata_mutations_preserve_edit_only_authority(action):
    assert required_permissions("manage_asset", {"action": action}) == {Permission.EDIT}


@pytest.mark.parametrize("action", ["deploy_package", "restore_package"])
def test_package_replacement_requires_compile_authority(action):
    assert required_permissions("manage_editor", {"action": action}) == {
        Permission.EDIT,
        Permission.RUN,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "name,params",
    [
        ("manage_asset", {"action": "move", "path": "Assets/Folder"}),
        ("asset_edit", {"action": "import", "path": "Assets/Fixture.dll"}),
        ("manage_editor", {"action": "deploy_package"}),
        ("manage_editor", {"action": "restore_package"}),
        (
            "batch_validate",
            {"commands": [{"tool": "asset_edit", "params": {"action": "delete"}}]},
        ),
    ],
)
@pytest.mark.parametrize(
    "request_grants,plugin_grants,allowed",
    [
        (["edit"], ["edit", "run"], False),
        (["edit", "run"], ["edit"], False),
        (["run"], ["edit", "run"], False),
        (["edit", "run"], ["run"], False),
        (["edit", "run"], ["edit", "run"], True),
    ],
)
async def test_compiler_effects_intersect_both_leases_before_native_dispatch(
    command_hub, monkeypatch, name, params, request_grants, plugin_grants, allowed
):
    monkeypatch.setattr(config, "http_remote_hosted", True)
    await command_hub.registry.unregister("session")
    await command_hub.registry.register("session", "Fixture", "hash", "6000", user_id="alice")
    claims = {
        "request": {"unity_mcp_permissions": request_grants},
        "plugin": {"unity_mcp_permissions": plugin_grants},
    }
    async with permission_service(monkeypatch, claims) as fixture:
        requester = (await fixture.service.validate("request")).lease
        plugin = (await fixture.service.validate("plugin")).lease
        command_hub.websocket.state.user_id = "alice"
        setattr(command_hub.websocket.state, AUTHENTICATED_LEASE_STATE, plugin)
        with bind_credential_lease(requester):
            result = await PluginHub.send_command("session", name, params)
        assert result["success"] is allowed
        assert len(command_hub.sent) == (1 if allowed else 0)
        assert PluginHub._pending == {}
        if not allowed:
            assert result["error"] == "permission_denied"
