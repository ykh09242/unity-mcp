"""Bounded event schema and registered SDK/CLI contracts; no live native peer."""

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
from pydantic import ValidationError


def definition(order=False):
    """Fence before the trigger and wait on the same stage's retained sequence."""
    predicate = (
        {"event_order": ["start", "hit", "hit"]}
        if order
        else {"event_name": "hit", "event_count": 2}
    )
    return {
        "name": "event-proof",
        "setup_steps": [{"name": "Load", "action": "load_scene", "scene": "Assets/Menu.unity"}],
        "steps": [
            {
                "name": "Fence",
                "action": "mark_events",
                "event_id": "combat.events",
                "event_mark": "attack",
            },
            {"name": "Attack", "action": "click_ui", "target": "Canvas/Attack"},
            {"name": "Proof", "action": "wait_events", "event_since": "attack", **predicate},
        ],
    }


@pytest.mark.parametrize("order", [False, True])
def test_event_definition_preserves_exact_fields_and_null_omission(order):
    value = definition(order)
    model = PlayScenario.model_validate(value)
    wire = model.model_dump(mode="json", exclude_none=True)
    for original, actual in zip(value["steps"], wire["steps"], strict=True):
        assert actual == {**original, "timeout_seconds": 30}
    included = model.model_dump(mode="json")
    assert "state_compare" not in included["steps"][0]
    assert {key for key in included["steps"][0] if key.startswith("event_")} == {
        "event_id",
        "event_mark",
    }
    native = json.loads(json.dumps(value))
    native["setup_steps"][0]["event_since"] = None
    assert PlayScenario.model_validate(normalize_native_definition(native)).model_dump(
        exclude_none=True
    ) == model.model_dump(exclude_none=True)


@pytest.mark.parametrize(
    "change",
    [
        {"event_count": True},
        {"event_count": 0},
        {"event_count": 129},
        {"event_count": 1.0},
        {"event_count": None},
        {"event_name": ""},
        {"event_name": "a" * 65},
        {"event_name": "space name"},
        {"event_order": ["hit"]},
        {"event_since": None},
        {"stable_for_ms": 1},
        {"state_equals": True},
    ],
)
def test_event_wait_rejects_invalid_or_conflicting_fields(change):
    value = definition()
    value["steps"][-1].update(change)
    with pytest.raises(ValidationError):
        PlayScenario.model_validate(value)


@pytest.mark.parametrize("events", [[], ["a"] * 17, [None], [True], ["a", ""]])
def test_event_order_is_bounded_exact_names(events):
    value = definition(True)
    value["steps"][-1]["event_order"] = events
    with pytest.raises(ValidationError):
        PlayScenario.model_validate(value)


@pytest.mark.parametrize("case", ["forward", "missing", "duplicate", "cross_stage", "too_many"])
def test_marks_require_prior_same_stage_unique_bounded_declarations(case):
    value = definition()
    if case == "forward":
        value["steps"] = [value["steps"][-1], value["steps"][0]]
    elif case == "missing":
        value["steps"][-1]["event_since"] = "absent"
    elif case == "duplicate":
        value["steps"].insert(1, dict(value["steps"][0]))
    elif case == "cross_stage":
        value["setup_steps"].append(value["steps"].pop(0))
    else:
        value["steps"] = [
            {
                "name": f"Fence{i}",
                "action": "mark_events",
                "event_id": "combat.events",
                "event_mark": f"mark{i}",
            }
            for i in range(17)
        ]
    with pytest.raises(ValidationError):
        PlayScenario.model_validate(value)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["2026-07-28", "legacy"])
@pytest.mark.parametrize("order", [False, True])
async def test_registered_sdk_dispatches_event_definition_exactly_once(monkeypatch, mode, order):
    module = importlib.import_module("services.tools.manage_play_scenario")
    assert (
        Path(module.__file__).resolve()
        == Path(__file__).resolve().parents[1] / "src/services/tools/manage_play_scenario.py"
    )
    reply = {"success": True, "data": {"name": "event-proof"}}
    send = AsyncMock(return_value=reply)
    monkeypatch.setattr(
        module, "get_unity_instance_from_context", AsyncMock(return_value="Owned@fixture")
    )
    monkeypatch.setattr(module, "send_with_unity_instance", send)
    server = FastMCP("event-contract")
    server.tool(module.manage_play_scenario)
    schema = (await server.list_tools(run_middleware=False))[0].parameters["$defs"]["ScenarioStep"]
    assert {"mark_events", "wait_events"} <= set(schema["properties"]["action"]["enum"])
    assert schema["additionalProperties"] is False
    async with Client(server, mode=mode) as client:
        result = await client.call_tool(
            "manage_play_scenario", {"action": "save", "scenario": definition(order)}
        )
    assert result.structured_content == reply
    send.assert_awaited_once()
    assert send.call_args.args[1:] == (
        "Owned@fixture",
        "manage_play_scenario",
        {
            "action": "save",
            "scenario": PlayScenario.model_validate(definition(order)).model_dump(
                mode="json", exclude_none=True
            ),
        },
    )
    assert send.call_args.kwargs == {"retry_on_reload": False}


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["2026-07-28", "legacy"])
@pytest.mark.parametrize("invalid", ["bool", "conflict", "forward", "cross_stage"])
async def test_registered_sdk_rejects_conflict_before_dispatch(monkeypatch, mode, invalid):
    module = importlib.import_module("services.tools.manage_play_scenario")
    send = AsyncMock()
    monkeypatch.setattr(module, "send_with_unity_instance", send)
    server = FastMCP("event-contract-invalid")
    server.tool(module.manage_play_scenario)
    value = definition()
    if invalid == "bool":
        value["steps"][-1]["event_count"] = True
    elif invalid == "conflict":
        value["steps"][-1]["event_order"] = ["hit"]
    elif invalid == "forward":
        value["steps"] = [value["steps"][-1], value["steps"][0]]
    else:
        value["setup_steps"].append(value["steps"].pop(0))
    async with Client(server, mode=mode) as client:
        result = await client.call_tool(
            "manage_play_scenario", {"action": "save", "scenario": value}, raise_on_error=False
        )
    assert result.is_error
    send.assert_not_awaited()


@pytest.mark.parametrize("order", [False, True])
def test_cli_file_retains_count_or_order_definition(monkeypatch, tmp_path, order):
    path = tmp_path / "events.json"
    value = definition(order)
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
    result = CliRunner().invoke(
        cli, ["--instance", "Owned@fixture", "play-scenario", "save", str(path)]
    )
    assert result.exit_code == 0, result.output
    assert calls == [
        {
            "type": "manage_play_scenario",
            "params": {
                "action": "save",
                "scenario": PlayScenario.model_validate(value).model_dump(
                    mode="json", exclude_none=True
                ),
            },
            "unity_instance": "Owned@fixture",
        }
    ]


def test_cli_invalid_event_definition_never_opens_http(monkeypatch, tmp_path):
    value = definition()
    value["steps"][-1]["event_count"] = True
    path = tmp_path / "invalid.json"
    path.write_text(json.dumps(value), encoding="utf-8")

    def forbidden(*_args, **_kwargs):
        pytest.fail("Invalid event input must be rejected before a connection is opened")

    monkeypatch.setattr(connection.httpx, "AsyncClient", forbidden)
    result = CliRunner().invoke(cli, ["play-scenario", "save", str(path)])
    assert result.exit_code != 0
