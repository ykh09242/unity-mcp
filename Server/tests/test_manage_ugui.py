"""uGUI request validation and routing, without a running Unity Editor."""

from dataclasses import dataclass
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastmcp import Client, FastMCP
from fastmcp.exceptions import ToolError
from pydantic import JsonValue
from starlette.websockets import WebSocket

from core.config import config
from models import MCPResponse
from services.registry import get_group_tool_names, get_registered_tools
from services.tools.manage_ugui import manage_ugui
from services.tools.refresh_unity import send_mutation
from transport.unity_transport import send_with_unity_instance


@dataclass(frozen=True, slots=True)
class Boundary:
    read: AsyncMock
    mutate: AsyncMock
    preflight: AsyncMock
    instance: AsyncMock


@pytest.fixture
def boundary(monkeypatch: pytest.MonkeyPatch) -> Boundary:
    fixture = Boundary(
        read=AsyncMock(return_value={"success": True}),
        mutate=AsyncMock(return_value={"success": True}),
        preflight=AsyncMock(return_value=None),
        instance=AsyncMock(return_value="UGUI@fixture"),
    )
    for name, replacement in (
        ("send_with_unity_instance", fixture.read),
        ("send_mutation", fixture.mutate),
        ("preflight", fixture.preflight),
        ("get_unity_instance_from_context", fixture.instance),
    ):
        monkeypatch.setattr(f"services.tools.manage_ugui.{name}", replacement)
    return fixture


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "action,options,mutation",
    [
        ("ping", {}, False),
        ("get_hierarchy", {"target": "Canvas"}, False),
        ("diagnose", {"target": "Canvas"}, False),
        ("create", {"parent": 123, "element_type": "panel", "name": "Menu"}, True),
        ("set_rect", {"target": -321, "properties": {"sizeDelta": [100, 200]}}, True),
        (
            "set_layout",
            {"target": "Canvas/Menu", "properties": {"type": "vertical", "spacing": 4}},
            True,
        ),
        (
            "set_text",
            {"target": "Label", "properties": {"text": "Hello", "fontSize": 24}},
            True,
        ),
        ("set_canvas", {"target": "Canvas", "properties": {"sortingOrder": 2}}, True),
    ],
)
async def test_routes_actions_when_valid(
    boundary: Boundary,
    action: str,
    options: dict[str, JsonValue],
    mutation: bool,
) -> None:
    # Given: separate transport seams expose accidental mutation of a read action.
    sender = boundary.mutate if mutation else boundary.read
    # When
    result = await manage_ugui(SimpleNamespace(), action=action, **options)
    # Then
    assert result["success"] is True
    args = sender.await_args.args
    assert args[1:3] == ("UGUI@fixture", "manage_ugui")
    assert args[3] == {
        "action": action,
        "include_inactive": False,
        "max_nodes": 200,
        **options,
    }
    assert (boundary.read.await_count, boundary.mutate.await_count) == (
        (0, 1) if mutation else (1, 0)
    )
    if action == "ping":
        boundary.preflight.assert_not_awaited()
    else:
        boundary.preflight.assert_awaited_once_with(
            SimpleNamespace(),
            requires_no_tests=mutation,
            wait_for_no_compile=True,
        )


