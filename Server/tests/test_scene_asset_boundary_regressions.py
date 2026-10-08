"""Scene validate CLI retains native payloads when optional summary shape differs."""

import copy
import json
from types import SimpleNamespace

import httpx
import pytest
from click.testing import CliRunner

from cli.main import cli
from cli.utils import connection
from cli.utils.output import format_output


# Current Unity ValidateScene produces dictionary data and integer counts.
# These other shapes exercise the shared transport's permissive response contract.
UNSUMMARIZED = [
    None,
    False,
    0,
    "",
    [],
    {},
    {"totalIssues": None, "repaired": 0},
    {"totalIssues": 1, "repaired": None},
    {"totalIssues": False, "repaired": 0},
    {"totalIssues": 0, "repaired": False},
    {"totalIssues": "0", "repaired": 0},
    {"totalIssues": 1, "repaired": []},
]


def inline(coroutine):
    """Reject suspension and avoid any event loop or real socket."""
    try:
        coroutine.send(None)
    except StopIteration as complete:
        return complete.value
    coroutine.close()
    raise AssertionError("Test coroutine unexpectedly suspended")


@pytest.fixture
def scene_http(monkeypatch):
    raw, requests = {}, []
    client_type = httpx.AsyncClient

    def respond(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json=raw)

    monkeypatch.setattr(
        connection.httpx, "AsyncClient", lambda: client_type(transport=httpx.MockTransport(respond))
    )
    monkeypatch.setattr(connection, "asyncio", SimpleNamespace(run=inline))
    monkeypatch.setattr(connection, "_auth_headers", lambda _config: {})
    return raw, requests


def set_response(raw, expected, wrapped):
    raw.clear()
    raw.update(copy.deepcopy({"status": "success", "result": expected} if wrapped else expected))


@pytest.mark.parametrize("wrapped", [False, True])
@pytest.mark.parametrize("data", UNSUMMARIZED)
def test_validate_text_keeps_response_without_inventing_summary(scene_http, wrapped, data):
    raw, requests = scene_http
    expected = {"success": True, "message": "Native result", "data": data}
    set_response(raw, expected, wrapped)
    result = CliRunner().invoke(cli, ["--format", "text", "scene", "validate"])
    assert result.exit_code == 0, (result.output, result.exception)
    assert result.stdout == format_output(expected, "text") + "\n"
    assert len(requests) == 1
    assert requests[0]["params"] == {"action": "validate"}


@pytest.mark.parametrize("wrapped", [False, True])
@pytest.mark.parametrize("data", UNSUMMARIZED)
def test_validate_json_keeps_null_false_zero_containers_and_count_shapes(scene_http, wrapped, data):
    raw, requests = scene_http
    expected = {"success": True, "message": "Native result", "data": data}
    set_response(raw, expected, wrapped)
    result = CliRunner().invoke(cli, ["--format", "json", "scene", "validate"])
    assert result.exit_code == 0, (result.output, result.exception)
    assert json.loads(result.stdout) == expected
    assert len(requests) == 1


@pytest.mark.parametrize("wrapped", [False, True])
@pytest.mark.parametrize(
    "data,notice",
    [
        ({"totalIssues": 0, "repaired": 0}, "Scene is clean"),
        ({"totalIssues": 2, "repaired": 0}, "Found 2 issue(s), none repaired"),
        ({"totalIssues": 2, "repaired": 1}, "Found 2 issue(s), repaired 1"),
    ],
)
def test_validate_documented_native_count_summaries_remain_readable(
    scene_http, wrapped, data, notice
):
    raw, requests = scene_http
    set_response(raw, {"success": True, "data": data}, wrapped)
    result = CliRunner().invoke(cli, ["--format", "text", "scene", "validate", "--repair"])
    assert result.exit_code == 0, (result.output, result.exception)
    assert notice in result.stdout
    assert requests[0]["params"] == {"action": "validate", "autoRepair": True}


@pytest.mark.parametrize("wrapped", [False, True])
def test_validate_native_failure_retains_partial_json_and_nonzero_exit(scene_http, wrapped):
    raw, requests = scene_http
    expected = {
        "success": False,
        "error": "Native validation rejected",
        "code": "native_failure",
        "data": {"totalIssues": 0, "repaired": False, "reference": None, "partial": []},
    }
    set_response(raw, expected, wrapped)
    result = CliRunner().invoke(cli, ["--format", "json", "scene", "validate"])
    assert result.exit_code == 1, (result.output, result.exception)
    assert json.loads(result.stdout) == expected
    assert len(requests) == 1
