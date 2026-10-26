"""Actual credential grants fence a selected plugin before whole-tool queuing."""

from types import SimpleNamespace

import pytest

from core.config import config
from services.api_key_service import ValidationResult
from services.operation_permissions import OperationPermissionError
from services.registry import operation_invocation as invocation
from services.registry.operation_context import scheduler
from services.registry.operation_target import capture_http_target
from tests.remote_credentials import remote_credentials
from transport.plugin_hub import PluginHub
from transport.plugin_registry import PluginRegistry


@pytest.mark.asyncio
@pytest.mark.parametrize("name,allowed", [("get_sha", True), ("create_script", False)])
async def test_authoritative_principal_and_selected_plugin_grants_precede_writer_queue(
    monkeypatch, name, allowed
):
    monkeypatch.setattr(config, "http_remote_hosted", True)
    registry = PluginRegistry()
    await registry.register("owned", "Owned", "deadbeef", "fixture", user_id="alice")
    socket = SimpleNamespace(
        state=SimpleNamespace(
            user_id="alice",
            plugin_generation="generation",
            plugin_arbitration_generation=None,
            plugin_features=frozenset(),
        )
    )
    hub = SimpleNamespace(
        _registry=registry,
        _connections={"owned": socket},
        _supports=PluginHub._supports,
        _socket_lease=PluginHub._socket_lease,
    )
    entered = []

    class Context:
        async def get_state(self, key, default=None):
            return {"unity_instance": "Owned@deadbeef", "user_id": "stale-other-user"}.get(
                key, default
            )

    async def callback(ctx):
        entered.append(True)
        return "observed"

    async with remote_credentials({"requester": "alice", "plugin": "alice"}) as auth:

        async def verdict(key):
            return ValidationResult(
                valid=True,
                user_id="alice",
                metadata={
                    "unity_mcp_permissions": ["read"]
                    if key == "plugin"
                    else ["read", "edit", "run", "test"]
                },
            )

        monkeypatch.setattr(auth.service, "_validate_external", verdict)
        await auth.attach_socket(socket, "plugin")
        target = await capture_http_target("owned", hub=hub)

        async def capture(instance, *, user_id=None):
            assert instance == "Owned@deadbeef" and user_id == "alice"
            return target

        monkeypatch.setattr(invocation, "capture_target", capture)
        async with auth.requester("requester"):
            call = invocation.wrap_tool(callback, name, "manage_script")
            if allowed:
                assert await call(Context()) == "observed"
            else:
                with pytest.raises(OperationPermissionError):
                    await call(Context())
    assert entered == ([True] if allowed else [])
    assert scheduler().snapshot() == {
        "instances": 0,
        "active": 0,
        "waiting": 0,
        "retained_bytes": 0,
    }