@pytest.mark.asyncio
async def test_normalizes_json_when_client_supplies_strings(boundary: Boundary) -> None:
    # Given
    properties = '{"anchorMin":[0,0],"anchorMax":[1,1]}'
    resolutions = '[{"width":64,"height":8192},{"width":8192,"height":64}]'
    # When
    await manage_ugui(
        SimpleNamespace(),
        action=" DIAGNOSE ",
        target="Canvas",
        properties=properties,
        resolutions=resolutions,
        max_nodes=1000,
        include_inactive=True,
    )
    # Then
    assert boundary.read.await_args.args[3] == {
        "action": "diagnose",
        "target": "Canvas",
        "properties": {"anchorMin": [0, 0], "anchorMax": [1, 1]},
        "resolutions": [{"width": 64, "height": 8192}, {"width": 8192, "height": 64}],
        "max_nodes": 1000,
        "include_inactive": True,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "options",
    [
        {"action": "delete"},
        {"action": ""},
        {"action": "create"},
        {"action": "create", "element_type": "slider"},
        {"action": "create", "element_type": "panel"},
        *[
            {"action": action, "properties": {}}
            for action in ("set_rect", "set_layout", "set_text", "set_canvas")
        ],
        {"action": "set_rect", "target": "Canvas"},
        {"action": "diagnose", "target": None},
        {"target": None},
        {"action": "set_rect", "target": "Canvas", "properties": {}},
        {"target": True},
        {"target": " "},
        {"parent": False},
        {"parent": ""},
        {"name": " "},
        {"name": "Menu/Title"},
        {"include_inactive": "false"},
        *[{"max_nodes": value} for value in (0, 1001, True, 2.5, "200")],
        *[
            {"properties": value}
            for value in ('{"bad":', "[]", "null", "[object Object]", "", 42)
        ],
        *[
            {"resolutions": value}
            for value in (
                "not-json",
                "{}",
                [],
                [{"width": 100, "height": 100}] * 9,
                [{"width": 63, "height": 100}],
                [{"width": 100, "height": 8193}],
                [{"width": True, "height": 100}],
                [{"width": 100.5, "height": 100}],
                [{"width": "100", "height": 100}],
                [{"width": 100}],
                [{"width": 100, "height": 100, "scale": 2}],
                42,
            )
        ],
    ],
)
async def test_rejects_payload_when_invalid_before_editor_access(
    boundary: Boundary,
    options: dict[str, JsonValue],
) -> None:
    # Given
    request = {"action": "get_hierarchy", "target": "Canvas", **options}
    # When
    result = await manage_ugui(SimpleNamespace(), **request)
    # Then
    assert result["success"] is False
    assert result["message"]
    for seam in (boundary.read, boundary.mutate, boundary.preflight, boundary.instance):
        seam.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response",
    [
        {
            "success": False,
            "message": "uGUI package unavailable",
            "data": {"requiredPackage": "com.unity.ugui"},
        },
        {
            "success": False,
            "message": "TMP unavailable",
            "data": {"requiredType": "TMPro.TextMeshProUGUI"},
        },
        {"success": True, "data": {"capabilities": {"ugui": False, "tmp": False}}},
    ],
)
async def test_preserves_dependency_report_when_editor_returns_it(
    boundary: Boundary,
    response: dict[str, JsonValue],
) -> None:
    # Given
    boundary.read.return_value = response
    # When
    result = await manage_ugui(SimpleNamespace(), action="ping")
    # Then
    assert result == response


@pytest.mark.asyncio
async def test_returns_busy_when_preflight_blocks_mutation(boundary: Boundary) -> None:
    # Given
    blocked = MCPResponse(
        success=False,
        error="busy",
        hint="retry",
        data={"reason": "tests_running"},
    )
    boundary.preflight.return_value = blocked
    # When
    result = await manage_ugui(SimpleNamespace(), action="create", element_type="canvas")
    # Then
    assert result == blocked.model_dump()
    boundary.mutate.assert_not_awaited()
    boundary.instance.assert_not_awaited()


@pytest.mark.asyncio
async def test_wraps_response_when_transport_returns_non_object(boundary: Boundary) -> None:
    # Given
    boundary.read.return_value = "invalid transport response"
    # When
    result = await manage_ugui(SimpleNamespace(), action="ping")
    # Then
    assert result == {"success": False, "message": "invalid transport response"}


