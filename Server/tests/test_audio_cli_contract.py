"""Audio CLI contracts through the real HTTP decoder and command error handler."""

import json

import httpx
import pytest
from click.testing import CliRunner

from cli.main import cli
from cli.utils import connection


@pytest.fixture
def audio_transport(monkeypatch):
    requests = []
    response = {"success": True, "data": {"isPlaying": False}}
    client_type = httpx.AsyncClient

    def respond(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json=response)

    monkeypatch.setattr(connection.httpx, "AsyncClient", lambda: client_type(transport=httpx.MockTransport(respond)))
    monkeypatch.setattr(connection, "_auth_headers", lambda config: {})
    return response, requests


@pytest.mark.parametrize("action", ["play", "stop"])
@pytest.mark.parametrize("selector,target", [(None, "Player"), ("by_id", "-42"), ("by_name", "42"), ("by_path", "/Root/Speaker")])
def test_audio_actions_use_native_tool(audio_transport, action, selector, target):
    response, requests = audio_transport
    args = ["--instance", "Project@audio", "--format", "json", "audio", action]
    if selector:
        args += ["--search-method", selector]
    result = CliRunner().invoke(cli, [*args, "--", target])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == response
    params = {"action": action, "target": target}
    if selector:
        params["searchMethod"] = selector
    assert requests == [{"type": "manage_audio", "params": params, "unity_instance": "Project@audio"}]


@pytest.mark.parametrize("clip", ["Assets/Audio/Click.wav", ""])
def test_play_forwards_explicit_clip_even_when_blank(audio_transport, clip):
    _, requests = audio_transport
    result = CliRunner().invoke(cli, ["--format", "json", "audio", "play", "Player", "--clip", clip])
    assert result.exit_code == 0, result.output
    assert requests[0]["params"] == {"action": "play", "target": "Player", "clip": clip}


@pytest.mark.parametrize("action", ["play", "stop", "volume"])
@pytest.mark.parametrize("wrapped", [False, True])
def test_audio_native_failure_exits_and_is_one_json_document(audio_transport, action, wrapped):
    response, requests = audio_transport
    failure = {"success": False, "error": "AudioSource not found", "data": {"target": "Missing", "isPlaying": False}}
    response.clear()
    response.update({"status": "success", "result": failure} if wrapped else failure)
    args = ["--format", "json", "audio", action, "Missing"]
    if action == "volume":
        args.append("0")
    result = CliRunner().invoke(cli, args)
    assert result.exit_code == 1, result.output
    assert json.loads(result.stdout) == failure
    assert len(requests) == 1


@pytest.mark.parametrize("level", ["0", "0.5", "1"])
def test_valid_volume_preserves_component_route(audio_transport, level):
    response, requests = audio_transport
    result = CliRunner().invoke(cli, ["--format", "json", "audio", "volume", "Player", level, "--search-method", "by_name"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == response
    assert requests[0]["type"] == "manage_components"
    assert requests[0]["params"] == {"action": "set_property", "target": "Player", "componentType": "AudioSource", "property": "volume", "value": float(level), "searchMethod": "by_name"}


@pytest.mark.parametrize("level", ["-0.1", "1.1", "nan", "inf", "-inf"])
def test_invalid_volume_rejected_before_transport(audio_transport, level):
    _, requests = audio_transport
    result = CliRunner().invoke(cli, ["audio", "volume", "--", "Player", level])
    assert result.exit_code == 2, result.output
    assert "finite number between 0 and 1" in result.output
    assert requests == []
