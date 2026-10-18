"""Admission, decoder integrity, permission and real SDK publication for device profiles."""

import copy
import json
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastmcp import Client, FastMCP
from pydantic import ValidationError

from models.ugui_device_profile_diagnostics import (
    DEVICE_PROFILES,
    validate_ugui_device_profile_diagnostics,
)
from services.operation_permissions import (
    OperationPermissionError,
    authorize_operation,
    parse_permissions,
)
from services.registry import get_registered_tools
from services.tools.manage_ugui import manage_ugui


def virtual(dpi=160):
    return {
        "source": "virtual",
        "width": 800,
        "height": 600,
        "dpi": dpi,
        "safe_area": [0, 0, 800, 600],
        "orientation": "landscape_left",
    }


@pytest.fixture
def boundary(monkeypatch):
    calls = {
        "preflight": AsyncMock(return_value=None),
        "get_unity_instance_from_context": AsyncMock(return_value="ui14@fixture"),
        "send_with_unity_instance": AsyncMock(return_value={"success": True, "data": {}}),
        "send_mutation": AsyncMock(
            side_effect=AssertionError("device profiles used mutation path")
        ),
    }
    for name, call in calls.items():
        monkeypatch.setattr(f"services.tools.manage_ugui.{name}", call)
    return calls


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "profiles",
    [
        None,
        [],
        [virtual()] * 9,
        [{"source": "observed", "dpi": 160}],
        [virtual(1e-300)],
        [virtual(1e308)],
        [virtual(True)],
        [{**virtual(), "safe_area": [0, 0, 0, 600]}],
        [{**virtual(), "safe_area": [1, 0, 800, 600]}],
        [{**virtual(), "safe_area": [0, 0, 1e-300, 600]}],
        [{**virtual(), "orientation": "auto_rotation"}],
        [{**virtual(), "width": True}],
        [{**virtual(), "unexpected": 1}],
    ],
)
async def test_invalid_profile_stops_before_preflight_and_transport(boundary, profiles):
    # Given / When
    result = await manage_ugui(
        SimpleNamespace(),
        action="diagnose_device_profiles",
        target=123,
        device_profiles=profiles,
    )
    # Then
    assert result == {"success": False, "message": "invalid_device_profile_request"}
    for call in boundary.values():
        call.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("threshold", [True, 0, -1, 1e-300, 1e308, float("nan")])
async def test_invalid_threshold_has_zero_transport(boundary, threshold):
    # Given / When
    result = await manage_ugui(
        SimpleNamespace(),
        action="diagnose_device_profiles",
        target=123,
        device_profiles=[virtual()],
        minimum_touch_target_mm=threshold,
    )
    # Then
    assert result["message"] == "invalid_device_profile_request"
    for call in boundary.values():
        call.assert_not_awaited()


def test_eight_closed_profiles_are_admitted_but_nine_are_rejected():
    # Given / When
    eight = DEVICE_PROFILES.validate_python([virtual()] * 8)
    # Then
    assert len(eight) == 8
    with pytest.raises(ValidationError):
        DEVICE_PROFILES.validate_python([virtual()] * 9)


@pytest.mark.parametrize("native", [False, True])
def test_read_permission_admits_profile_action_and_other_grants_do_not(native):
    # Given
    read = parse_permissions({"unity_mcp_permissions": ["read"]}, strict=True)
    # When
    authorize_operation(read, "manage_ugui", {"action": "diagnose_device_profiles"}, native=native)
    # Then
    for grant in ("edit", "run", "test"):
        authority = parse_permissions({"unity_mcp_permissions": [grant]}, strict=True)
        with pytest.raises(OperationPermissionError):
            authorize_operation(
                authority, "manage_ugui", {"action": "diagnose_device_profiles"}, native=native
            )


