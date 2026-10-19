"""Explicit component provenance survives request, native response and saved artifacts."""

from copy import deepcopy
import json
from types import SimpleNamespace
from typing import assert_never
from unittest.mock import AsyncMock

from click.testing import CliRunner
import pytest

from cli.commands.play_scenario_ui import capture_ui_snapshot as capture_command
from cli.utils.play_scenario_ui import compare_ui_snapshots
from models.ui_snapshots import validate_ui_snapshot
from services.tools.capture_ui_snapshot import UiSnapshotRequest, capture_ui_snapshot
from .test_play_scenario_ui import save, snapshot
from .test_ui_snapshot_control_state import snapshot_v2
from .test_ui_snapshot_identity import mapping, snapshot_v3


def toolkit_snapshot(version=2, component_type="ui_document"):
    value = snapshot_v3() if version == 3 else snapshot_v2()
    value["framework"] = "uitoolkit"
    value["evidence"].update(geometry_space="panel_points", occlusion_status="unavailable")
    if component_type is not None:
        value["evidence"]["component_type"] = component_type
    return value


@pytest.mark.parametrize("framework", ["ugui", "uitoolkit"])
def test_omitted_selector_preserves_sparse_requests_when_framework_is_valid(framework):
    # Given / When
    request = UiSnapshotRequest(framework=framework, target="Canvas")
    # Then
    assert "component_type" not in request.model_dump(exclude_none=True)


