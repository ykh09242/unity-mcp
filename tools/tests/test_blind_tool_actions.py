"""Exercise the scope and attribution of action-choice observations."""

import json

import pytest

from tools.experiments import blind_tool_actions as actions
from tools.experiments.blind_tool_claims import score_claims
from tools.experiments.blind_tool_host import project_item
from tools.experiments.blind_tool_trace import client_trace, decision, observation


ASSET = "Assets/Editor/Views/Menu.uxml"
UI = {"task": actions.UI_TASK, "ui_asset": ASSET}
CAMERA = {"task": actions.CAMERA_TASK}
XML = (
    '<ui:UXML xmlns:ui="UnityEngine.UIElements" xmlns:custom="Game.Widgets">'
    "<ui:Button/><ui:TextField/><ui:TextField/><custom:Button/>"
    "<!-- <ui:Button/> --></ui:UXML>"
)
COUNTS = {"buttons": 1, "text_fields": 2}


@pytest.mark.parametrize("route", sorted(actions.UI_ROUTES))
def test_only_exact_saved_source_goes_through_a_trusted_relay(route):
    args = {"action": "read", "path": ASSET}
    assert decision(route, args, "", fixture=UI) == "unity"
    for unsafe in (
        {"path": "Assets/Other.uxml"},
        {"path": "Assets/Views/../Menu.uxml"},
        {"contents": XML},
        {"preview": False},
        {"include_values": True},
    ):
        assert decision(route, {**args, **unsafe}, "", fixture=UI) == "blocked"
    assert decision(route, args, "", fixture=CAMERA) == "blocked"
    assert decision(route, args, "", fixture=UI, endpoint=True) == "blocked"
    assert (
        decision("ui_source.update", {"action": "update", "path": ASSET}, "", fixture=UI)
        == "blocked"
    )


@pytest.mark.parametrize(
    "path",
    ["Assets/Resources./GameData/menu.uxml", "Assets/Editor/../Game/Menu.uxml", "Assets//a.uxml"],
)
def test_scoped_ui_asset_rejects_noncanonical_windows_components(path):
    assert not actions.valid_ui_path(path)


@pytest.mark.parametrize("route", sorted(actions.CAMERA_ROUTES - {actions.CAMERA_RESOURCE}))
def test_mixed_camera_tools_accept_only_argument_free_inventory(route):
    assert decision(route, {"action": "list_cameras"}, "", fixture=CAMERA) == "unity"
    assert (
        decision(route, {"action": "list_cameras", "target": "Object"}, "", fixture=CAMERA)
        == "blocked"
    )
    assert (
        decision("manage_camera.set_lens", {"action": "set_lens"}, "", fixture=CAMERA) == "blocked"
    )
    assert decision(route, {}, "", fixture=UI) == "blocked"


def test_maximum_requires_the_full_unique_unity_camera_result():
    cameras = [{"name": "A", "depth": -1}, {"name": "B", "depth": 0.5}]
    for route in actions.CAMERA_ROUTES:
        assert observation(route, {"unityCameras": cameras}) == {"camera": "B", "depth": 0.5}
        assert not observation(route, {"unityCameras": cameras, "truncated": True})
        assert not observation(route, {"unityCameras": [*cameras, {"name": "C", "depth": 0.5}]})
        assert not observation(route, {"unityCameras": [*cameras, {"name": "C"}]})


@pytest.mark.parametrize("route", sorted(actions.UI_ROUTES))
def test_ui_response_counts_only_authored_ui_elements(route):
    assert observation(route, {"path": ASSET, "contents": XML}, UI) == COUNTS
    assert observation(route, {"path": ASSET, "contents": XML[:-5]}, UI) == {}
    assert observation(route, {"path": ASSET, "contents": "<!DOCTYPE x>" + XML}, UI) == {}
    assert observation(route, {"path": "Assets/Other.uxml", "contents": XML}, UI) == {}


def test_host_projects_only_verified_exact_saved_source_into_actor_evidence(tmp_path):
    project = str(tmp_path)
    item = {
        "type": "commandExecution",
        "id": "read",
        "command": "Get-Content -LiteralPath " + ASSET,
        "cwd": project,
        "exitCode": 0,
        "aggregatedOutput": XML,
    }
    projected = project_item(item, "unityMCP", "", project, UI)
    assert projected["shell_receipt"]["facts"] == COUNTS
    stream = tmp_path / "events.jsonl"
    stream.write_text(json.dumps({"type": "item.completed", "item": projected}), encoding="utf8")
    trace = client_trace(stream, "", endpoint=False, project=project, fixture=UI)
    assert trace.saved_observations == [COUNTS]
    assert list(trace.calls.values())[0].route == actions.SAVED_UI
    claims = {
        key: {"value": value, "source": "asset_file", "channel": "file"}
        for key, value in COUNTS.items()
    }
    assert (
        score_claims(actions.UI_TASK, claims, [], trace.saved_observations)["status"] == "correct"
    )
    assert score_claims(actions.UI_TASK, claims, [], [])["status"] == "needs_review"
    other = {**item, "command": "Get-Content -LiteralPath Assets/Other.uxml"}
    assert "facts" not in project_item(other, "unityMCP", "", project, UI)["shell_receipt"]


def test_raw_cli_file_observation_uses_the_ui_fixture(tmp_path):
    event = {
        "type": "item.completed",
        "item": {
            "id": "read",
            "type": "command_execution",
            "command": f"Get-Content -Encoding utf8 -Raw -LiteralPath {ASSET}",
            "cwd": str(tmp_path),
            "exit_code": 0,
            "aggregated_output": XML,
        },
    }
    stream = tmp_path / "events.jsonl"
    stream.write_text(json.dumps(event), encoding="utf8")
    trace = client_trace(stream, "", endpoint=False, project=str(tmp_path), fixture=UI)
    assert trace.saved_observations == [COUNTS]


def test_missing_host_payload_is_distinct_from_a_reported_stale_editor():
    uri = "mcpforunity://editor/state"
    assert observation(uri, {}, CAMERA) == {}
    stale = {"staleness": {"is_stale": True}, "editor": {"active_scene": {"name": "Paused"}}}
    assert observation(uri, stale, CAMERA)["fresh"] is False
    assert observation(uri, stale, CAMERA)["scene_name"] == "Paused"
