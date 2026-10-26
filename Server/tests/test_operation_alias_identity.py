"""A canonical adapter name never grants trust to its captured replacement.

SDK cases retain real registration/context injection with owned target and peer
boundaries substituted; they do not claim HTTP authentication or native wire proof.
"""

import inspect
from unittest.mock import AsyncMock

from fastmcp import Client, Context as SdkContext, FastMCP
import pytest

from core.config import config
from services.registry import operation_invocation as invocation, tool_registry
from services.registry.capability_adapter import register_capability
from services.registry.capability_catalog import CAPABILITIES
from services.registry.operation_context import active_operation
from services.registry.operation_scheduler import OperationScope
from services.registry.operation_target import OperationTarget


@pytest.mark.asyncio
@pytest.mark.parametrize("replacement", [False, True])
@pytest.mark.parametrize("surface", ["direct", "legacy", "2026-07-28"])
async def test_actual_wrapped_alias_admits_only_its_real_canonical_callback_as_shared(
    monkeypatch, replacement, surface
):
    import services.tools.manage_physics as physics
    import services.tools as tools

    monkeypatch.setattr(config, "http_remote_hosted", False)
    monkeypatch.setattr(tool_registry, "_tool_registry", list(tool_registry._tool_registry))
    # A previous real SDK registration moves injected ctx after required action.
    # Preserve that supported signature and restore its metadata after this test.
    for name in ("__signature__", "__fastmcp__"):
        monkeypatch.setattr(
            physics.manage_physics, name, getattr(physics.manage_physics, name, None), raising=False
        )
    FastMCP("previous-source-registration").tool()(physics.manage_physics)
    feature = next(item for item in CAPABILITIES if item.name == "query_physics")
    observed, effects = [], []

    async def replacement_handler(**kwargs):
        observed.append(active_operation().request.mode)
        effects.append("replacement executed")
        return {"success": True}

    replacement_handler.__signature__ = inspect.signature(physics.manage_physics)

    async def peer(*args, **kwargs):
        observed.append(active_operation().request.mode)
        return {"success": True}

    async def target(*args, **kwargs):
        return OperationTarget(OperationScope("local", "owned", "g"), lambda: True, True)

    class Context:
        async def get_state(self, key, default=None):
            return "Owned@owned" if key == "unity_instance" else default

    monkeypatch.setattr(invocation, "capture_target", target)
    monkeypatch.setattr(physics, "send_with_unity_instance", peer)
    implementation = replacement_handler if replacement else physics.manage_physics
    alias = register_capability(feature, implementation)
    assert invocation.trusted_tool(alias, feature.name) is (not replacement)
    arguments = {"action": "raycast", "origin": [0.0, 0.0, 0.0], "direction": [0.0, 0.0, 1.0]}
    if surface == "direct":
        result = await invocation.wrap_tool(alias, feature.name, feature.source)(
            ctx=Context(), **arguments
        )
    else:
        definition = next(
            item for item in tool_registry.get_registered_tools() if item["func"] is alias
        )
        monkeypatch.setenv("UNITY_MCP_TOOL_PROFILE", "full")
        monkeypatch.setattr(config, "transport_mode", "stdio")
        monkeypatch.setattr(tools, "discover_modules", lambda *args: iter(()))
        monkeypatch.setattr(tools, "get_registered_tools", lambda: [definition])
        selected = AsyncMock(return_value="Owned@owned")
        monkeypatch.setattr(tools, "get_unity_instance_from_context", selected)
        monkeypatch.setattr(physics, "get_unity_instance_from_context", selected)
        server = FastMCP("registered-alias-operation-identity")
        tools.register_all_tools(server)
        async with Client(server, mode=surface) as client:
            published = await client.list_tools()
            assert len(published) == 1 and published[0].name == feature.name
            assert "ctx" not in published[0].input_schema.get("properties", {})
            result = (await client.call_tool(feature.name, arguments)).data
        if not replacement:
            assert isinstance(selected.await_args.args[0], SdkContext)
    assert result["success"] is True
    assert observed == ["exclusive" if replacement else "shared_read"]
    assert effects == (["replacement executed"] if replacement else [])


@pytest.mark.asyncio
async def test_real_builtin_borrowed_under_another_name_remains_exclusive(monkeypatch):
    import services.tools.manage_script as scripts

    observed = []

    async def target(*args, **kwargs):
        return OperationTarget(OperationScope("local", "owned", "g"), lambda: True, True)

    async def mutation(*args, **kwargs):
        observed.append(active_operation().request.mode)
        return {"success": True}

    class Context:
        async def get_state(self, key, default=None):
            return "Owned@owned" if key == "unity_instance" else default

    monkeypatch.setattr(config, "http_remote_hosted", False)
    monkeypatch.setattr(invocation, "capture_target", target)
    monkeypatch.setattr(scripts, "send_mutation", mutation)
    assert invocation.trusted_tool(scripts.delete_script, "get_sha") is False
    result = await invocation.wrap_tool(scripts.delete_script, "get_sha", "manage_script")(
        Context(), uri="Assets/Owned.cs"
    )
    assert result["success"] is True
    assert observed == ["exclusive"]
