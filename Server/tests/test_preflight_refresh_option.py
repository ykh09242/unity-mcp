"""The public refresh option suppresses optional imports while retaining native admission."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastmcp import Client, FastMCP

from core.config import config
from services.registry import operation_invocation
from services.resources import editor_state
from services.tools import find_gameobjects
from services.tools import manage_components, manage_prefabs, manage_scene, preflight, refresh_unity
from services.tools import register_all_tools
from transport.legacy import unity_connection
from transport.plugin_hub import PluginHub
from transport.unity_instance_middleware import UnityInstanceMiddleware


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("module", "arguments"),
    [
        (manage_prefabs, {"action": "get_info", "prefab_path": "Assets/Panel.prefab"}),
        (
            manage_prefabs,
            {"action": "trace_property", "target": 314, "property_path": "m_Intensity"},
        ),
        (find_gameobjects, {"search_term": "Root"}),
    ],
)
async def test_direct_nonboolean_refresh_fails_before_editor_access(monkeypatch, module, arguments):
    target = AsyncMock()
    dispatch = AsyncMock()
    monkeypatch.setattr(module, "get_unity_instance_from_context", target)
    monkeypatch.setattr(module, "send_with_unity_instance", dispatch)
    handler = getattr(module, module.__name__.rsplit(".", 1)[1])
    result = await handler(None, **arguments, refresh_if_dirty="false")
    assert result["success"] is False and "refresh_if_dirty" in result["message"]
    target.assert_not_awaited()
    dispatch.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["legacy", "2026-07-28"])
async def test_public_dirty_preflight_across_families(monkeypatch, mode):
    editor = SimpleNamespace(id="Project@deadbeef", name="Project", hash="deadbeef", port=6400)
    monkeypatch.setenv("UNITY_MCP_TOOL_PROFILE", "full")
    monkeypatch.setattr(config, "transport_mode", "stdio")
    monkeypatch.setattr(config, "http_remote_hosted", False)
    monkeypatch.setattr(PluginHub, "is_configured", lambda: False)
    pool = unity_connection.UnityConnectionPool()
    monkeypatch.setattr(unity_connection, "get_unity_connection_pool", lambda: pool)
    monkeypatch.setattr(pool, "discover_all_instances", lambda **_: [editor])
    monkeypatch.setattr(operation_invocation, "capture_target", AsyncMock(return_value=None))
    monkeypatch.setattr(preflight, "_in_pytest", lambda: False)
    allowed = AsyncMock(return_value=True)
    monkeypatch.setattr(preflight, "remote_operation_allowed", allowed)
    state = AsyncMock(
        return_value={"success": True, "data": {"assets": {"external_changes_dirty": True}}}
    )
    monkeypatch.setattr(editor_state, "get_editor_state_authoritative", state)
    refresh = AsyncMock(return_value={"success": True})
    monkeypatch.setattr(refresh_unity, "refresh_unity", refresh)
    dispatch = AsyncMock(return_value={"success": True, "data": {}})
    for module in (manage_scene, manage_components, manage_prefabs):
        monkeypatch.setattr(module, "send_with_unity_instance", dispatch)

    server = FastMCP("dirty-preflight")
    register_all_tools(server)
    server.add_middleware(UnityInstanceMiddleware())
    async with Client(server, mode=mode) as client:
        listed = {tool.name: tool for tool in await client.list_tools()}
        for tool in ("scene_inspect", "manage_components", "prefab_contents", "run_tests"):
            field = listed[tool].input_schema["properties"]["refresh_if_dirty"]
            assert field["type"] == "boolean" and field["default"] is True
        for tool in (
            "asset_bundles",
            "asset_labels",
            "asset_reference_diagnostics",
            "scene_reference_diagnostics",
        ):
            assert "refresh_if_dirty" not in listed[tool].input_schema["properties"]
        for tool, action, applicable in (
            ("scene_inspect", "get_active", True),
            ("prefab_overrides", "list_overrides", True),
            ("prefab_overrides", "trace_property", False),
        ):
            result = await client.call_tool(
                "manage_tools", {"action": "describe", "tool": tool, "operation": action}
            )
            assert (
                "refresh_if_dirty" in result.structured_content["operation"]["input_fields"]
            ) == applicable

        target = editor.id
        draft = {"action": "get_active", "refresh_if_dirty": False, "unity_instance": target}
        prepared = await client.call_tool(
            "manage_tools", {"action": "prepare", "tool": "scene_inspect", "arguments": draft}
        )
        assert prepared.structured_content["prepared_call"]["arguments"] == draft
        state.assert_not_awaited()
        dispatch.assert_not_awaited()
        for name, arguments in (
            ("scene_inspect", {"action": "get_active"}),
            (
                "manage_components",
                {"action": "add", "component_type": "BoxCollider", "target": "123"},
            ),
        ):
            for option, expected in (
                ({"refresh_if_dirty": False}, 0),
                ({}, 1),
                ({"refresh_if_dirty": True}, 1),
            ):
                state.reset_mock()
                refresh.reset_mock()
                result = await client.call_tool(
                    name, {"unity_instance": target, **arguments, **option}
                )
                assert result.structured_content["success"], result.content
                state.assert_awaited_once()
                assert refresh.await_count == expected
                _, instance, command, payload = dispatch.await_args.args
                assert (instance, command) == (target, name)
                assert "refresh_if_dirty" not in payload and "unity_instance" not in payload
            for invalid in ("false", 0, None):
                dispatch.reset_mock()
                state.reset_mock()
                result = await client.call_tool(
                    name,
                    {**arguments, "unity_instance": target, "refresh_if_dirty": invalid},
                    raise_on_error=False,
                )
                assert result.is_error
                dispatch.assert_not_awaited()
                state.assert_not_awaited()

        refresh.reset_mock()
        result = await client.call_tool(
            "prefab_contents",
            {
                "action": "modify_contents",
                "prefab_path": "Assets/Panel.prefab",
                "position": [1, 2, 3],
                "preview_only": True,
                "refresh_if_dirty": True,
                "unity_instance": target,
            },
        )
        assert result.structured_content["success"], result.content
        refresh.assert_not_awaited()

        state.reset_mock()
        state.side_effect = [
            {
                "success": True,
                "data": {
                    "assets": {"external_changes_dirty": True},
                    "compilation": {"is_compiling": True},
                },
            },
            {"success": True, "data": {"compilation": {"is_compiling": False}}},
        ]
        result = await client.call_tool(
            "scene_inspect",
            {"action": "get_active", "refresh_if_dirty": False, "unity_instance": target},
        )
        assert result.structured_content["success"], result.content
        assert state.await_count == 2
        refresh.assert_not_awaited()
        state.side_effect = None

        allowed.side_effect = lambda command, *_args, **_kwargs: command != "refresh_unity"
        result = await client.call_tool(
            "scene_inspect",
            {"action": "get_active", "refresh_if_dirty": True, "unity_instance": target},
        )
        assert result.structured_content["success"], result.content
        refresh.assert_not_awaited()