@pytest.mark.asyncio
async def test_exposes_schema_and_ui_group_when_registered(boundary: Boundary) -> None:
    # Given: production decorator metadata is used by the real MCP SDK.
    metadata = next(item for item in get_registered_tools() if item["name"] == "manage_ugui")
    server = FastMCP("ugui-schema")
    server.tool(
        name=metadata["name"],
        description=metadata["description"],
        **metadata["kwargs"],
    )(manage_ugui)
    # When
    async with Client(server) as client:
        tools = await client.list_tools()
        result = await client.call_tool(
            "manage_ugui",
            {
                "action": "diagnose",
                "target": "Canvas",
                "resolutions": '[{"width":64,"height":8192}]',
            },
        )
    # Then
    tool = next(item for item in tools if item.name == "manage_ugui")
    assert tool.annotations.read_only_hint is False
    assert metadata["kwargs"]["tags"] == {"group:ui"}
    assert "manage_ugui" in get_group_tool_names()["ui"]
    assert metadata["unity_target"] == "manage_ugui"
    assert tool.input_schema["properties"]["max_nodes"]["minimum"] == 1
    assert tool.input_schema["properties"]["max_nodes"]["maximum"] == 1000
    assert result.structured_content["success"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "options",
    [
        *[{"max_nodes": value} for value in (True, 1.5, "200", 0, 1001)],
        {"target": True},
        {"parent": False},
        {"include_inactive": "false"},
        {"resolutions": [{"width": True, "height": 100}]},
        {"resolutions": [{"width": 100.0, "height": 100}]},
        {"resolutions": [{"width": "100", "height": 100}]},
    ],
)
async def test_rejects_coercion_when_called_through_sdk(
    boundary: Boundary,
    options: dict[str, JsonValue],
) -> None:
    # Given
    server = FastMCP("ugui-validation")
    server.tool()(manage_ugui)
    # When / Then
    async with Client(server) as client:
        with pytest.raises(ToolError):
            await client.call_tool("manage_ugui", {"action": "get_hierarchy", **options})
    boundary.read.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("number", ["NaN", "Infinity", "-Infinity", "1e400"])
async def test_rejects_nonfinite_json_before_http_transport(
    boundary: Boundary,
    monkeypatch: pytest.MonkeyPatch,
    number: str,
) -> None:
    # Given: the HTTP encoder emits nonstandard JSON for non-finite numbers.
    websocket = WebSocket(
        {"type": "websocket"},
        receive=AsyncMock(return_value={"type": "websocket.connect"}),
        send=AsyncMock(),
    )
    await websocket.accept()

    async def encode_request(instance, command, params, **kwargs):
        await websocket.send_json({"params": params})
        return {"success": True}

    http_send = AsyncMock(side_effect=encode_request)
    monkeypatch.setattr(config, "transport_mode", "http")
    monkeypatch.setattr(config, "http_remote_hosted", False)
    monkeypatch.setattr(
        "services.tools.manage_ugui.send_with_unity_instance",
        send_with_unity_instance,
    )
    monkeypatch.setattr("services.tools.manage_ugui.send_mutation", send_mutation)
    monkeypatch.setattr(
        "transport.plugin_hub.PluginHub.send_command_for_instance", http_send
    )
    server = FastMCP("ugui-finite-json")
    server.tool()(manage_ugui)
    # When: embedded JSON accepts nonstandard constants and numeric overflow.
    async with Client(server) as client:
        response = await client.call_tool(
            "manage_ugui",
            {
                "action": "set_rect",
                "target": "Canvas",
                "properties": '{"anchorMin":[' + number + ',0]}',
            },
        )
    result = response.structured_content
    # Then: return an input error before readiness or transport, without a retry hint.
    assert result["success"] is False
    assert result.get("hint") != "retry"
    assert "JSON" in result["message"]
    assert "finite" in result["message"]
    for seam in (http_send, boundary.preflight, boundary.instance):
        seam.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("number", [float("nan"), float("inf"), -float("inf")])
async def test_rejects_nested_nonfinite_dictionary_before_editor_access(
    boundary: Boundary,
    number: float,
) -> None:
    # Given: direct dictionary values use the same JSON-value validation.
    properties = {"anchoredPosition": {"x": number, "y": 0}}
    # When
    result = await manage_ugui(
        SimpleNamespace(),
        action="set_rect",
        target="Canvas",
        properties=properties,
    )
    # Then
    assert result["success"] is False
    assert "finite" in result["message"]
    for seam in (boundary.mutate, boundary.preflight, boundary.instance):
        seam.assert_not_awaited()


@pytest.mark.asyncio
async def test_preserves_finite_numbers_and_nonfinite_words_in_text(
    boundary: Boundary,
) -> None:
    # Given: the same words are valid text, and ordinary finite numbers stay numeric.
    properties = '{"text":"NaN Infinity -Infinity 1e400","fontSize":24.5}'
    server = FastMCP("ugui-finite-text")
    server.tool()(manage_ugui)
    # When
    async with Client(server) as client:
        response = await client.call_tool(
            "manage_ugui",
            {"action": "set_text", "target": "Label", "properties": properties},
        )
    # Then
    assert response.structured_content["success"] is True
    assert boundary.mutate.await_args.args[3]["properties"] == {
        "text": "NaN Infinity -Infinity 1e400",
        "fontSize": 24.5,
    }


