"""Scene-edit boundary validation, routing and CLI machine-readable output."""

import importlib
import json
from unittest.mock import AsyncMock, Mock

import pytest
from click.testing import CliRunner

from cli.commands.scene_edits import scene_edit
from cli.utils.config import CLIConfig


tool = importlib.import_module("services.tools.manage_scene_edits")
commands = importlib.import_module("cli.commands.scene_edits")


def edit(**overrides):
    return {
        "target": 42,
        "component_type": "Transform",
        "property": "localPosition",
        "value": [1, 2, 3],
        **overrides,
    }


@pytest.fixture
def bridge(monkeypatch):
    send = AsyncMock(return_value={"success": True, "data": {"preview_id": "example"}})
    gate = AsyncMock(return_value=None)
    monkeypatch.setattr(tool, "get_unity_instance_from_context", AsyncMock(return_value="fixture"))
    monkeypatch.setattr(tool, "preflight", gate)
    monkeypatch.setattr(tool, "send_with_unity_instance", send)
    return send, gate


@pytest.mark.asyncio
async def test_hundreds_of_scene_edits_and_structure_operations_are_forwarded(bridge):
    send, _ = bridge
    edits = [edit(target=index) for index in range(300)]
    result = await tool.manage_scene_edits(None, "preview", edits=edits)
    assert result["success"] is True
    assert send.call_args.args[3]["edits"] == edits
    operations = [
        {"op": "create", "temp_id": f"$node{index}", "name": f"Node{index}", "parent": 42}
        for index in range(100)
    ]
    result = await tool.manage_scene_edits(
        None, "preview_structure", target=42, operations=operations
    )
    assert result["success"] is True
    assert len(send.call_args.args[3]["operations"]) == 100


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "arguments",
    [
        {"action": "preview", "edits": []},
        {"action": "preview", "edits": [edit()] * 1001},
        {"action": "preview", "preview_id": "token"},
        {"action": "apply", "preview_id": "token", "edits": [edit()]},
        {"action": "apply", "edits": [edit()]},
        {"action": "preview", "edits": [edit(value=[float("nan"), 0, 0])]},
        {"action": "preview", "edits": [edit(component_type="CustomScript")]},
        {"action": "preview", "edits": [edit(target=True)]},
        {"action": "preview", "edits": [edit(component_index=-1)]},
        {"action": "preview", "edits": [edit(component_index=True)]},
        {"action": "preview", "edits": [edit(unknown="ignored")]},
        {"action": "apply", "preview_id": " "},
        {"action": "apply", "preview_id": "x" * 65},
        {"action": "apply", "preview_id": "token", "undo_name": " "},
        {"action": "preview", "edits": [edit(value="x" * (4 * 1024 * 1024 + 1))]},
    ],
)
async def test_invalid_inputs_never_preflight_or_dispatch(bridge, arguments):
    send, gate = bridge
    result = await tool.manage_scene_edits(None, **arguments)
    assert result["success"] is False
    gate.assert_not_awaited()
    send.assert_not_awaited()


@pytest.mark.asyncio
async def test_preview_does_not_refresh_assets_and_routes_selected_instance(bridge):
    send, gate = bridge
    result = await tool.manage_scene_edits(None, "preview", edits=[edit()])
    assert result["success"] is True
    assert send.call_args.args[1:3] == ("fixture", "manage_scene_edits")
    assert send.call_args.args[3]["edits"] == [edit()]
    gate.assert_awaited_once_with(None, wait_for_no_compile=True, refresh_if_dirty=False)


@pytest.mark.asyncio
async def test_apply_preserves_expected_false_zero_and_native_failure_details(bridge):
    send, _ = bridge
    failure = {"success": False, "error": "drift", "data": {"rollback_verified": True}}
    send.return_value = failure
    edits = [
        edit(component_type="BoxCollider", property="m_IsTrigger", value=True, expected_old=False),
        edit(component_type="SphereCollider", property="m_Radius", value=2, expected_old=0),
    ]
    result = await tool.manage_scene_edits(None, "apply", edits=edits)
    assert result == failure
    assert send.call_args.args[3]["edits"] == edits


@pytest.mark.asyncio
async def test_apply_token_is_forwarded_without_edits(bridge):
    send, _ = bridge
    await tool.manage_scene_edits(
        None, "apply", preview_id="one-attempt", undo_name="Grouped edits"
    )
    assert send.call_args.args[3] == {
        "action": "apply",
        "preview_id": "one-attempt",
        "undo_name": "Grouped edits",
    }


@pytest.fixture
def cli_bridge(monkeypatch):
    send = Mock(return_value={"success": True, "data": {"preview_id": "example"}})
    monkeypatch.setattr(commands, "get_config", lambda: CLIConfig(format="json"))
    monkeypatch.setattr(commands, "run_command", send)
    return send


def test_cli_preview_file_returns_one_json_document(tmp_path, cli_bridge):
    source = tmp_path / "edits.json"
    source.write_text(json.dumps([edit()]), encoding="utf-8")
    result = CliRunner().invoke(scene_edit, ["preview", str(source)])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["data"]["preview_id"] == "example"
    assert cli_bridge.call_args.args[0] == "manage_scene_edits"
    assert cli_bridge.call_args.args[1] == {"action": "preview", "edits": [edit()]}


@pytest.mark.parametrize(
    "args",
    [
        ["apply"],
        ["apply", "--preview-id", " "],
        ["apply", "--preview-id", "token", "--undo-name", " "],
    ],
)
def test_cli_invalid_sources_do_not_dispatch(cli_bridge, args):
    result = CliRunner().invoke(scene_edit, args)
    assert result.exit_code != 0
    cli_bridge.assert_not_called()


def test_cli_direct_apply_requires_expectations_and_rejects_both_sources(tmp_path, cli_bridge):
    source = tmp_path / "edits.json"
    source.write_text(json.dumps([edit()]), encoding="utf-8")
    for args in (
        ["apply", "--edits-file", str(source)],
        ["apply", "--edits-file", str(source), "--preview-id", "token"],
    ):
        result = CliRunner().invoke(scene_edit, args)
        assert result.exit_code != 0
    cli_bridge.assert_not_called()
    source.write_text(json.dumps([edit(expected_old=[0, 0, 0])]), encoding="utf-8")
    result = CliRunner().invoke(scene_edit, ["apply", "--edits-file", str(source)])
    assert result.exit_code == 0, result.output
    assert cli_bridge.call_args.args[1]["edits"][0]["expected_old"] == [0, 0, 0]


def test_cli_native_failure_prints_json_and_exits_nonzero(cli_bridge):
    failure = {"success": False, "error": "drift", "data": {"rollback_verified": False}}
    cli_bridge.return_value = failure
    result = CliRunner().invoke(scene_edit, ["apply", "--preview-id", "token"])
    assert result.exit_code == 1
    assert json.loads(result.output) == failure
