"""Exact multi-scene schema/wire contracts; controlled peer, no native execution."""

import importlib
import json
from pathlib import Path
from unittest.mock import AsyncMock

import httpx
import pytest
from cli.main import cli
from cli.utils import connection
from click.testing import CliRunner
from fastmcp import Client, FastMCP
from models.play_scenarios import PlayScenario, normalize_native_definition
from models.scenario_recording import ScenarioRecordingCommand
from pydantic import ValidationError


def definition():
    return {
        "name": "scene-proof",
        "setup_steps": [{"name": "Anchor", "action": "load_scene", "scene": "Assets/A.unity"}],
        "steps": [
            {
                "name": "Add",
                "action": "load_scene",
                "scene": "Assets/B.unity",
                "load_mode": "additive",
            },
            {
                "name": "Loaded",
                "action": "wait_scene",
                "scene": "Assets/B.unity",
                "scene_state": "loaded",
            },
            {
                "name": "Selected",
                "action": "wait_object",
                "scene": "Assets/B.unity",
                "target_id": "shared",
            },
            {
                "name": "Click",
                "action": "click_ui",
                "scene": "Assets/B.unity",
                "target_id": "shared",
            },
            {"name": "Activate", "action": "set_active_scene", "scene": "Assets/B.unity"},
            {"name": "Unload", "action": "unload_scene", "scene": "Assets/A.unity"},
            {
                "name": "Absent",
                "action": "wait_scene",
                "scene": "Assets/A.unity",
                "scene_state": "unloaded",
            },
        ],
        "cleanup_steps": [
            {"name": "Anchor again", "action": "load_scene", "scene": "Assets/A.unity"}
        ],
    }


def test_scene_definition_roundtrip_omits_absent_fields():
    value = definition()
    model = PlayScenario.model_validate(value)
    wire = model.model_dump(mode="json", exclude_none=True)
    for expected, actual in zip(value["steps"], wire["steps"], strict=True):
        assert actual == {**expected, "timeout_seconds": 30}
    included = model.model_dump(mode="json")
    assert "load_mode" not in included["setup_steps"][0]
    assert "scene_state" not in included["setup_steps"][0]
    native = json.loads(json.dumps(value))
    native["setup_steps"][0].update(load_mode=None, scene_state=None)
    assert PlayScenario.model_validate(normalize_native_definition(native)).model_dump(
        exclude_none=True
    ) == model.model_dump(exclude_none=True)


@pytest.mark.parametrize(
    "change",
    [
        {"load_mode": None},
        {"load_mode": True},
        {"load_mode": "ADD"},
        {"scene_state": "loaded"},
        {"target": "x"},
    ],
)
def test_scene_load_rejects_invalid_fields(change):
    value = definition()
    value["steps"][0].update(change)
    with pytest.raises(ValidationError):
        PlayScenario.model_validate(value)


@pytest.mark.parametrize("action", ["unload_scene", "set_active_scene", "wait_scene"])
@pytest.mark.parametrize(
    "change",
    [
        {"scene": None},
        {"scene": "A"},
        {"scene": "Assets/../A.unity"},
        {"load_mode": "single"},
        {"count": 1},
    ],
)
def test_scene_actions_reject_invalid_scope_and_irrelevant_fields(action, change):
    value = definition()
    value["steps"] = [{"name": "Scope", "action": action, "scene": "Assets/B.unity", **change}]
    with pytest.raises(ValidationError):
        PlayScenario.model_validate(value)


def test_additive_first_anchor_is_rejected_but_legacy_anchor_is_preserved():
    value = definition()
    value["setup_steps"][0]["load_mode"] = "additive"
    with pytest.raises(ValidationError):
        PlayScenario.model_validate(value)
    legacy = {
        "name": "legacy",
        "steps": [
            {"name": "Load", "action": "load_scene", "scene": "Assets/A.unity"},
            {"name": "Object", "action": "wait_object", "target": "Root"},
        ],
    }
    wire = PlayScenario.model_validate(legacy).model_dump(exclude_none=True)
    assert wire["steps"] == [{**step, "timeout_seconds": 30} for step in legacy["steps"]]


