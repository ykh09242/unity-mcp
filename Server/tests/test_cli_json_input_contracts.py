"""JSON compatibility repairs must preserve values delivered by the public CLI."""

import json

import httpx
import pytest
from click.testing import CliRunner

from cli.main import cli
from cli.utils import connection


@pytest.fixture
def command_requests(monkeypatch):
    requests = []
    client_type = httpx.AsyncClient

    def respond(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json={"success": True, "data": {}})

    monkeypatch.setattr(
        connection.httpx,
        "AsyncClient",
        lambda **_options: client_type(transport=httpx.MockTransport(respond), trust_env=False),
    )
    monkeypatch.setattr(connection, "_auth_headers", lambda config: {})
    for key in ("HOST", "HTTP_PORT", "TIMEOUT", "FORMAT", "INSTANCE"):
        monkeypatch.delenv(f"UNITY_MCP_{key}", raising=False)
    return requests


def invoke_payload(consumer, payload):
    if consumer == "batch":
        command = [
            "batch",
            "inline",
            '[{"tool":"owned_tool","params":' + payload + "}]",
        ]
    else:
        command = ["component", "modify", "Owned", "OwnedComponent", "--properties", payload]
    return CliRunner().invoke(cli, ["--format", "json", "--instance", "Owned@fixture", *command])


@pytest.mark.parametrize("consumer", ["batch", "component"])
@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        (
            '{"TrueKey":"TrueNorth Falsehood","enabled":True}',
            {"TrueKey": "TrueNorth Falsehood", "enabled": True},
        ),
        (
            "{'FalseKey':'Falsehood TrueNorth','enabled':false,'empty':null}",
            {"FalseKey": "Falsehood TrueNorth", "enabled": False, "empty": None},
        ),
        (
            """{"name":"O'Brien TrueNorth","enabled":False}""",
            {"name": "O'Brien TrueNorth", "enabled": False},
        ),
        (
            r"""{'name':'O\'Brien "TrueNorth"','enabled':True}""",
            {"name": 'O\'Brien "TrueNorth"', "enabled": True},
        ),
        (
            r"""{"path":"C:\\TrueFolder\\FalseFile","quote":"\"True\"","enabled":False}""",
            {"path": r"C:\TrueFolder\FalseFile", "quote": '"True"', "enabled": False},
        ),
        (
            r"""{'path':'C:\\TrueFolder\\','quote':'\"False\"','enabled':True}""",
            {"path": "C:\\TrueFolder\\", "quote": '"False"', "enabled": True},
        ),
        (
            r"""{'nested':[True,False,true,false,null,{"text":"False\n\uD55C\uAE00"}]}""",
            {"nested": [True, False, True, False, None, {"text": "False\n\ud55c\uae00"}]},
        ),
        (
            '{"name":"O\'Brien TrueNorth","enabled":true}',
            {"name": "O'Brien TrueNorth", "enabled": True},
        ),
    ],
)
def test_compatible_json_preserves_wire_values(command_requests, consumer, payload, expected):
    result = invoke_payload(consumer, payload)
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == {"success": True, "data": {}}
    assert len(command_requests) == 1
    request = command_requests[0]
    assert request["unity_instance"] == "Owned@fixture"
    if consumer == "batch":
        assert request["type"] == "batch_execute"
        assert request["params"]["commands"] == [{"tool": "owned_tool", "params": expected}]
    else:
        assert request["type"] == "manage_components"
        assert request["params"]["properties"] == expected


@pytest.mark.parametrize("consumer", ["batch", "component"])
@pytest.mark.parametrize(
    "payload",
    [
        "{'value': TrueFalse}",
        "{'value': Falsehood}",
        "{'value': None}",
        "{'value': (1, 2)}",
        "{'value': {1, 2}}",
        "{'value': 1 + 2}",
        "{'value': missing_call()}",
        r"{'value': '\x41'}",
        r"{'value': '\q'}",
        "{'value': 'unterminated}",
    ],
)
def test_invalid_compatibility_input_never_dispatches(command_requests, consumer, payload):
    result = invoke_payload(consumer, payload)
    assert result.exit_code == 1, result.output
    assert "Invalid JSON" in json.loads(result.stdout)["error"]
    assert result.stderr == ""
    assert command_requests == []


