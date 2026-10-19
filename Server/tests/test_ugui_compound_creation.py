"""Closed compound requests, real policy admission and SDK schema publication."""

import copy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastmcp import Client, FastMCP

from services.operation_permissions import (
    OperationPermissionError,
    authorize_operation,
    parse_permissions,
)
from services.registry import get_registered_tools
from services.tools.manage_ugui import manage_ugui


def request(kind="slider", **properties):
    return {
        "action": "create_control",
        "element_type": kind,
        "parent": 123,
        "name": "OwnedControl",
        "properties": properties,
    }


@pytest.fixture
def boundary(monkeypatch):
    calls = {
        "preflight": AsyncMock(return_value=None),
        "get_unity_instance_from_context": AsyncMock(return_value="ui16@owned"),
        "send_mutation": AsyncMock(return_value={"success": True, "data": {"marker": "native"}}),
        "send_with_unity_instance": AsyncMock(
            side_effect=AssertionError("compound used read transport")
        ),
    }
    for name, call in calls.items():
        monkeypatch.setattr(f"services.tools.manage_ugui.{name}", call)
    return calls


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["scroll_view", "toggle", "slider", "tmp_input"])
async def test_valid_closed_control_reaches_one_mutation(boundary, kind):
    result = await manage_ugui(SimpleNamespace(), **request(kind))
    assert result["success"] is True
    boundary["preflight"].assert_awaited_once()
    boundary["get_unity_instance_from_context"].assert_awaited_once()
    boundary["send_mutation"].assert_awaited_once()
    call = boundary["send_mutation"].call_args
    params = call.args[3] if len(call.args) > 3 else call.kwargs["params"]
    assert set(params) == {
        "action",
        "element_type",
        "parent",
        "name",
        "properties",
        "include_inactive",
    }
    assert params["properties"]["eventSystem"] == "preserve"
    assert params["properties"]["sizeDelta"][0] > 0
    boundary["send_with_unity_instance"].assert_not_awaited()


INVALID = [
    request(parent=True),
    {**request(), "parent": 0},
    {**request(), "parent": True},
    {**request(), "parent": 2**31},
    {**request(), "name": "x" * 257},
    request(sizeDelta=[0, 24]),
    request(sizeDelta=[8193, 24]),
    request(sizeDelta=[True, 24]),
    request(anchoredPosition=[1e309, 0]),
    request(anchoredPosition=[1e100, 0]),
    request(minValue=1, maxValue=1),
    request(minValue=0, maxValue=1e-50),
    request(minValue=-3e38, maxValue=3e38),
    request(minValue=0, maxValue=4, wholeNumbers=True, value=1.5),
    request(minValue=0, maxValue=1, value=2),
    request("toggle", value=True),
    request("toggle", isOn=1),
    request("scroll_view", spacing=129),
    request("slider", direction="Diagonal"),
    request("tmp_input", characterLimit=True),
    request("tmp_input", characterLimit=2, text="Long"),
    request("tmp_input", text="x" * 1025),
    request("tmp_input", text="\ud800"),
    request("tmp_input", richText=True),
    request("tmp_input", emojiFallbackSupport=True),
    request("toggle", eventSystem="create_input_system"),
    {**request(), "properties": "null"},
    {**request(), "properties": '{"value":1e309}'},
    {**request(), "target": 123},
    {**request(), "camera": "Camera"},
    {**request(), "max_nodes": 1},
    {**request(), "include_inactive": 1},
    {**request(), "properties": {"extra": "x" * 17000}},
]


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", INVALID, ids=[f"closed-{n}" for n in range(len(INVALID))])
async def test_invalid_control_is_zero_preflight_and_zero_transport(boundary, payload):
    result = await manage_ugui(SimpleNamespace(), **copy.deepcopy(payload))
    assert result == {"success": False, "message": "invalid_compound_control_request"}
    for call in boundary.values():
        call.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["scroll_view", "toggle", "slider", "tmp_input"])
async def test_old_generic_create_does_not_gain_compound_kinds(boundary, kind):
    result = await manage_ugui(SimpleNamespace(), **{**request(kind), "action": "create"})
    assert result["success"] is False
    boundary["send_mutation"].assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kind,key", [("toggle", "label"), ("tmp_input", "text"), ("tmp_input", "placeholder")]
)
async def test_markup_is_literal_transport_data(boundary, kind, key):
    literal = '<font="Foreign"><sprite=0>★</font>'
    await manage_ugui(SimpleNamespace(), **request(kind, **{key: literal}))
    call = boundary["send_mutation"].call_args
    params = call.args[3] if len(call.args) > 3 else call.kwargs["params"]
    assert params["properties"][key] == literal
    assert "richText" not in params["properties"]


@pytest.mark.parametrize("native", [False, True])
@pytest.mark.parametrize(
    "grant,allowed", [("read", False), ("test", False), ("edit", True), ("run", False)]
)
def test_real_operation_policy_requires_edit(grant, allowed, native):
    authority = parse_permissions({"unity_mcp_permissions": [grant]}, strict=True)
    if allowed:
        authorize_operation(authority, "manage_ugui", request(), native=native)
    else:
        with pytest.raises(OperationPermissionError):
            authorize_operation(authority, "manage_ugui", request(), native=native)


@pytest.mark.asyncio
async def test_existing_generic_image_create_retains_its_transport(boundary):
    result = await manage_ugui(
        SimpleNamespace(),
        action="create",
        element_type="image",
        parent=123,
        name="Basic",
        properties={},
    )
    assert result["success"] is True
    boundary["send_mutation"].assert_awaited_once()
    assert boundary["send_mutation"].call_args.args[3]["action"] == "create"


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["2026-07-28", "legacy"])
async def test_real_sdk_publishes_and_dispatches_compound_kind(boundary, mode):
    entry = next(item for item in get_registered_tools() if item["name"] == "manage_ugui")
    server = FastMCP("owned-compound-sdk")
    server.tool(name=entry["name"], **entry["kwargs"])(manage_ugui)
    async with Client(server, mode=mode) as client:
        result = await client.call_tool("manage_ugui", request("toggle", label="Owned"))
        assert result.is_error is False
    boundary["send_mutation"].assert_awaited_once()
