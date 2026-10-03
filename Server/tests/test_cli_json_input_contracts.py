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
        lambda: client_type(transport=httpx.MockTransport(respond), trust_env=False),
    )
    monkeypatch.setattr(connection, "_auth_headers", lambda config: {})
    for key in ("HOST", "HTTP_PORT", "TIMEOUT", "FORMAT", "INSTANCE"):
        monkeypatch.delenv(f"UNITY_MCP_{key}", raising=False)
    return requests


def invoke_payload(consumer, payload):
    if consumer == "batch":
        command = [
            "batch", "inline",
            '[{"tool":"owned_tool","params":' + payload + "}]",
        ]
    else:
        command = ["component", "modify", "Owned", "OwnedComponent", "--properties", payload]
    return CliRunner().invoke(
        cli, ["--format", "json", "--instance", "Owned@fixture", *command]
    )


@pytest.mark.parametrize("consumer", ["batch", "component"])
@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ('{"TrueKey":"TrueNorth Falsehood","enabled":True}',
         {"TrueKey": "TrueNorth Falsehood", "enabled": True}),
        ("{'FalseKey':'Falsehood TrueNorth','enabled':false,'empty':null}",
         {"FalseKey": "Falsehood TrueNorth", "enabled": False, "empty": None}),
        ("""{"name":"O'Brien TrueNorth","enabled":False}""",
         {"name": "O'Brien TrueNorth", "enabled": False}),
        (r"""{'name':'O\'Brien "TrueNorth"','enabled':True}""",
         {"name": 'O\'Brien "TrueNorth"', "enabled": True}),
        (r"""{"path":"C:\\TrueFolder\\FalseFile","quote":"\"True\"","enabled":False}""",
         {"path": r"C:\TrueFolder\FalseFile", "quote": '"True"', "enabled": False}),
        (r"""{'path':'C:\\TrueFolder\\','quote':'\"False\"','enabled':True}""",
         {"path": "C:\\TrueFolder\\", "quote": '"False"', "enabled": True}),
        (r"""{'nested':[True,False,true,false,null,{"text":"False\n\uD55C\uAE00"}]}""",
         {"nested": [True, False, True, False, None, {"text": "False\n한글"}]}),
        ('{"name":"O\'Brien TrueNorth","enabled":true}',
         {"name": "O'Brien TrueNorth", "enabled": True}),
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
    assert "Invalid JSON" in result.stderr
    assert command_requests == []