@pytest.mark.parametrize("scope", ["active", "all_loaded"])
def test_recording_scope_is_start_only(scope):
    assert (
        ScenarioRecordingCommand(action="start", name="draft", scene_scope=scope).wire_parameters()[
            "scene_scope"
        ]
        == scope
    )
    with pytest.raises(ValidationError):
        ScenarioRecordingCommand(action="status", scene_scope=scope)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["2026-07-28", "legacy"])
@pytest.mark.parametrize("kind", ["scenario", "recording"])
async def test_registered_sdk_preserves_scene_contract(monkeypatch, mode, kind):
    name = "manage_play_scenario" if kind == "scenario" else "manage_scenario_recording"
    module = importlib.import_module("services.tools." + name)
    assert (
        Path(module.__file__).resolve().is_relative_to(Path(__file__).resolve().parents[1] / "src")
    )
    reply = {"success": True, "data": {"owned": "scene-proof"}}
    send = AsyncMock(return_value=reply)
    monkeypatch.setattr(
        module, "get_unity_instance_from_context", AsyncMock(return_value="Owned@scene")
    )
    monkeypatch.setattr(module, "send_with_unity_instance", send)
    server = FastMCP("scene-contract")
    server.tool(getattr(module, name))
    args = (
        {"action": "save", "scenario": definition()}
        if kind == "scenario"
        else {"action": "start", "name": "draft", "scene_scope": "all_loaded"}
    )
    async with Client(server, mode=mode) as client:
        result = await client.call_tool(name, args)
    assert result.structured_content == reply
    send.assert_awaited_once()
    expected = (
        {
            "action": "save",
            "scenario": PlayScenario.model_validate(definition()).model_dump(
                mode="json", exclude_none=True
            ),
        }
        if kind == "scenario"
        else {
            "action": "start",
            "name": "draft",
            "scene_scope": "all_loaded",
            "max_events": 31,
            "duration_seconds": 300,
        }
    )
    assert send.call_args.args[1:] == ("Owned@scene", name, expected)
    assert send.call_args.kwargs == {"retry_on_reload": False}


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["2026-07-28", "legacy"])
async def test_registered_sdk_bad_scene_never_dispatches(monkeypatch, mode):
    module = importlib.import_module("services.tools.manage_play_scenario")
    send = AsyncMock()
    monkeypatch.setattr(module, "send_with_unity_instance", send)
    server = FastMCP("invalid-scene")
    server.tool(module.manage_play_scenario)
    value = definition()
    value["steps"][0]["load_mode"] = True
    async with Client(server, mode=mode) as client:
        result = await client.call_tool(
            "manage_play_scenario", {"action": "save", "scenario": value}, raise_on_error=False
        )
    assert result.is_error
    send.assert_not_awaited()


def test_cli_scene_save_and_recording_scope(monkeypatch, tmp_path):
    value = definition()
    path = tmp_path / "scenes.json"
    path.write_text(json.dumps(value), encoding="utf-8")
    calls = []
    client_type = httpx.AsyncClient

    def respond(request):
        calls.append(json.loads(request.content))
        return httpx.Response(200, json={"success": True})

    monkeypatch.setattr(connection, "read_local_auth_token", lambda host, port: "fixture")
    monkeypatch.setattr(
        connection.httpx,
        "AsyncClient",
        lambda: client_type(transport=httpx.MockTransport(respond), trust_env=False),
    )
    for args in [
        ["play-scenario", "save", str(path)],
        ["scenario-recording", "start", "draft", "--scene-scope", "all_loaded"],
    ]:
        result = CliRunner().invoke(cli, ["--instance", "Owned@scene", *args])
        assert result.exit_code == 0, result.output
    assert calls[0]["params"]["scenario"] == PlayScenario.model_validate(value).model_dump(
        mode="json", exclude_none=True
    )
    assert calls[1]["params"]["scene_scope"] == "all_loaded"


