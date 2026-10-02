"""
Tests for the manage_components tool.

This tool handles component lifecycle operations (add, remove, set_property).
"""
import pytest
from unittest.mock import AsyncMock

from .test_helpers import DummyContext
import services.tools.manage_components as manage_comp_mod


@pytest.mark.asyncio
async def test_single_property_explicit_null_is_forwarded(monkeypatch):
    send = AsyncMock(return_value={"success": True, "data": {}})
    monkeypatch.setattr(manage_comp_mod, "send_with_unity_instance", send)
    response = await manage_comp_mod.manage_components(
        DummyContext(), "set_property", "Player", "Renderer",
        property="sharedMaterial", value=None,
    )
    assert response["success"] is True
    assert send.call_args.args[3]["property"] == "sharedMaterial"
    assert "value" in send.call_args.args[3]
    assert send.call_args.args[3]["value"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("arguments", [
    {"property": "sharedMaterial"},
    {"properties": "[object Object]"},
    {"property": "mass", "value": "undefined"},
    {"target": ""},
    {"component_type": ""},
])
async def test_invalid_component_inputs_do_not_preflight_or_dispatch(monkeypatch, arguments):
    preflight = AsyncMock(return_value=None)
    send = AsyncMock(return_value={"success": True})
    monkeypatch.setattr(manage_comp_mod, "preflight", preflight)
    monkeypatch.setattr(manage_comp_mod, "send_with_unity_instance", send)
    params = {"action": "set_property", "target": "Player", "component_type": "Renderer"}
    response = await manage_comp_mod.manage_components(DummyContext(), **{**params, **arguments})
    assert response["success"] is False
    preflight.assert_not_awaited()
    send.assert_not_awaited()


@pytest.mark.asyncio
async def test_partial_component_add_failure_retains_created_component_details(monkeypatch):
    failure = {"success": False, "error": "Property initialization failed", "data": {
        "instanceID": 123, "componentType": "Rigidbody", "componentInstanceID": 456,
        "componentAdded": True,
        "errors": ["mass: invalid value"],
    }}
    monkeypatch.setattr(manage_comp_mod, "send_with_unity_instance", AsyncMock(return_value=failure))
    response = await manage_comp_mod.manage_components(
        DummyContext(), "add", "Player", "Rigidbody", properties={"mass": "invalid"},
    )
    assert response == failure


@pytest.mark.asyncio
async def test_manage_components_add_single(monkeypatch):
    """Test adding a single component."""
    captured = {}

    async def fake_send(cmd, params, **kwargs):
        captured["cmd"] = cmd
        captured["params"] = params
        return {
            "success": True,
            "data": {
                "addedComponents": [{"typeName": "UnityEngine.Rigidbody", "instanceID": 12345}]
            },
        }

    monkeypatch.setattr(
        manage_comp_mod,
        "async_send_command_with_retry",
        fake_send,
    )

    resp = await manage_comp_mod.manage_components(
        ctx=DummyContext(),
        action="add",
        target="Player",
        component_type="Rigidbody",
    )

    assert resp.get("success") is True
    assert captured["cmd"] == "manage_components"
    assert captured["params"]["action"] == "add"
    assert captured["params"]["target"] == "Player"
    assert captured["params"]["componentType"] == "Rigidbody"


@pytest.mark.asyncio
async def test_manage_components_remove(monkeypatch):
    """Test removing a component."""
    captured = {}

    async def fake_send(cmd, params, **kwargs):
        captured["params"] = params
        return {"success": True, "data": {"instanceID": 12345, "name": "Player"}}

    monkeypatch.setattr(
        manage_comp_mod,
        "async_send_command_with_retry",
        fake_send,
    )

    resp = await manage_comp_mod.manage_components(
        ctx=DummyContext(),
        action="remove",
        target="Player",
        component_type="Rigidbody",
    )

    assert resp.get("success") is True
    assert captured["params"]["action"] == "remove"
    assert captured["params"]["componentType"] == "Rigidbody"


@pytest.mark.asyncio
async def test_manage_components_set_property_single(monkeypatch):
    """Test setting a single component property."""
    captured = {}

    async def fake_send(cmd, params, **kwargs):
        captured["params"] = params
        return {"success": True, "data": {"instanceID": 12345}}

    monkeypatch.setattr(
        manage_comp_mod,
        "async_send_command_with_retry",
        fake_send,
    )

    resp = await manage_comp_mod.manage_components(
        ctx=DummyContext(),
        action="set_property",
        target="Player",
        component_type="Rigidbody",
        property="mass",
        value=5.0,
    )

    assert resp.get("success") is True
    assert captured["params"]["action"] == "set_property"
    assert captured["params"]["property"] == "mass"
    assert captured["params"]["value"] == 5.0


@pytest.mark.asyncio
async def test_manage_components_set_property_multiple(monkeypatch):
    """Test setting multiple component properties via properties dict."""
    captured = {}

    async def fake_send(cmd, params, **kwargs):
        captured["params"] = params
        return {"success": True, "data": {"instanceID": 12345}}

    monkeypatch.setattr(
        manage_comp_mod,
        "async_send_command_with_retry",
        fake_send,
    )

    resp = await manage_comp_mod.manage_components(
        ctx=DummyContext(),
        action="set_property",
        target="Player",
        component_type="Rigidbody",
        properties={"mass": 5.0, "drag": 0.5},
    )

    assert resp.get("success") is True
    assert captured["params"]["action"] == "set_property"
    assert captured["params"]["properties"] == {"mass": 5.0, "drag": 0.5}


@pytest.mark.asyncio
async def test_manage_components_set_property_json_string(monkeypatch):
    """Test setting component properties with JSON string input."""
    captured = {}

    async def fake_send(cmd, params, **kwargs):
        captured["params"] = params
        return {"success": True, "data": {"instanceID": 12345}}

    monkeypatch.setattr(
        manage_comp_mod,
        "async_send_command_with_retry",
        fake_send,
    )

    resp = await manage_comp_mod.manage_components(
        ctx=DummyContext(),
        action="set_property",
        target="Player",
        component_type="Rigidbody",
        properties='{"mass": 10.0}',  # JSON string
    )

    assert resp.get("success") is True
    assert captured["params"]["properties"] == {"mass": 10.0}


@pytest.mark.asyncio
async def test_manage_components_add_with_properties(monkeypatch):
    """Test adding a component with initial properties."""
    captured = {}

    async def fake_send(cmd, params, **kwargs):
        captured["params"] = params
        return {
            "success": True,
            "data": {"addedComponents": [{"typeName": "Rigidbody", "instanceID": 123}]},
        }

    monkeypatch.setattr(
        manage_comp_mod,
        "async_send_command_with_retry",
        fake_send,
    )

    resp = await manage_comp_mod.manage_components(
        ctx=DummyContext(),
        action="add",
        target="Player",
        component_type="Rigidbody",
        properties={"mass": 2.0, "useGravity": False},
    )

    assert resp.get("success") is True
    assert captured["params"]["properties"] == {"mass": 2.0, "useGravity": False}


@pytest.mark.asyncio
async def test_manage_components_search_method_passthrough(monkeypatch):
    """Test that search_method is correctly passed through."""
    captured = {}

    async def fake_send(cmd, params, **kwargs):
        captured["params"] = params
        return {"success": True, "data": {}}

    monkeypatch.setattr(
        manage_comp_mod,
        "async_send_command_with_retry",
        fake_send,
    )

    resp = await manage_comp_mod.manage_components(
        ctx=DummyContext(),
        action="add",
        target="Canvas/Panel",
        component_type="Image",
        search_method="by_path",
    )

    assert resp.get("success") is True
    assert captured["params"]["searchMethod"] == "by_path"


@pytest.mark.asyncio
async def test_manage_components_target_by_id(monkeypatch):
    """Test targeting by instance ID."""
    captured = {}

    async def fake_send(cmd, params, **kwargs):
        captured["params"] = params
        return {"success": True, "data": {}}

    monkeypatch.setattr(
        manage_comp_mod,
        "async_send_command_with_retry",
        fake_send,
    )

    resp = await manage_comp_mod.manage_components(
        ctx=DummyContext(),
        action="add",
        target=12345,  # Integer instance ID
        component_type="BoxCollider",
        search_method="by_id",
    )

    assert resp.get("success") is True
    assert captured["params"]["target"] == 12345
    assert captured["params"]["searchMethod"] == "by_id"