@pytest.mark.asyncio
async def test_preserves_finite_extremes_in_json_properties(boundary: Boundary) -> None:
    # Given: representable finite values need no additional magnitude restrictions.
    properties = '{"anchoredPosition":[-3.4e38,3.4e38],"sizeDelta":[1e-38,0]}'
    server = FastMCP("ugui-finite-extremes")
    server.tool()(manage_ugui)
    # When
    async with Client(server) as client:
        response = await client.call_tool(
            "manage_ugui",
            {"action": "set_rect", "target": "Canvas", "properties": properties},
        )
    # Then
    assert response.structured_content["success"] is True
    assert boundary.mutate.await_args.args[3]["properties"] == {
        "anchoredPosition": [-3.4e38, 3.4e38],
        "sizeDelta": [1e-38, 0],
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "properties",
    [
        '{"text":"\\ud800"}',
        '{"text":"\\udc00"}',
        '{"\\ud800":0}',
        '{"nested":[{"\\udc00":0}]}',
    ],
)
async def test_rejects_unpaired_surrogates_before_http_encoding(
    boundary: Boundary,
    monkeypatch: pytest.MonkeyPatch,
    properties: str,
) -> None:
    # Given: WebSocket text must be encodable as UTF-8 at the ASGI transport sink.
    async def send_as_utf8(message):
        if "text" in message:
            message["text"].encode("utf-8")

    websocket = WebSocket(
        {"type": "websocket"},
        receive=AsyncMock(return_value={"type": "websocket.connect"}),
        send=send_as_utf8,
    )
    await websocket.accept()

    async def encode_request(instance, command, params, **kwargs):
        await websocket.send_json({"params": params})
        return {"success": True}

    http_send = AsyncMock(side_effect=encode_request)
    monkeypatch.setattr(config, "transport_mode", "http")
    monkeypatch.setattr(config, "http_remote_hosted", False)
    monkeypatch.setattr("services.tools.manage_ugui.send_mutation", send_mutation)
    monkeypatch.setattr(
        "transport.plugin_hub.PluginHub.send_command_for_instance", http_send
    )
    server = FastMCP("ugui-unicode-json")
    server.tool()(manage_ugui)
    # When: escaped input passes the MCP envelope, then becomes a surrogate on decode.
    async with Client(server) as client:
        response = await client.call_tool(
            "manage_ugui",
            {"action": "set_text", "target": "Label", "properties": properties},
        )
    result = response.structured_content
    # Then: malformed characters are an input error rather than a retryable send failure.
    assert result["success"] is False
    assert result.get("hint") != "retry"
    assert "Unicode" in result["message"]
    assert len(result["message"]) < 300
    for seam in (http_send, boundary.preflight, boundary.instance):
        seam.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "properties,expected",
    [
        ('{"text":"\\ud83d\\ude00"}', {"text": "😀"}),
        ('{"text":"한글 Ελληνικά العربية"}', {"text": "한글 Ελληνικά العربية"}),
        ('{"text":"\\ufffd"}', {"text": "�"}),
    ],
)
async def test_preserves_valid_unicode_in_json_properties(
    boundary: Boundary,
    properties: str,
    expected: dict[str, JsonValue],
) -> None:
    # Given: complete surrogate pairs and ordinary Unicode characters encode as UTF-8.
    server = FastMCP("ugui-valid-unicode")
    server.tool()(manage_ugui)
    # When
    async with Client(server) as client:
        response = await client.call_tool(
            "manage_ugui",
            {"action": "set_text", "target": "Label", "properties": properties},
        )
    # Then: preserve text instead of normalizing, escaping, or replacing characters.
    assert response.structured_content["success"] is True
    assert boundary.mutate.await_args.args[3]["properties"] == expected