@pytest.mark.parametrize("value", [None, True, 1, "ALL_LOADED"])
def test_recording_scope_rejects_null_bool_number_and_unknown(value):
    with pytest.raises(ValidationError):
        ScenarioRecordingCommand(action="start", name="draft", scene_scope=value)


@pytest.mark.parametrize("state", [None, True, 1, "LOADED", "missing"])
def test_scene_wait_state_requires_exact_non_null_enum(state):
    value = definition()
    value["steps"][1]["scene_state"] = state
    with pytest.raises(ValidationError):
        PlayScenario.model_validate(value)


@pytest.mark.parametrize("scene", [None, True, "Assets/../B.unity"])
def test_object_scene_uses_same_strict_path_policy(scene):
    value = definition()
    value["steps"][2]["scene"] = scene
    with pytest.raises(ValidationError):
        PlayScenario.model_validate(value)


def test_object_scene_accepts_generic_folder_names():
    value = definition()
    value["steps"][2]["scene"] = "Assets/GameData/B.unity"
    assert PlayScenario.model_validate(value).steps[2].scene == "Assets/GameData/B.unity"


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["2026-07-28", "legacy"])
async def test_registered_sdk_null_recording_scope_is_rejected_before_dispatch(monkeypatch, mode):
    module = importlib.import_module("services.tools.manage_scenario_recording")
    send = AsyncMock()
    monkeypatch.setattr(module, "send_with_unity_instance", send)
    server = FastMCP("null-recording-scope")
    server.tool(module.manage_scenario_recording)
    async with Client(server, mode=mode) as client:
        result = await client.call_tool(
            "manage_scenario_recording",
            {"action": "start", "name": "draft", "scene_scope": None},
            raise_on_error=False,
        )
    assert result.is_error
    send.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["2026-07-28", "legacy"])
@pytest.mark.parametrize("action", ["status", "stop", "cancel", "checkpoint"])
@pytest.mark.parametrize("scope", ["active", "all_loaded", None])
async def test_registered_recording_scope_nonstart_is_rejected_by_presence(
    monkeypatch, mode, action, scope
):
    module = importlib.import_module("services.tools.manage_scenario_recording")
    send = AsyncMock(return_value={"success": True})
    monkeypatch.setattr(
        module, "get_unity_instance_from_context", AsyncMock(return_value="Owned@scene")
    )
    monkeypatch.setattr(module, "send_with_unity_instance", send)
    server = FastMCP("irrelevant-scope")
    server.tool(module.manage_scenario_recording)
    args = {"action": action, "scene_scope": scope}
    if action != "status":
        args["recording_id"] = "a" * 32
    if action == "checkpoint":
        args["checkpoint_name"] = "proof"
    async with Client(server, mode=mode) as client:
        result = await client.call_tool("manage_scenario_recording", args, raise_on_error=False)
    assert result.is_error or result.structured_content.get("success") is False
    send.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["2026-07-28", "legacy"])
async def test_registered_recording_omitted_scope_preserves_legacy_nonstart(monkeypatch, mode):
    module = importlib.import_module("services.tools.manage_scenario_recording")
    reply = {"success": True, "data": {"owned": "legacy-status"}}
    send = AsyncMock(return_value=reply)
    monkeypatch.setattr(
        module, "get_unity_instance_from_context", AsyncMock(return_value="Owned@scene")
    )
    monkeypatch.setattr(module, "send_with_unity_instance", send)
    server = FastMCP("omitted-scope")
    server.tool(module.manage_scenario_recording)
    async with Client(server, mode=mode) as client:
        result = await client.call_tool("manage_scenario_recording", {"action": "status"})
    assert result.structured_content == reply
    send.assert_awaited_once()
    assert send.call_args.args[1:] == (
        "Owned@scene",
        "manage_scenario_recording",
        {"action": "status"},
    )
