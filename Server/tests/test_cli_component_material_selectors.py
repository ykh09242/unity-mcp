"""Exercise component selectors and renderer slots through Click and HTTP decoding."""

import json

import httpx
import pytest
from click.testing import CliRunner

from cli.main import cli
from cli.utils import connection


@pytest.fixture
def wire(monkeypatch):
    requests = []
    response = {"success": True, "data": {}}
    client_type = httpx.AsyncClient

    def respond(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json=response)

    monkeypatch.setattr(
        connection.httpx,
        "AsyncClient",
        lambda **_options: client_type(transport=httpx.MockTransport(respond)),
    )
    monkeypatch.setattr(connection, "_auth_headers", lambda config: {})
    return requests, response


COMPONENT_COMMANDS = [
    ["remove", "Cube", "BoxCollider", "--force"],
    ["set", "Cube", "BoxCollider", "isTrigger", "false"],
    ["modify", "Cube", "BoxCollider", "--properties", '{"size":[0,2,3]}'],
]


@pytest.mark.parametrize("command", COMPONENT_COMMANDS)
@pytest.mark.parametrize(
    ("options", "selection"),
    [
        (["--strict-selection"], {"strictSelection": True}),
        (["--component-instance-id", "-2147483648"], {"componentInstanceID": -2147483648}),
        (["--component-instance-id", "2147483647"], {"componentInstanceID": 2147483647}),
        (
            ["--strict-selection", "--component-index", "0"],
            {"strictSelection": True, "componentIndex": 0},
        ),
    ],
)
def test_component_selection_uses_selected_native_contract(wire, command, options, selection):
    # Given a successful native endpoint and an explicit selector.
    requests, _ = wire
    # When the public CLI invokes the component command.
    result = CliRunner().invoke(cli, ["--format", "json", "component", *command, *options])
    # Then the native command receives exact selector types and values.
    assert result.exit_code == 0, result.output
    assert len(requests) == 1
    assert requests[0]["type"] == "manage_components_selected"
    for key, value in selection.items():
        assert requests[0]["params"][key] == value
        assert type(requests[0]["params"][key]) is type(value)


@pytest.mark.parametrize("command", COMPONENT_COMMANDS)
@pytest.mark.parametrize("options", [[], ["--component-index", "0"]])
def test_component_omitted_selection_keeps_legacy_route(wire, command, options):
    # Given no strict or identity selection.
    requests, _ = wire
    # When a legacy command executes.
    result = CliRunner().invoke(cli, ["component", *command, *options])
    # Then the first-match route and optional index remain unchanged.
    assert result.exit_code == 0, result.output
    assert requests[0]["type"] == "manage_components"
    assert "strictSelection" not in requests[0]["params"]
    assert "componentInstanceID" not in requests[0]["params"]


@pytest.mark.parametrize("command", COMPONENT_COMMANDS)
@pytest.mark.parametrize(
    "options",
    [
        ["--component-instance-id", "0"],
        ["--component-instance-id", "2147483648"],
        ["--component-instance-id", "-2147483649"],
        ["--component-instance-id", "1.5"],
        ["--component-instance-id", "false"],
        ["--component-instance-id", "1", "--component-index", "0"],
        ["--strict-selection", "--component-index", "-1"],
        ["--strict-selection", "--component-index", "2147483648"],
    ],
)
def test_invalid_component_selector_is_rejected_before_dispatch(wire, command, options):
    # Given an invalid or conflicting selector.
    requests, _ = wire
    # When Click admits the command.
    result = CliRunner().invoke(cli, ["component", *command, *options])
    # Then validation fails locally without sending a mutation.
    assert result.exit_code == 2, result.output
    assert requests == []


@pytest.mark.parametrize("command", COMPONENT_COMMANDS)
def test_component_native_selection_failure_stays_opaque(wire, command):
    # Given structured native ambiguity details.
    requests, response = wire
    failure = {
        "success": False,
        "error": "ambiguous_component",
        "data": {"candidates": [{"instanceID": -47}, {"instanceID": 89}]},
    }
    response.clear()
    response.update({"status": "success", "result": failure})
    # When strict selection fails natively.
    result = CliRunner().invoke(
        cli, ["--format", "json", "component", *command, "--strict-selection"]
    )
    # Then the process fails and preserves the complete native response.
    assert result.exit_code == 1, result.output
    assert json.loads(result.stdout) == failure
    assert len(requests) == 1


@pytest.mark.parametrize(("options", "slot"), [([], 0), (["--slot", "3"], 3)])
def test_renderer_color_sends_nonnegative_slot(wire, options, slot):
    # Given a selected renderer slot or its default.
    requests, _ = wire
    # When the renderer color command executes.
    result = CliRunner().invoke(
        cli, ["material", "set-renderer-color", "Cube", "1", "0", "0", *options]
    )
    # Then the material command receives an integer slot.
    assert result.exit_code == 0, result.output
    assert requests[0]["type"] == "manage_material"
    assert requests[0]["params"]["slot"] == slot
    assert type(requests[0]["params"]["slot"]) is int


@pytest.mark.parametrize("slot", ["-1", "1.5", "false"])
def test_renderer_color_rejects_invalid_slot_before_dispatch(wire, slot):
    # Given a slot outside the accepted CLI domain.
    requests, _ = wire
    # When the command is parsed.
    result = CliRunner().invoke(
        cli, ["material", "set-renderer-color", "Cube", "1", "0", "0", "--slot", slot]
    )
    # Then no native mutation is sent.
    assert result.exit_code == 2, result.output
    assert requests == []