@pytest.mark.asyncio
async def test_rejects_nested_unpaired_surrogate_in_dictionary(
    boundary: Boundary,
) -> None:
    # Given: direct dictionaries use the same validation as decoded object strings.
    properties = {"nested": [{"text": "\ud800"}]}
    # When
    result = await manage_ugui(
        SimpleNamespace(),
        action="set_text",
        target="Label",
        properties=properties,
    )
    # Then
    assert result["success"] is False
    assert "Unicode" in result["message"]
    for seam in (boundary.mutate, boundary.preflight, boundary.instance):
        seam.assert_not_awaited()


@pytest.mark.asyncio
async def test_accepts_full_resolution_budget_when_at_bounds(boundary: Boundary) -> None:
    # Given
    resolutions = [{"width": 64, "height": 8192}] * 8
    # When
    result = await manage_ugui(
        SimpleNamespace(),
        action="diagnose",
        target="Canvas",
        max_nodes=1,
        resolutions=resolutions,
    )
    # Then
    assert result["success"] is True
    assert boundary.read.await_args.args[3]["resolutions"] == resolutions


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "options",
    [{"action": "ping"}, {"action": "create", "element_type": "canvas"}],
)
async def test_preserves_retry_details_when_legacy_transport_returns_response_model(
    boundary: Boundary,
    monkeypatch: pytest.MonkeyPatch,
    options: dict[str, JsonValue],
) -> None:
    # Given: stdio transport really returns MCPResponse when the retry wrapper fails.
    response = MCPResponse(
        success=False,
        error="Unity connection unavailable",
        hint="retry",
        data={"reason": "reloading", "retry_after_ms": 500},
    )
    legacy_send = AsyncMock(return_value=response)
    monkeypatch.setattr(config, "transport_mode", "stdio")
    monkeypatch.setattr(
        "services.tools.manage_ugui.send_with_unity_instance",
        send_with_unity_instance,
    )
    monkeypatch.setattr("services.tools.manage_ugui.send_mutation", send_mutation)
    monkeypatch.setattr(
        "services.tools.manage_ugui.async_send_command_with_retry",
        legacy_send,
    )
    monkeypatch.setattr(
        "transport.legacy.unity_connection.async_send_command_with_retry",
        legacy_send,
    )
    server = FastMCP("ugui-legacy-response")
    server.tool()(manage_ugui)
    # When: exercise the real MCP serialization and both production routing helpers.
    async with Client(server) as client:
        result = await client.call_tool("manage_ugui", options)
    # Then: machine-readable error/hint/data survive the legacy failure path.
    assert result.structured_content == response.model_dump()
    assert legacy_send.await_count == 1


@pytest.mark.asyncio
async def test_rejects_nested_json_when_properties_exceed_decoder_depth(
    boundary: Boundary,
) -> None:
    # Given: JSON supplied inside a string bypasses the MCP envelope's depth bound.
    properties = '{"anchorMin":' + '[' * 2000 + '0' + ']' * 2000 + '}'
    server = FastMCP("ugui-json-depth")
    server.tool()(manage_ugui)
    # When
    async with Client(server) as client:
        response = await client.call_tool(
            "manage_ugui",
            {"action": "set_rect", "target": "Canvas", "properties": properties},
        )
    result = response.structured_content
    # Then: invalid structure produces a regular error without reaching Unity.
    assert result["success"] is False
    for seam in (boundary.mutate, boundary.preflight, boundary.instance):
        seam.assert_not_awaited()


@pytest.mark.asyncio
async def test_bounds_error_when_resolution_keys_are_invalid(boundary: Boundary) -> None:
    # Given: each key's value is only a small scalar, but validation repeats its name.
    unknown = "unexpected_" + "x" * 2000
    resolutions = [
        {"width": 64, "height": 64, **{f"{unknown}{n}": 64 + n for n in range(20)}}
    ]
    server = FastMCP("ugui-json-validation-budget")
    server.tool()(manage_ugui)
    # When
    async with Client(server) as client:
        response = await client.call_tool(
            "manage_ugui",
            {"action": "diagnose", "target": "Canvas", "resolutions": resolutions},
        )
    result = response.structured_content
    # Then: reject without retaining/echoing an unbounded validation transcript.
    assert result["success"] is False
    assert len(result["message"]) < 1000
    boundary.read.assert_not_awaited()
