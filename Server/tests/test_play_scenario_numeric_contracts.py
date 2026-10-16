"""Numeric predicate schema and actual SDK/CLI admission contracts."""

import importlib
import json
from unittest.mock import AsyncMock

import httpx
import pytest
from cli.main import cli
from cli.utils import connection
from click.testing import CliRunner
from fastmcp import Client, FastMCP
from models.play_scenarios import PlayScenario, ScenarioStep, normalize_native_definition
from pydantic import ValidationError

PREDICATES = [
    {"operator": "gte", "value": 9007199254740993},
    {"operator": "lte", "value": -(2**63)},
    {"operator": "range", "min": 2**63 - 1, "max": float(2**63)},
    {"operator": "approximately", "value": 5e-324, "relative_tolerance": 0.5},
    {"operator": "approximately", "value": 0.1, "absolute_tolerance": 0},
    {"operator": "approximately", "value": 1, "absolute_tolerance": 2, "relative_tolerance": 3},
]


def step_for(predicate, kind="state"):
    """Use canonical existing actions, changing only their comparison condition."""
    if kind == "state":
        return {
            "name": "Ready",
            "action": "wait_state",
            "state_id": "state.ready",
            "state_compare": predicate,
        }
    return {
        "name": "Ready",
        "action": "wait_object",
        "target": "Root",
        "component": "UnityEngine.Transform",
        "property": {"path": "m_LocalPosition.x", "compare": predicate},
    }


def definition(step):
    """Whole definitions retain the required initial scene boundary."""
    return {
        "name": "numeric-contract",
        "steps": [{"name": "Load", "action": "load_scene", "scene": "Assets/Menu.unity"}, step],
    }


@pytest.mark.parametrize("kind", ["state", "property"])
@pytest.mark.parametrize("predicate", PREDICATES)
def test_predicates_preserve_exact_operands_and_omitted_fields(kind, predicate):
    model = ScenarioStep.model_validate(step_for(predicate, kind))
    wire = model.model_dump(mode="json", exclude_none=True)
    actual = wire["state_compare"] if kind == "state" else wire["property"]["compare"]
    assert actual == predicate
    for field, value in predicate.items():
        assert type(actual[field]) is type(value)
    # New fields must remain absent even in ordinary serialization with null inclusion.
    included = model.model_dump(mode="json")
    if kind == "state":
        assert included["state_compare"] == predicate
    else:
        assert "equals" not in included["property"]
        assert included["property"]["compare"] == predicate


BAD_NUMBERS = [
    True,
    None,
    "1",
    [],
    {},
    float("nan"),
    float("inf"),
    -float("inf"),
    2**63,
    -(2**63) - 1,
]
BAD_PREDICATES = [
    *({"operator": "gte", "value": value} for value in BAD_NUMBERS),
    *(
        {"operator": "approximately", "value": 1, "absolute_tolerance": value}
        for value in [*BAD_NUMBERS, -1]
    ),
    {"operator": "gte"},
    {"operator": "gte", "value": 1, "min": None},
    {"operator": "range", "min": 9007199254740993, "max": 9007199254740992.0},
    {"operator": "range", "min": 0, "max": 1, "value": None},
    {"operator": "approximately", "value": 1},
    {"operator": "approximately", "value": 1, "relative_tolerance": 0, "max": None},
    {"operator": ">=", "value": 1},
    {"operator": "gte", "value": 1, "unknown": 0},
]


@pytest.mark.parametrize("kind", ["state", "property"])
@pytest.mark.parametrize("predicate", BAD_PREDICATES)
def test_invalid_numeric_contracts_are_rejected(kind, predicate):
    with pytest.raises(ValidationError):
        ScenarioStep.model_validate(step_for(predicate, kind))


@pytest.mark.parametrize("kind", ["state", "property"])
@pytest.mark.parametrize("equals", [None, 0, False])
def test_equality_and_comparison_conflict_by_presence(kind, equals):
    step = step_for(PREDICATES[0], kind)
    if kind == "state":
        step["state_equals"] = equals
    else:
        step["property"]["equals"] = equals
    with pytest.raises(ValidationError):
        ScenarioStep.model_validate(step)


@pytest.mark.parametrize("kind", ["state", "property"])
def test_explicit_null_comparison_is_rejected(kind):
    with pytest.raises(ValidationError):
        ScenarioStep.model_validate(step_for(None, kind))


def test_state_comparison_is_not_an_effect_step_field():
    with pytest.raises(ValidationError):
        ScenarioStep.model_validate(
            {
                "name": "Load",
                "action": "load_scene",
                "scene": "Assets/Menu.unity",
                "state_compare": PREDICATES[0],
            }
        )