@pytest.mark.parametrize(
    "selector", [None, "UIDocument", "UI_DOCUMENT", " auto ", "", "unknown", 1, True, [], {}]
)
def test_invalid_supplied_selector_rejects_when_request_is_parsed(selector):
    # Given / When / Then
    with pytest.raises(ValueError):
        UiSnapshotRequest(framework="uitoolkit", target="Canvas", component_type=selector)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "framework,selector",
    [
        ("ugui", "auto"),
        ("ugui", "ui_document"),
        ("ugui", "panel_renderer"),
        ("uitoolkit", "unknown"),
        ("uitoolkit", True),
    ],
)
async def test_invalid_selection_stays_local_when_capture_is_called(
    monkeypatch, framework, selector
):
    # Given
    ready, dispatch = AsyncMock(), AsyncMock()
    monkeypatch.setattr("services.tools.capture_ui_snapshot.preflight", ready)
    monkeypatch.setattr("services.tools.capture_ui_snapshot.send_with_unity_instance", dispatch)
    # When
    result = await capture_ui_snapshot(
        SimpleNamespace(), framework, "Canvas", component_type=selector
    )
    # Then
    assert result["success"] is False
    ready.assert_not_awaited()
    dispatch.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "selector,resolved",
    [
        ("auto", "ui_document"),
        ("auto", "panel_renderer"),
        ("ui_document", "ui_document"),
        ("panel_renderer", "panel_renderer"),
    ],
)
@pytest.mark.parametrize("version", [2, 3])
async def test_selection_forwards_exactly_when_native_kind_matches(
    monkeypatch, selector, resolved, version
):
    # Given
    native = toolkit_snapshot(version, resolved)
    dispatch = AsyncMock(return_value={"success": True, "data": native})
    monkeypatch.setattr(
        "services.tools.capture_ui_snapshot.preflight", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(
        "services.tools.capture_ui_snapshot.get_unity_instance_from_context",
        AsyncMock(return_value="Editor@ui13"),
    )
    monkeypatch.setattr("services.tools.capture_ui_snapshot.send_with_unity_instance", dispatch)
    arguments = {"component_type": selector}
    if version == 3:
        arguments["identity_map"] = mapping()
    # When
    result = await capture_ui_snapshot(SimpleNamespace(), "uitoolkit", "Canvas", **arguments)
    # Then
    assert result == {"success": True, "data": native}
    assert dispatch.await_args.args[1:] == (
        "Editor@ui13",
        "capture_ui_snapshot",
        {"framework": "uitoolkit", "target": "Canvas", "max_nodes": 200, **arguments},
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", [None, "panel_renderer", "auto", "unknown"])
async def test_explicit_capture_refuses_when_plugin_ignores_or_misreports_selection(
    monkeypatch, kind
):
    # Given
    dispatch = AsyncMock(
        return_value={"success": True, "data": toolkit_snapshot(component_type=kind)}
    )
    monkeypatch.setattr(
        "services.tools.capture_ui_snapshot.preflight", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(
        "services.tools.capture_ui_snapshot.get_unity_instance_from_context",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr("services.tools.capture_ui_snapshot.send_with_unity_instance", dispatch)
    # When
    result = await capture_ui_snapshot(
        SimpleNamespace(), "uitoolkit", "Canvas", component_type="ui_document"
    )
    # Then
    assert result["success"] is False


@pytest.mark.parametrize("version", [2, 3])
@pytest.mark.parametrize("kind", ["ui_document", "panel_renderer"])
def test_provenance_round_trips_when_artifact_is_current(version, kind):
    # Given
    native = toolkit_snapshot(version, kind)
    # When
    parsed = validate_ui_snapshot(native)
    # Then
    assert json.loads(parsed.model_dump_json())["evidence"] == native["evidence"]


@pytest.mark.parametrize("version", [2, 3])
def test_legacy_evidence_stays_sparse_when_component_was_omitted(version):
    # Given
    native = toolkit_snapshot(version, None)
    # When
    parsed = validate_ui_snapshot(native)
    # Then
    assert json.loads(parsed.model_dump_json())["evidence"] == native["evidence"]


@pytest.mark.parametrize("version", [2, 3])
@pytest.mark.parametrize("kind", [None, "auto", "unknown", True])
def test_invalid_recorded_kind_refuses_when_toolkit_evidence_is_supplied(version, kind):
    # Given
    value = toolkit_snapshot(version)
    value["evidence"]["component_type"] = kind
    # When / Then
    with pytest.raises(ValueError):
        validate_ui_snapshot(value)


@pytest.mark.parametrize("version", [1, 2, 3])
@pytest.mark.parametrize("kind", [None, "auto", "ui_document", "panel_renderer"])
def test_ugui_refuses_component_evidence_when_supplied(version, kind):
    # Given
    value = {1: snapshot, 2: snapshot_v2, 3: snapshot_v3}[version]()
    value["evidence"]["component_type"] = kind
    # When / Then
    with pytest.raises(ValueError):
        validate_ui_snapshot(value)


@pytest.mark.parametrize(
    "corruption", ["private_input", "overflow", "nonfinite", "identity_method"]
)
def test_component_evidence_cannot_bypass_existing_bounds_when_payload_is_invalid(corruption):
    # Given
    value = toolkit_snapshot(3)
    match corruption:
        case "private_input":
            value["nodes"][0].update(
                text="UI13_PRIVATE",
                state={"input": {"redacted": True, "read_only": False, "password": True}},
            )
        case "overflow":
            value["max_nodes"] = 1
        case "nonfinite":
            value["nodes"][0]["geometry"]["bounds"][0] = float("nan")
        case "identity_method":
            value["evidence"]["identity_method"] = "unknown"
        case unreachable:
            assert_never(unreachable)
    # When / Then
    with pytest.raises(ValueError) as error:
        validate_ui_snapshot(value, expected_component_type="auto")
    assert "UI13_PRIVATE" not in str(error.value)


@pytest.mark.parametrize("kind", [None, "panel_renderer"])
def test_comparator_refuses_when_component_provenance_differs(tmp_path, kind):
    # Given
    before = save(tmp_path / "before.json", toolkit_snapshot())
    after = save(tmp_path / "after.json", toolkit_snapshot(component_type=kind))
    # When / Then
    with pytest.raises(ValueError, match="evidence"):
        compare_ui_snapshots(before, after)


def test_comparator_matches_actual_kind_when_explicit_auto_and_document_resolve_equally(tmp_path):
    # Given
    native = toolkit_snapshot()
    auto = validate_ui_snapshot(native, expected_component_type="auto")
    document = validate_ui_snapshot(deepcopy(native), expected_component_type="ui_document")
    # When
    result = compare_ui_snapshots(
        save(tmp_path / "auto.json", json.loads(auto.model_dump_json())),
        save(tmp_path / "document.json", json.loads(document.model_dump_json())),
    )
    # Then
    assert result["status"] == "same"


@pytest.mark.parametrize("kind", ["auto", "ui_document", "panel_renderer"])
@pytest.mark.parametrize("version", [2, 3])
def test_cli_persists_actual_kind_when_component_is_selected(tmp_path, monkeypatch, kind, version):
    # Given
    resolved = "panel_renderer" if kind == "panel_renderer" else "ui_document"
    calls = []

    def dispatch(command, arguments, config):
        calls.append((command, arguments))
        return {"success": True, "data": toolkit_snapshot(version, resolved)}

    monkeypatch.setattr("cli.commands.play_scenario_ui.run_command", dispatch)
    monkeypatch.setattr(
        "cli.commands.play_scenario_ui.get_config", lambda: SimpleNamespace(format="json")
    )
    output = tmp_path / "snapshot.json"
    arguments = [
        "--framework",
        "uitoolkit",
        "--target",
        "Canvas",
        "--component-type",
        kind,
        "--output",
        str(output),
    ]
    expected = {
        "framework": "uitoolkit",
        "target": "Canvas",
        "max_nodes": 200,
        "component_type": kind,
    }
    if version == 3:
        source = save(tmp_path / "map.json", {"identity_map": mapping()})
        arguments.extend(["--identity-map-file", str(source)])
        expected["identity_map"] = mapping()
    # When
    result = CliRunner().invoke(capture_command, arguments)
    # Then
    assert result.exit_code == 0, result.output
    assert calls == [("capture_ui_snapshot", expected)]
    assert json.loads(output.read_text(encoding="utf-8"))["evidence"]["component_type"] == resolved


@pytest.mark.parametrize("failure", ["ugui", "missing", "wrong", "existing"])
def test_cli_leaves_no_new_artifact_when_selection_cannot_be_proven(tmp_path, monkeypatch, failure):
    # Given
    calls = []

    def dispatch(*args):
        calls.append(args)
        return {
            "success": True,
            "data": toolkit_snapshot(
                component_type=None if failure == "missing" else "panel_renderer"
            ),
        }

    monkeypatch.setattr("cli.commands.play_scenario_ui.run_command", dispatch)
    monkeypatch.setattr(
        "cli.commands.play_scenario_ui.get_config", lambda: SimpleNamespace(format="json")
    )
    output = tmp_path / "snapshot.json"
    if failure == "existing":
        output.write_text("keep", encoding="utf-8")
    # When
    result = CliRunner().invoke(
        capture_command,
        [
            "--framework",
            "ugui" if failure == "ugui" else "uitoolkit",
            "--target",
            "Canvas",
            "--component-type",
            "ui_document",
            "--output",
            str(output),
        ],
    )
    # Then
    assert result.exit_code != 0
    if failure in ("ugui", "existing"):
        assert calls == []
    if failure == "existing":
        assert output.read_text(encoding="utf-8") == "keep"
    else:
        assert not output.exists()
