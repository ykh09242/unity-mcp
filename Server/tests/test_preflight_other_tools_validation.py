"""Validate uGUI requests before the production Editor readiness guard."""

from dataclasses import dataclass
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastmcp import Client, FastMCP
from pydantic import JsonValue

from services.tools.manage_ugui import manage_ugui


@dataclass(frozen=True, slots=True)
class Boundary:
    state: AsyncMock
    refresh: AsyncMock
    read: AsyncMock
    mutate: AsyncMock


@pytest.fixture
def boundary(monkeypatch: pytest.MonkeyPatch) -> Boundary:
    fixture = Boundary(
        state=AsyncMock(return_value={"success": True, "data": {"tests": {"is_running": True}}}),
        refresh=AsyncMock(return_value={"success": True}),
        read=AsyncMock(return_value={"success": True}),
        mutate=AsyncMock(return_value={"success": True}),
    )
    monkeypatch.setattr("services.tools.preflight._in_pytest", lambda: False)
    monkeypatch.setattr("services.resources.editor_state.get_editor_state_authoritative", fixture.state)
    monkeypatch.setattr("services.tools.refresh_unity.refresh_unity", fixture.refresh)
    monkeypatch.setattr("services.tools.manage_ugui.send_with_unity_instance", fixture.read)
    monkeypatch.setattr("services.tools.manage_ugui.send_mutation", fixture.mutate)
    monkeypatch.setattr("services.tools.manage_ugui.get_unity_instance_from_context", AsyncMock(return_value="UGUI@fixture"))
    return fixture


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "options",
    [
        {"action": "set_text", "properties": {"fontSize": True}},
        {"action": "set_text", "properties": {"fontSize": None}},
        {"action": "set_text", "properties": {"fontSize": 0}},
        {"action": "set_text", "properties": {"raycastTarget": 0}},
        {"action": "set_text", "properties": {"text": False}},
        {"action": "set_canvas", "properties": {"sortingOrder": 1.5}},
        {"action": "set_canvas", "properties": {"sortingOrder": 32768}},
        {"action": "set_canvas", "properties": {"pixelPerfect": "false"}},
        {"action": "set_canvas", "properties": {"referenceResolution": [0, 100]}},
        {"action": "set_canvas", "properties": {"matchWidthOrHeight": 2}},
        {"action": "set_rect", "properties": {"pivot": [True, 0]}},
        {"action": "set_rect", "properties": {"pivot": [2, 0]}},
        {"action": "set_rect", "properties": {"sizeDelta": [1e100, 2]}},
        {"action": "set_rect", "properties": {"anchorMin": [1, 0], "anchorMax": [0, 1]}},
        {"action": "set_rect", "properties": {"offsetMin": [0, 0], "sizeDelta": [0, 0]}},
        {"action": "set_rect", "properties": {"unknown": 1}},
        {"action": "set_layout", "properties": {"spacing": 0}},
        {"action": "set_layout", "properties": {"type": "unsupported"}},
        {"action": "set_layout", "properties": {"type": "grid", "spacing": 0}},
        {"action": "set_layout", "properties": {"type": "grid", "constraintCount": False}},
        {"action": "set_layout", "properties": {"type": "vertical", "padding": {"left": 1, "right": 1}}},
        {"action": "create", "element_type": "text", "parent": "Canvas", "properties": {"fontSize": "24"}},
        {"action": "create", "element_type": "canvas", "properties": {"fontSize": 24}},
    ],
)
async def test_invalid_ugui_properties_do_not_check_editor_readiness(
    boundary: Boundary, options: dict[str, JsonValue],
) -> None:
    # Given: real preflight would report running tests if it reached Editor state.
    # When
    result = await manage_ugui(SimpleNamespace(), target="Canvas", **options)
    # Then: callers receive their input error without any Editor reads or refresh.
    assert result["success"] is False
    assert result.get("error") != "busy"
    boundary.state.assert_not_awaited()
    boundary.refresh.assert_not_awaited()
    boundary.read.assert_not_awaited()
    boundary.mutate.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "properties",
    [
        {"fontSize": True},
        '{"fontSize":true}',
        {"raycastTarget": 0},
        {"fontSizeMin": 30, "fontSizeMax": 20},
    ],
)
async def test_nested_ugui_errors_are_rejected_through_the_sdk_before_preflight(
    boundary: Boundary, properties: JsonValue,
) -> None:
    # Given: the MCP schema accepts a properties object or embedded JSON string.
    server = FastMCP("ugui-preflight-validation")
    server.tool()(manage_ugui)
    # When
    async with Client(server) as client:
        result = await client.call_tool("manage_ugui", {
            "action": "set_text", "target": "Canvas", "properties": properties,
        })
    # Then: nested values reach local semantic validation without Editor reads.
    assert result.structured_content["success"] is False
    assert result.structured_content.get("error") != "busy"
    boundary.state.assert_not_awaited()
    boundary.refresh.assert_not_awaited()
    boundary.read.assert_not_awaited()
    boundary.mutate.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "action,properties",
    [
        ("set_text", {"text": "", "raycastTarget": False, "fontSize": 24.5}),
        ("set_canvas", {"sortingOrder": 0, "pixelPerfect": False, "worldCamera": None}),
        ("set_rect", {"pivot": {"x": 0, "y": 0}, "sizeDelta": [0, 0]}),
        ("set_layout", {"type": "vertical", "spacing": 0, "childControlWidth": False}),
        ("set_layout", {"type": "grid", "spacing": [0, 0], "constraintCount": 1}),
    ],
)
async def test_valid_ugui_properties_keep_false_zero_and_nullable_reference(
    boundary: Boundary, action: str, properties: dict[str, JsonValue],
) -> None:
    # Given
    boundary.state.return_value = {"success": True, "data": {"tests": {"is_running": False}}}
    # When
    result = await manage_ugui(SimpleNamespace(), action=action, target=0, properties=properties)
    # Then
    assert result["success"] is True
    boundary.state.assert_awaited_once()
    assert boundary.mutate.await_args.args[3]["properties"] == properties
    assert boundary.mutate.await_args.args[3]["target"] == 0
    boundary.refresh.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("selector", ["target", "parent"])
