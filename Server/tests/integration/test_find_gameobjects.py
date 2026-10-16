"""
Tests for the find_gameobjects tool.

This tool provides paginated GameObject search, returning instance IDs only.
"""

import pytest

from .test_helpers import DummyContext
import services.tools.find_gameobjects as find_go_mod


@pytest.mark.asyncio
async def test_find_gameobjects_basic_search(monkeypatch):
    """Test basic search returns instance IDs."""
    captured = {}

    async def fake_send(cmd, params, **kwargs):
        captured["cmd"] = cmd
        captured["params"] = params
        return {
            "success": True,
            "data": {
                "instanceIDs": [12345, 67890],
                "pageSize": 25,
                "cursor": 0,
                "totalCount": 2,
                "hasMore": False,
            },
        }

    monkeypatch.setattr(
        find_go_mod,
        "async_send_command_with_retry",
        fake_send,
    )

    resp = await find_go_mod.find_gameobjects(
        ctx=DummyContext(),
        search_term="Player",
        search_method="by_name",
    )

    assert resp.get("success") is True
    assert captured["cmd"] == "find_gameobjects"
    assert captured["params"]["searchTerm"] == "Player"
    assert captured["params"]["searchMethod"] == "by_name"


@pytest.mark.asyncio
async def test_find_gameobjects_by_component(monkeypatch):
    """Test search by component type."""
    captured = {}

    async def fake_send(cmd, params, **kwargs):
        captured["params"] = params
        return {
            "success": True,
            "data": {
                "instanceIDs": [111, 222, 333],
                "pageSize": 25,
                "cursor": 0,
                "totalCount": 3,
                "hasMore": False,
            },
        }

    monkeypatch.setattr(
        find_go_mod,
        "async_send_command_with_retry",
        fake_send,
    )

    resp = await find_go_mod.find_gameobjects(
        ctx=DummyContext(),
        search_term="Camera",
        search_method="by_component",
    )

    assert resp.get("success") is True
    assert captured["params"]["searchTerm"] == "Camera"
    assert captured["params"]["searchMethod"] == "by_component"


@pytest.mark.asyncio
async def test_find_gameobjects_pagination_params(monkeypatch):
    """Test pagination parameters are passed correctly."""
    captured = {}

    async def fake_send(cmd, params, **kwargs):
        captured["params"] = params
        return {
            "success": True,
            "data": {
                "instanceIDs": [444, 555],
                "pageSize": 10,
                "cursor": 20,
                "totalCount": 50,
                "hasMore": True,
                "nextCursor": "30",
            },
        }

    monkeypatch.setattr(
        find_go_mod,
        "async_send_command_with_retry",
        fake_send,
    )

    resp = await find_go_mod.find_gameobjects(
        ctx=DummyContext(),
        search_term="Enemy",
        search_method="by_tag",
        page_size="10",
        cursor="20",
    )

    assert resp.get("success") is True
    p = captured["params"]
    assert p["pageSize"] == 10
    assert p["cursor"] == 20


@pytest.mark.asyncio
async def test_find_gameobjects_boolean_coercion(monkeypatch):
    """Test boolean string coercion for include_inactive."""
    captured = {}

    async def fake_send(cmd, params, **kwargs):
        captured["params"] = params
        return {"success": True, "data": {"instanceIDs": []}}

    monkeypatch.setattr(
        find_go_mod,
        "async_send_command_with_retry",
        fake_send,
    )

    resp = await find_go_mod.find_gameobjects(
        ctx=DummyContext(),
        search_term="HiddenObject",
        search_method="by_name",
        include_inactive="true",
    )

    assert resp.get("success") is True
    p = captured["params"]
    assert p["includeInactive"] is True


@pytest.mark.asyncio
async def test_find_gameobjects_by_layer(monkeypatch):
    """Test search by layer."""
    captured = {}

    async def fake_send(cmd, params, **kwargs):
        captured["params"] = params
        return {"success": True, "data": {"instanceIDs": [999]}}

    monkeypatch.setattr(
        find_go_mod,
        "async_send_command_with_retry",
        fake_send,
    )

    resp = await find_go_mod.find_gameobjects(
        ctx=DummyContext(),
        search_term="UI",
        search_method="by_layer",
    )

    assert resp.get("success") is True
    assert captured["params"]["searchMethod"] == "by_layer"
    assert captured["params"]["searchTerm"] == "UI"