@pytest.mark.parametrize("scalar", [False, 0, -(2**63), 2**63 - 1, 0.1, "ready"])
@pytest.mark.parametrize("kind", ["state", "property"])
def test_legacy_scalar_equality_is_unchanged(kind, scalar):
    step = step_for(PREDICATES[0], kind)
    if kind == "state":
        del step["state_compare"]
        step["state_equals"] = scalar
    else:
        step["property"] = {"path": "m_LocalPosition.x", "equals": scalar}
    wire = ScenarioStep.model_validate(step).model_dump(mode="json", exclude_none=True)
    actual = wire["state_equals"] if kind == "state" else wire["property"]["equals"]
    assert actual == scalar
    assert type(actual) is type(scalar)
    assert "state_compare" not in wire
    if kind == "property":
        assert "compare" not in wire["property"]


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["2026-07-28", "legacy"])
@pytest.mark.parametrize("kind", ["state", "property"])
@pytest.mark.parametrize("predicate", PREDICATES)
async def test_registered_sdk_preserves_numeric_native_wire(monkeypatch, mode, kind, predicate):
    module = importlib.import_module("services.tools.manage_play_scenario")
    reply = {"success": True, "data": {"name": "numeric-contract"}}
    send = AsyncMock(return_value=reply)
    monkeypatch.setattr(
        module, "get_unity_instance_from_context", AsyncMock(return_value="Selected@owned")
    )
    monkeypatch.setattr(module, "send_with_unity_instance", send)
    server = FastMCP("numeric-contract")
    server.tool(module.manage_play_scenario)
    registered = (await server.list_tools(run_middleware=False))[0]
    assert "state_compare" in registered.parameters["$defs"]["ScenarioStep"]["properties"]
    async with Client(server, mode=mode) as client:
        result = await client.call_tool(
            "manage_play_scenario",
            {"action": "save", "scenario": definition(step_for(predicate, kind))},
        )
    assert json.loads(result.content[0].text) == reply
    send.assert_awaited_once()
    assert send.call_args.args[1:3] == ("Selected@owned", "manage_play_scenario")
    assert send.call_args.kwargs == {"retry_on_reload": False}
    actual = send.call_args.args[3]["scenario"]["steps"][1]
    expected = ScenarioStep.model_validate(step_for(predicate, kind)).model_dump(
        mode="json", exclude_none=True
    )
    assert actual == expected
    observed = actual["state_compare"] if kind == "state" else actual["property"]["compare"]
    assert observed == predicate
    assert {key: type(value) for key, value in observed.items()} == {
        key: type(value) for key, value in predicate.items()
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["2026-07-28", "legacy"])
async def test_registered_sdk_rejects_invalid_predicate_before_dispatch(monkeypatch, mode):
    module = importlib.import_module("services.tools.manage_play_scenario")
    send = AsyncMock()
    monkeypatch.setattr(module, "send_with_unity_instance", send)
    server = FastMCP("numeric-contract-invalid")
    server.tool(module.manage_play_scenario)
    async with Client(server, mode=mode) as client:
        result = await client.call_tool(
            "manage_play_scenario",
            {
                "action": "save",
                "scenario": definition(step_for({"operator": "gte", "value": True})),
            },
            raise_on_error=False,
        )
    assert result.is_error
    send.assert_not_awaited()


@pytest.mark.parametrize("kind", ["state", "property"])
@pytest.mark.parametrize("predicate", PREDICATES)
def test_cli_file_and_native_normalization_preserve_predicate(
    monkeypatch, tmp_path, kind, predicate
):
    authored = definition(step_for(predicate, kind))
    native = json.loads(json.dumps(authored))
    native["steps"][0]["state_compare"] = None
    normalized = PlayScenario.model_validate(normalize_native_definition(native))
    assert normalized.model_dump(mode="json", exclude_none=True) == PlayScenario.model_validate(
        authored
    ).model_dump(mode="json", exclude_none=True)
    path = tmp_path / "scenario.json"
    path.write_text(json.dumps(authored), encoding="utf-8")
    calls = []
    reply = {"success": True, "data": {"name": "numeric-contract"}}
    client_type = httpx.AsyncClient

    def respond(request):
        assert request.url.path == "/api/command"
        calls.append(json.loads(request.content))
        return httpx.Response(200, json=reply)

    monkeypatch.setattr(connection, "read_local_auth_token", lambda host, port: "fixture")
    monkeypatch.setattr(
        connection.httpx,
        "AsyncClient",
        lambda: client_type(transport=httpx.MockTransport(respond), trust_env=False),
    )
    result = CliRunner().invoke(
        cli,
        ["--instance", "Selected@owned", "--format", "json", "play-scenario", "save", str(path)],
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == reply
    assert len(calls) == 1
    assert calls[0] == {
        "type": "manage_play_scenario",
        "params": {
            "action": "save",
            "scenario": normalized.model_dump(mode="json", exclude_none=True),
        },
        "unity_instance": "Selected@owned",
    }


@pytest.mark.parametrize("field", ["min", "max", "relative_tolerance"])
@pytest.mark.parametrize("value", BAD_NUMBERS)
def test_all_numeric_operand_positions_reject_coercion(field, value):
    predicate = (
        {"operator": "range", "min": 0, "max": 1}
        if field in ("min", "max")
        else {"operator": "approximately", "value": 1, "relative_tolerance": 0}
    )
    predicate[field] = value
    with pytest.raises(ValidationError):
        ScenarioStep.model_validate(step_for(predicate))


def test_cli_rejects_invalid_numeric_file_before_connection(monkeypatch, tmp_path):
    path = tmp_path / "invalid.json"
    path.write_text(
        json.dumps(definition(step_for({"operator": "gte", "value": True}))), encoding="utf-8"
    )

    def forbidden(*_args, **_kwargs):
        pytest.fail("Invalid authored numeric input must not open a connection")

    monkeypatch.setattr(connection.httpx, "AsyncClient", forbidden)
    result = CliRunner().invoke(cli, ["play-scenario", "save", str(path)])
    assert result.exit_code != 0
    assert "Numeric operands" in result.output
