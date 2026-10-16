"""UI artifact comparisons reject ambiguity and preserve meaningful semantic changes."""

from copy import deepcopy
import json
from pathlib import Path
import socket

import click
from click.testing import CliRunner
from pydantic import JsonValue
import pytest

from cli.commands.play_scenario_ui import register_ui_commands
from cli.utils.play_scenario_ui import UiComparisonPolicy, compare_ui_snapshots, load_ui_snapshot


def node(identity: str, parent: str | None = None, index: int = 0) -> dict[str, JsonValue]:
    return {
        "id": identity,
        "parent": parent,
        "sibling_index": index,
        "type": "RectTransform",
        "components": ["RectTransform"],
        "classes": [],
        "text": None,
        "active": True,
        "visible": True,
        "interactable": None,
        "renderer_culled": None,
        "alpha": 1.0,
        "geometry": {"bounds": [0.0, 0.0, 1920.0, 1080.0]},
    }


def snapshot() -> dict[str, JsonValue]:
    return {
        "schema_version": 1,
        "framework": "ugui",
        "mode": "play",
        "scenes": ["Assets/Scenes/Menu.unity"],
        "resolution": {"width": 1920, "height": 1080},
        "target": "Menu::Canvas",
        "evidence": {
            "geometry_space": "world_aabb",
            "visibility_method": "render_eligibility",
            "interaction_method": "component_state",
            "occlusion_status": "renderer_cull_only",
            "layout_status": "current",
        },
        "complete": True,
        "max_nodes": 200,
        "nodes": [node("Menu::Canvas"), node("Menu::Canvas/Button", "Menu::Canvas")],
        "limitations": ["Actual occlusion remains unavailable."],
    }


def save(path: Path, value: dict[str, JsonValue]) -> Path:
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def test_reports_added_removed_and_semantic_fields_when_tree_changes(tmp_path: Path) -> None:
    # Given
    before = snapshot()
    after = deepcopy(before)
    after["nodes"] = [
        {**node("Menu::Canvas"), "text": "Ready", "active": False},
        node("Menu::Canvas/Label", "Menu::Canvas"),
    ]
    # When
    result = compare_ui_snapshots(
        save(tmp_path / "old.json", before), save(tmp_path / "new.json", after)
    )
    # Then
    assert result["status"] == "changed"
    assert result["added"] == ["Menu::Canvas/Label"]
    assert result["removed"] == ["Menu::Canvas/Button"]
    assert result["changed"] == [{"id": "Menu::Canvas", "fields": ["text", "active"]}]


@pytest.mark.parametrize("delta,expected", [(0.5, "same"), (0.501, "changed")])
def test_geometry_tolerance_is_inclusive_when_coordinates_shift(
    tmp_path: Path, delta: float, expected: str
) -> None:
    # Given
    before, after = snapshot(), snapshot()
    after["nodes"] = [
        {**node("Menu::Canvas"), "geometry": {"bounds": [delta, 0.0, 1920.0, 1080.0]}},
        node("Menu::Canvas/Button", "Menu::Canvas"),
    ]
    # When
    result = compare_ui_snapshots(
        save(tmp_path / "old.json", before),
        save(tmp_path / "new.json", after),
        UiComparisonPolicy(0.5),
    )
    # Then
    assert result["status"] == expected


@pytest.mark.parametrize(
    "field,value",
    [
        ("framework", "uitoolkit"),
        ("resolution", {"width": 1280, "height": 720}),
        ("mode", "edit"),
        ("scenes", ["Assets/Scenes/Other.unity"]),
        ("complete", False),
        ("complete", 1),
        ("schema_version", True),
        ("nodes", [node("Menu::Canvas"), node("Menu::Canvas")]),
        ("nodes", [node("Menu::Canvas"), node("Menu::Canvas/Child", "missing")]),
        ("max_nodes", 1),
        ("evidence", {}),
        ("unexpected", True),
    ],
)
def test_rejects_incompatible_or_incomplete_evidence_when_selected(
    tmp_path: Path, field: str, value: JsonValue
) -> None:
    # Given
    after = snapshot()
    after[field] = value
    baseline, candidate = (
        save(tmp_path / "old.json", snapshot()),
        save(tmp_path / "new.json", after),
    )
    # When / Then
    with pytest.raises(ValueError):
        compare_ui_snapshots(baseline, candidate)