@pytest.mark.asyncio
async def test_find_gameobjects_by_path(monkeypatch):
    """Test search by hierarchy path."""
    captured = {}

    async def fake_send(cmd, params, **kwargs):
        captured["params"] = params
        return {"success": True, "data": {"instanceIDs": [777]}}

    monkeypatch.setattr(
        find_go_mod,
        "async_send_command_with_retry",
        fake_send,
    )

    resp = await find_go_mod.find_gameobjects(
        ctx=DummyContext(),
        search_term="Canvas/Panel/Button",
        search_method="by_path",
    )

    assert resp.get("success") is True
    assert captured["params"]["searchMethod"] == "by_path"
    assert captured["params"]["searchTerm"] == "Canvas/Panel/Button"


@pytest.mark.asyncio
async def test_scoped_search_forwards_opaque_cursor_and_keeps_native_details(monkeypatch):
    from unittest.mock import ANY, AsyncMock

    captured = {}
    reply = {"success": False, "code": "stale_cursor", "data": {"restart": True, "extra": 17}}

    async def send(instance, command, params, **kwargs):
        captured.update(instance=instance, command=command, params=params)
        return reply

    gate = AsyncMock(return_value=None)
    monkeypatch.setattr(
        find_go_mod, "get_unity_instance_from_context", AsyncMock(return_value="Owned@fixture")
    )
    monkeypatch.setattr(find_go_mod, "preflight", gate)
    monkeypatch.setattr(
        find_go_mod,
        "send_with_unity_instance",
        lambda fn, instance, command, params: send(instance, command, params),
    )
    conditions = [{"search_method": "by_component", "search_term": "BoxCollider"}]
    result = await find_go_mod.find_gameobjects(
        DummyContext(),
        scene_scope="all_loaded",
        conditions=conditions,
        cursor="a" * 32,
        max_depth=0,
        page_size=17,
    )
    assert result == reply
    assert captured == {
        "instance": "Owned@fixture",
        "command": "find_gameobjects",
        "params": {
            "searchMethod": "by_name",
            "includeInactive": False,
            "pageSize": 17,
            "cursor": "a" * 32,
            "scene_scope": "all_loaded",
            "conditions": conditions,
            "max_depth": 0,
        },
    }
    gate.assert_awaited_once_with(ANY, wait_for_no_compile=True, refresh_if_dirty=True)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "options",
    [
        {"page_size": "2"},
        {"page_size": True},
        {"page_size": 1.5},
        {"page_size": 0},
        {"page_size": 501},
        {"max_depth": True},
        {"max_depth": -1},
        {"max_depth": 129},
        {"cursor": 0},
        {"cursor": "g" * 32},
        {"search_term": "x" * 513},
        {"search_term": "\U0001f600" * 257},
        {"conditions": [{"search_method": "by_name", "search_term": "x", "extra": True}]},
        {"conditions": [{"search_method": "by_name", "search_term": "x"}] * 8},
        {"conditions": [{"search_method": "invalid", "search_term": "x"}]},
        {"scene_scope": "legacy", "max_depth": 0},
        {"scene_scope": "legacy", "conditions": []},
    ],
)
async def test_scoped_invalid_options_rejected_before_preflight(monkeypatch, options):
    from unittest.mock import ANY, AsyncMock

    gate = AsyncMock(side_effect=AssertionError("preflight must not run"))
    instance = AsyncMock(side_effect=AssertionError("instance lookup must not run"))
    monkeypatch.setattr(find_go_mod, "preflight", gate)
    monkeypatch.setattr(find_go_mod, "get_unity_instance_from_context", instance)
    response = await find_go_mod.find_gameobjects(
        DummyContext(), **{"scene_scope": "active", "search_term": "x", **options}
    )
    assert response["success"] is False
    gate.assert_not_awaited()
    instance.assert_not_awaited()


@pytest.mark.asyncio
async def test_scoped_preflight_failure_preserves_gate_without_dispatch(monkeypatch):
    from unittest.mock import ANY, AsyncMock, Mock

    reply = {"success": False, "error": "reload_pending", "data": {"retry": True}}
    gate = Mock()
    gate.model_dump.return_value = reply
    monkeypatch.setattr(
        find_go_mod, "get_unity_instance_from_context", AsyncMock(return_value="Owned@fixture")
    )
    monkeypatch.setattr(find_go_mod, "preflight", AsyncMock(return_value=gate))
    dispatch = AsyncMock(side_effect=AssertionError("no dispatch after gate"))
    monkeypatch.setattr(find_go_mod, "send_with_unity_instance", dispatch)
    assert (
        await find_go_mod.find_gameobjects(
            DummyContext(), "x", scene_scope="active", cursor="b" * 32
        )
        == reply
    )
    dispatch.assert_not_awaited()