@pytest.mark.asyncio
async def test_old_plugin_response_is_rejected_after_read_dispatch(boundary):
    # Given: an old plugin that returns legacy geometry instead of this closed report.
    boundary["send_with_unity_instance"].return_value = {
        "success": True,
        "data": {"framework": "ugui", "geometry": []},
    }
    # When
    result = await manage_ugui(
        SimpleNamespace(),
        action="diagnose_device_profiles",
        target=123,
        device_profiles=[virtual()],
        minimum_touch_target_mm=7,
    )
    # Then
    assert result == {"success": False, "message": "unsupported_device_profile_response"}
    boundary["send_mutation"].assert_not_awaited()
    boundary["preflight"].assert_awaited_once()
    assert boundary["preflight"].await_args.kwargs["requires_no_tests"] is False
    assert boundary["send_with_unity_instance"].await_args.args[3]["minimum_touch_target_mm"] == 7


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["2026-07-28", "legacy"])
async def test_real_sdk_publishes_closed_profile_schema_and_rejects_invalid_input(boundary, mode):
    # Given: actual supported SDK modes, with transport forbidden for malformed input.
    server = FastMCP("ui14")
    entry = next(item for item in get_registered_tools() if item["name"] == "manage_ugui")
    server.tool(name=entry["name"], **entry["kwargs"])(manage_ugui)
    async with Client(server, mode=mode) as client:
        tools = await client.list_tools()
        tool = next(item for item in tools if item.name == "manage_ugui")
        schema = tool.input_schema if hasattr(tool, "input_schema") else tool.inputSchema
        assert "diagnose_device_profiles" in schema["properties"]["action"]["enum"]
        assert schema["properties"]["device_profiles"]["default"] is None
        assert schema["properties"]["minimum_touch_target_mm"]["default"] is None
        # When
        result = await client.call_tool(
            "manage_ugui",
            {
                "action": "diagnose_device_profiles",
                "target": 123,
                "device_profiles": [{"source": "observed", "width": 800}],
            },
            raise_on_error=False,
        )
    # Then
    assert result.is_error
    for call in boundary.values():
        call.assert_not_awaited()


@pytest.fixture
def actual_native_case():
    """NativeQA supplies the request/report pair captured by the native fixture."""
    source = os.environ.get("UI14_NATIVE_REPORT_CASE")
    if not source:
        pytest.skip(
            "Actual native request/report is required; synthetic payloads do not satisfy this acceptance check."
        )
    case = json.loads(Path(source).read_text(encoding="utf-8"))
    return case


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["2026-07-28", "legacy"])
async def test_actual_native_report_passes_both_real_sdk_modes(boundary, actual_native_case, mode):
    # Given: actual native evidence, retained separately from authored source artifacts.
    request = actual_native_case["request"]
    report = actual_native_case["report"]
    assert len(report["profiles"]) == 2
    boundary["send_with_unity_instance"].return_value = {"success": True, "data": report}
    server = FastMCP("ui14-native")
    entry = next(item for item in get_registered_tools() if item["name"] == "manage_ugui")
    server.tool(name=entry["name"], **entry["kwargs"])(manage_ugui)
    async with Client(server, mode=mode) as client:
        # When
        response = await client.call_tool("manage_ugui", request)
    # Then
    assert not response.is_error
    content = response.structured_content
    assert content["success"] is True
    assert content["data"]["profiles"][0]["nodes"][0]["width_mm"] == pytest.approx(7.62)
    assert content["data"]["profiles"][1]["nodes"][0]["width_mm"] == pytest.approx(3.81)
    boundary["send_mutation"].assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["2026-07-28", "legacy"])
async def test_actual_fractional_native_report_passes_both_sdk_modes(boundary, mode):
    # Given: actual fractional native values; absence never becomes synthetic acceptance.
    source = os.environ.get("UI14_NATIVE_FRACTIONAL_CASE")
    if not source:
        pytest.skip(
            "Actual fractional native request/report is required for this acceptance check."
        )
    case = json.loads(Path(source).read_text(encoding="utf-8"))
    boundary["send_with_unity_instance"].return_value = {"success": True, "data": case["report"]}
    server = FastMCP("ui14-fractional-native")
    entry = next(item for item in get_registered_tools() if item["name"] == "manage_ugui")
    server.tool(name=entry["name"], **entry["kwargs"])(manage_ugui)
    async with Client(server, mode=mode) as client:
        # When
        response = await client.call_tool("manage_ugui", case["request"])
    # Then
    assert not response.is_error
    assert response.structured_content["success"] is True
    boundary["send_mutation"].assert_not_awaited()


@pytest.mark.parametrize("tamper", ["dpi", "safe", "threshold", "budget", "unknown", "projection"])
def test_actual_native_decoder_refuses_forged_evidence(actual_native_case, tamper):
    # Given
    report = copy.deepcopy(actual_native_case["report"])
    node = report["profiles"][0]["nodes"][0]
    match tamper:
        case "dpi":
            report["profiles"][0]["dpi"] = None
        case "safe":
            node["safe_area_relation"] = "possible_unsafe_region"
        case "threshold":
            node["touch_target"] = "below_caller_threshold"
        case "budget":
            report["budget"]["evaluations"] += 1
        case "unknown":
            report["unexpected"] = "rejected"
        case "projection":
            report["profiles"][0]["route"] = "current_camera_geometry"
    # When / Then
    with pytest.raises(ValueError, match="unsupported_device_profile_response"):
        validate_ugui_device_profile_diagnostics(report)