@pytest.mark.parametrize("payload", ["[]", '["value"]', "null", "true", "false", "0", '"value"'])
def test_raw_rejects_non_object_parameters_before_dispatch(command_requests, payload):
    result = CliRunner().invoke(
        cli,
        [
            "--host",
            "127.0.0.1",
            "--port",
            "8080",
            "--timeout",
            "30",
            "--instance",
            "Owned@fixture",
            "--format",
            "json",
            "raw",
            "owned_tool",
            payload,
        ],
    )
    assert result.exit_code == 1, result.output
    error = json.loads(result.stdout)
    assert error["success"] is False
    assert error["code"] == "invalid_json_params"
    assert "expected an object" in error["error"]
    assert result.stderr == ""
    assert command_requests == []


@pytest.mark.parametrize("payload", [[], ["{}"], ['{"enabled":false,"count":0,"items":[]}']])
def test_raw_object_parameters_preserve_wire_values(command_requests, payload):
    result = CliRunner().invoke(
        cli,
        [
            "--host",
            "127.0.0.1",
            "--port",
            "8080",
            "--timeout",
            "30",
            "--instance",
            "Owned@fixture",
            "--format",
            "json",
            "raw",
            "owned_tool",
            *payload,
        ],
    )
    assert result.exit_code == 0, (result.exception, result.output)
    assert result.stderr == ""
    assert json.loads(result.stdout) == {"success": True, "data": {}}
    assert command_requests == [
        {
            "type": "owned_tool",
            "params": json.loads(payload[0]) if payload else {},
            "unity_instance": "Owned@fixture",
        }
    ]


@pytest.mark.parametrize("consumer", ["raw", "property", "component", "batch"])
@pytest.mark.parametrize("number", ["NaN", "Infinity", "-Infinity", "1e999", "-1e999"])
@pytest.mark.parametrize("nested", [False, True])
def test_nonfinite_inline_input_is_an_input_error_before_network(
    command_requests, consumer, number, nested
):
    value = '{"nested":[' + number + "]}" if nested else number
    payload = '{"value":' + value + "}"
    command = {
        "raw": ["raw", "owned_tool", payload],
        "property": ["component", "set", "Owned", "OwnedComponent", "value", "--", value],
        "component": ["component", "modify", "Owned", "OwnedComponent", "--properties", payload],
        "batch": ["batch", "inline", '[{"tool":"owned_tool","params":' + payload + "}]"],
    }[consumer]
    result = CliRunner().invoke(cli, ["--format", "json", *command])

    assert result.exit_code == 1, result.output
    error = json.loads(result.stdout)
    assert error["success"] is False
    assert error["code"] == ("invalid_json_params" if consumer == "raw" else "invalid_json_input")
    assert "finite" in error["error"].lower()
    assert result.stderr == ""
    assert "connection" not in result.output.lower()
    assert command_requests == []


@pytest.mark.parametrize("payload", ["nan", "inf", "+Infinity", "+1e999"])
def test_nonfinite_float_fallback_is_an_input_error(command_requests, payload):
    result = CliRunner().invoke(
        cli,
        ["--format", "json", "component", "set", "Owned", "OwnedComponent", "value", payload],
    )
    assert result.exit_code == 1, result.output
    error = json.loads(result.stdout)
    assert error["code"] == "invalid_json_input"
    assert "finite" in error["error"].lower()
    assert result.stderr == ""
    assert command_requests == []


@pytest.mark.parametrize(
    "payload,expected",
    [('"NaN"', "NaN"), ("ordinary text", "ordinary text"), ("1,2,3", "1,2,3"), ("3.14", 3.14)],
)
def test_property_fallback_preserves_strings_and_finite_numbers(
    command_requests, payload, expected
):
    result = CliRunner().invoke(
        cli,
        ["--format", "json", "component", "set", "Owned", "OwnedComponent", "value", payload],
    )
    assert result.exit_code == 0, result.output
    assert command_requests[0]["params"]["value"] == expected