@pytest.mark.parametrize(
    "geometry",
    [{"bounds": [float("nan"), 0, 1, 1]}, {"bounds": [True, 0, 1, 1]}, {"bounds": [0, 0, -1, 1]}],
)
def test_rejects_invalid_geometry_when_parsing(tmp_path: Path, geometry: JsonValue) -> None:
    # Given
    value = snapshot()
    value["nodes"] = [{**node("Menu::Canvas"), "geometry": geometry}]
    path = save(tmp_path / "snapshot.json", value)
    # When / Then
    with pytest.raises(ValueError):
        load_ui_snapshot(path)


def test_rejects_duplicate_json_keys_when_parsing(tmp_path: Path) -> None:
    # Given
    path = tmp_path / "snapshot.json"
    path.write_text('{"schema_version":1,"schema_version":1}', encoding="utf-8")
    # When / Then
    with pytest.raises(ValueError):
        load_ui_snapshot(path)


def test_requires_actual_occlusion_when_policy_requests_it(tmp_path: Path) -> None:
    # Given
    path = save(tmp_path / "snapshot.json", snapshot())
    # When / Then
    with pytest.raises(ValueError):
        compare_ui_snapshots(path, path, UiComparisonPolicy(require_occlusion=True))


def test_cli_is_offline_and_preserves_existing_output_when_comparing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given
    def network_forbidden(*_args, **_kwargs):
        pytest.fail("UI snapshot comparison attempted network access")

    monkeypatch.setattr(socket.socket, "connect", network_forbidden)
    monkeypatch.setattr(
        "cli.commands.play_scenario_ui.get_config", lambda: type("Config", (), {"format": "json"})()
    )
    group = click.Group()
    register_ui_commands(group)
    path = save(tmp_path / "snapshot.json", snapshot())
    output = tmp_path / "comparison.json"
    output.write_text("keep", encoding="utf-8")
    # When
    result = CliRunner().invoke(
        group, ["compare-ui-snapshots", str(path), str(path), "--output", str(output)]
    )
    # Then
    assert result.exit_code == 2
    assert json.loads(result.output)["status"] == "not_comparable"
    assert output.read_text(encoding="utf-8") == "keep"


@pytest.mark.parametrize("change,exit_code", [(False, 0), (True, 1)])
def test_cli_reports_exit_status_when_state_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, change: bool, exit_code: int
) -> None:
    # Given
    monkeypatch.setattr(
        "cli.commands.play_scenario_ui.get_config", lambda: type("Config", (), {"format": "json"})()
    )
    group = click.Group()
    register_ui_commands(group)
    before, after = snapshot(), snapshot()
    if change:
        after["nodes"] = [node("Menu::Canvas")]
    # When
    result = CliRunner().invoke(
        group,
        [
            "compare-ui-snapshots",
            str(save(tmp_path / "a.json", before)),
            str(save(tmp_path / "b.json", after)),
        ],
    )
    # Then
    assert result.exit_code == exit_code
    assert json.loads(result.output)["compared"] is True


def test_capture_saves_complete_parseable_evidence_when_native_response_is_valid(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given
    native = snapshot()
    native["schema_version"] = 2
    native["evidence"].update(
        control_state_method="typed_allowlist_v1", input_privacy="recognized_input_ownership_v1"
    )
    for item in native["nodes"]:
        item["state"] = {}
    monkeypatch.setattr(
        "cli.commands.play_scenario_ui.get_config", lambda: type("Config", (), {"format": "json"})()
    )
    monkeypatch.setattr(
        "cli.commands.play_scenario_ui.run_command",
        lambda *_args: {"result": {"success": True, "data": native}},
    )
    group = click.Group()
    register_ui_commands(group)
    output = tmp_path / "snapshot.json"
    # When
    result = CliRunner().invoke(
        group,
        [
            "capture-ui-snapshot",
            "--framework",
            "ugui",
            "--target",
            "Canvas",
            "--output",
            str(output),
        ],
    )
    # Then
    assert result.exit_code == 0, result.output
    assert load_ui_snapshot(output).target == "Menu::Canvas"
    assert len(load_ui_snapshot(output).nodes) == 2