@pytest.mark.parametrize("value", [-(2**31) - 1, 2**31])
async def test_out_of_range_ugui_selectors_do_not_check_editor_readiness(
    boundary: Boundary, selector: str, value: int,
) -> None:
    # Given: native instance IDs have Int32 bounds, but the MCP integer has none.
    options = {"target": "Canvas", selector: value}
    # When
    result = await manage_ugui(
        SimpleNamespace(), action="set_text", properties={"text": "valid"}, **options,
    )
    # Then
    assert result["success"] is False
    assert result.get("error") != "busy"
    boundary.state.assert_not_awaited()
    boundary.refresh.assert_not_awaited()
    boundary.read.assert_not_awaited()
    boundary.mutate.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "element_type,properties",
    [
        ("text", {"fontSizeMin": 80}),
        ("text", {"fontSizeMax": 7}),
        ("image", {"anchorMin": [0.75, 0.75]}),
        ("image", {"anchorMax": [0.25, 0.25]}),
        ("panel", {"anchorMin": [1.25, 1.25]}),
        ("panel", {"anchorMax": [-0.25, -0.25]}),
    ],
)
async def test_invalid_ugui_create_properties_are_checked_against_known_defaults(
    boundary: Boundary, element_type: str, properties: dict[str, JsonValue],
) -> None:
    result = await manage_ugui(
        SimpleNamespace(), action="create", parent="Canvas", element_type=element_type,
        properties=properties,
    )
    assert result["success"] is False
    assert result.get("error") != "busy"
    boundary.state.assert_not_awaited()
    boundary.refresh.assert_not_awaited()
    boundary.read.assert_not_awaited()
    boundary.mutate.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "element_type,properties",
    [
        ("text", {"fontSizeMin": 72}),
        ("text", {"fontSizeMax": 8}),
        ("image", {"anchorMin": [0.5, 0.5]}),
        ("image", {"anchorMax": [0.5, 0.5]}),
        ("panel", {"anchorMin": [1, 1]}),
        ("panel", {"anchorMax": [0, 0]}),
    ],
)
async def test_valid_ugui_create_properties_preserve_default_boundaries(
    boundary: Boundary, element_type: str, properties: dict[str, JsonValue],
) -> None:
    boundary.state.return_value = {"success": True, "data": {"tests": {"is_running": False}}}
    result = await manage_ugui(
        SimpleNamespace(), action="create", parent="Canvas", element_type=element_type,
        properties=properties,
    )
    assert result["success"] is True
    boundary.state.assert_awaited_once()
    assert boundary.mutate.await_args.args[3]["properties"] == properties


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "action,properties",
    [
        ("set_text", {"fontSize": 1e-50}),
        ("set_canvas", {"referenceResolution": [1e-50, 100]}),
        ("set_canvas", {"planeDistance": 1e-50}),
        ("set_text", {"fontSize": 3.402823466385289e38}),
    ],
)
async def test_invalid_ugui_float32_domain_values_precede_editor_readiness(
    boundary: Boundary, action: str, properties: dict[str, JsonValue],
) -> None:
    result = await manage_ugui(SimpleNamespace(), action=action, target="Canvas", properties=properties)
    assert result["success"] is False
    assert result.get("error") != "busy"
    boundary.state.assert_not_awaited()
    boundary.refresh.assert_not_awaited()
    boundary.read.assert_not_awaited()
    boundary.mutate.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "action,properties",
    [
        ("set_text", {"color": [1.00000001, 0, 0, 1]}),
        ("set_rect", {"pivot": [1.00000001, 0]}),
        ("set_canvas", {"matchWidthOrHeight": 1.00000001}),
        ("set_layout", {"type": "layout_element", "minWidth": -1.00000001}),
        ("set_rect", {"anchorMin": [0.50000001, 0.5], "anchorMax": [0.5, 0.5]}),
        ("set_text", {"fontSizeMin": 72.00000001, "fontSizeMax": 72}),
        ("set_text", {"fontSize": 1.401298464324817e-45}),
        ("set_text", {"fontSize": 3.4028234663852886e38}),
    ],
)
async def test_valid_ugui_float32_rounding_preserves_raw_payload(
    boundary: Boundary, action: str, properties: dict[str, JsonValue],
) -> None:
    boundary.state.return_value = {"success": True, "data": {"tests": {"is_running": False}}}
    result = await manage_ugui(SimpleNamespace(), action=action, target="Canvas", properties=properties)
    assert result["success"] is True
    boundary.state.assert_awaited_once()
    assert boundary.mutate.await_args.args[3]["properties"] == properties


@pytest.mark.asyncio
async def test_create_ugui_offset_overflow_precedes_editor_readiness(boundary: Boundary) -> None:
    result = await manage_ugui(
        SimpleNamespace(), action="create", element_type="image", parent="Canvas",
        properties={"offsetMin": [-3e38, 0], "offsetMax": [3e38, 0]},
    )
    assert result["success"] is False
    assert result.get("error") != "busy"
    boundary.state.assert_not_awaited()
    boundary.refresh.assert_not_awaited()
    boundary.read.assert_not_awaited()
    boundary.mutate.assert_not_awaited()


@pytest.mark.asyncio
async def test_create_ugui_finite_large_offsets_are_forwarded(boundary: Boundary) -> None:
    boundary.state.return_value = {"success": True, "data": {"tests": {"is_running": False}}}
    properties = {"offsetMin": [-1.7e38, 0], "offsetMax": [1.7e38, 0]}
    result = await manage_ugui(
        SimpleNamespace(), action="create", element_type="image", parent="Canvas",
        properties=properties,
    )
    assert result["success"] is True
    boundary.state.assert_awaited_once()
    assert boundary.mutate.await_args.args[3]["properties"] == properties
