"""Capture CLI preserves explicit target options for native validation."""

import copy
import json
from types import SimpleNamespace

import httpx
import pytest
from click.testing import CliRunner

from cli.main import cli
from cli.utils import connection


def inline(coroutine):
    """Run only immediately completed in-memory requests, without an event loop."""
    try:
        coroutine.send(None)
    except StopIteration as complete:
        return complete.value
    coroutine.close()
    raise AssertionError("Test coroutine unexpectedly suspended")


@pytest.fixture
def camera_http(monkeypatch):
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


@pytest.mark.parametrize("wrapped", [False, True])
@pytest.mark.parametrize("follow", [None, "", "Player"])
@pytest.mark.parametrize("look_at", [None, "", "Player"])
def test_set_target_retains_explicit_empty_options_and_native_diagnostics(
    camera_http, wrapped, follow, look_at
):
    raw, requests = camera_http
    expected_response = {
        "success": False,
        "error": "Native target rejected",
        "code": "native_failure",
        "data": {"modified": False, "count": 0, "reference": None, "partial": []},
    }
    raw.update(
        copy.deepcopy(
            {"status": "success", "result": expected_response} if wrapped else expected_response
        )
    )
    args = ["--format", "json", "camera", "set-target", "Owned"]
    properties = {}
    for option, key, value in (("--follow", "follow", follow), ("--look-at", "lookAt", look_at)):
        if value is not None:
            args += [option, value]
            properties[key] = value
    expected_params = {"action": "set_target", "target": "Owned"}
    if properties:
        expected_params["properties"] = properties

    result = CliRunner().invoke(cli, args)

    assert result.exit_code == 1, (result.output, result.exception)
    assert json.loads(result.stdout) == expected_response
    assert len(requests) == 1
    assert requests[0]["type"] == "manage_camera"
    # Unity owns reference resolution: an explicit empty reference must reach its
    # validation instead of disappearing beside a valid reference update.
    assert requests[0]["params"] == expected_params
