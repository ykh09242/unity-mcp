"""Runtime diagnostics distinguish requested, installed and usable adapters."""

from types import SimpleNamespace

import pytest
from fastmcp import FastMCP

from core.sdk_compatibility import runtime_sdk_report
from transport.skills_extension import IDENTIFIER, install_skills


def test_skills_report_reads_actual_registration_and_reports_failed_installation():
    server = FastMCP("capability-state")
    server._skills_requested = True
    server._packaged_skills = None
    missing = runtime_sdk_report(server)["features"]["skills"]
    assert missing["configured"] is True
    assert missing["installed"] is False
    assert missing["state"] == "unavailable"
    assert missing["next_action"] == "repair_packaged_skill_bundle"
    server._packaged_skills = install_skills(server)
    active = runtime_sdk_report(server)["features"]["skills"]
    assert active["installed"] is True
    assert active["state"] == "active"
    assert active["next_action"] == "none"
    server._extensions.pop(IDENTIFIER)
    assert runtime_sdk_report(server)["features"]["skills"]["state"] == "unavailable"


@pytest.mark.parametrize(
    "requested,transport,installed,closed,expected,action",
    [
        (False, "http", False, False, "disabled", "enable_scene_load_inputs"),
        (True, "stdio", False, False, "unavailable", "use_http_transport"),
        (True, "http", False, False, "unavailable", "check_scene_input_installation"),
        (True, "http", True, False, "active", "none"),
        (True, "http", True, True, "inactive", "restart_server"),
    ],
)
def test_scene_input_report_has_actionable_installation_state(
    requested, transport, installed, closed, expected, action
):
    adapter = SimpleNamespace(ledger=SimpleNamespace(_closed=closed)) if installed else None
    server = SimpleNamespace(
        _scene_load_inputs_requested=requested,
        _scene_load_inputs_transport=transport,
        _scene_load_inputs=adapter,
        middleware=[adapter] if installed else [],
    )
    result = runtime_sdk_report(server)["features"]["scene_load_inputs"]
    assert result["configured"] == requested
    assert result["installed"] == installed
    assert result["state"] == expected
    assert result["next_action"] == action


def test_prepared_scene_adapter_without_registered_middleware_is_not_active():
    server = SimpleNamespace(
        _scene_load_inputs_requested=True,
        _scene_load_inputs_transport="http",
        _scene_load_inputs=SimpleNamespace(ledger=SimpleNamespace(_closed=False)),
        middleware=[],
    )
    result = runtime_sdk_report(server)["features"]["scene_load_inputs"]
    assert result["state"] == "unavailable"
    assert result["reason"] == "middleware_missing"


def test_unreviewed_sdk_cannot_report_optional_features_active():
    server = SimpleNamespace(
        _skills_requested=True,
        _scene_load_inputs_requested=True,
        _scene_load_inputs_transport="http",
    )
    result = runtime_sdk_report(server, version_reader=lambda _: "999.0.0")
    for name in ("skills", "scene_load_inputs"):
        assert result["features"][name]["state"] == "sdk_unsupported"
        assert result["features"][name]["next_action"] == "install_reviewed_sdk"
