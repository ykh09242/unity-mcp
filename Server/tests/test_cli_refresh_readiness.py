"""Refresh CLI contracts through Click and the actual HTTP command boundary."""

import importlib
import json
import os
from pathlib import Path
import subprocess
import sys
from unittest.mock import Mock

import httpx
import pytest
from click.testing import CliRunner

from cli.main import cli
from cli.utils import connection


@pytest.fixture
def refresh_cli(monkeypatch):
    editor = importlib.import_module("cli.commands.editor")
    state = importlib.import_module("services.resources.editor_state")
    now = [0.0]
    monkeypatch.setattr(editor, "time", Mock(
        monotonic=lambda: now[0],
        sleep=lambda delay: now.__setitem__(0, now[0] + delay),
    ))
    monkeypatch.setattr(state, "_now_unix_ms", lambda: 100000 + int(now[0] * 1000))
    monkeypatch.setattr(connection, "_auth_headers", lambda config: {})
    client_type = httpx.AsyncClient
    requests = []

    def invoke(handler, args=(), timeout=30):
        def respond(request):
            assert request.url.path == "/api/command"
            payload = json.loads(request.content)
            assert payload["unity_instance"] == "Fixture@hash"
            requests.append((payload, request.extensions["timeout"]["read"]))
            return handler(payload)

        monkeypatch.setattr(connection.httpx, "AsyncClient", lambda: client_type(
            transport=httpx.MockTransport(respond),
        ))
        return CliRunner().invoke(cli, [
            "--instance", "Fixture@hash", "--format", "json", "--timeout", str(timeout),
            "editor", "refresh", *args,
        ])

    return invoke, requests, now


def ready_state(*, playing=False, compiling=False, reloading=False, importing=False):
    return {"success": True, "data": {
        "schema_version": "unity-mcp/editor_state@2", "observed_at_unix_ms": 100000,
        "sequence": 1, "editor": {"play_mode": {"is_playing": playing}},
        "compilation": {"is_compiling": compiling, "is_domain_reload_pending": reloading},
        "tests": {"is_running": False},
        "assets": {"refresh": {"is_refresh_in_progress": importing}},
    }}


ACKNOWLEDGED = {"success": True, "message": "Refresh requested.", "data": {
    "refresh_triggered": True, "compile_requested": False, "resulting_state": "idle",
}}


def test_refresh_stable_play_polls_canonical_state_without_native_wait(refresh_cli):
    invoke, requests, _ = refresh_cli

    def handler(payload):
        if payload["type"] == "refresh_unity":
            assert payload["params"]["wait_for_ready"] is False
            assert payload["params"]["compile"] == "none"
            return httpx.Response(200, json=ACKNOWLEDGED)
        assert payload["type"] == "get_editor_state"
        return httpx.Response(200, json=ready_state(playing=True))

    result = invoke(handler)
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == ACKNOWLEDGED
    assert [payload["type"] for payload, _ in requests] == ["refresh_unity", "get_editor_state"]


def test_refresh_compile_preserves_native_start_barrier_and_waits_afterward(refresh_cli):
    invoke, requests, _ = refresh_cli
    states = iter([ready_state(compiling=True), ready_state(reloading=True), ready_state()])

    def handler(payload):
        if payload["type"] == "refresh_unity":
            assert payload["params"]["compile"] == "request"
            assert payload["params"]["wait_for_ready"] is True
            return httpx.Response(200, json=ACKNOWLEDGED)
        return httpx.Response(200, json=next(states))

    result = invoke(handler, ["--compile"])
    assert result.exit_code == 0, result.output
    assert sum(payload["type"] == "refresh_unity" for payload, _ in requests) == 1
    assert len(requests) == 4


def test_refresh_readiness_survives_reload_read_failures_without_replaying_refresh(refresh_cli):
    invoke, requests, _ = refresh_cli
    states = iter([
        httpx.Response(503, json={"error": "No Unity instances connected"}),
        httpx.Response(200, json={"success": False, "error": "Editor is reloading",
                                 "hint": "retry", "data": {"reason": "reloading"}}),
        httpx.Response(200, json=ready_state()),
    ])

    def handler(payload):
        return httpx.Response(200, json=ACKNOWLEDGED) if payload["type"] == "refresh_unity" else next(states)

    result = invoke(handler)
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == ACKNOWLEDGED
    assert [payload["type"] for payload, _ in requests].count("refresh_unity") == 1
    assert len(requests) == 4


@pytest.mark.parametrize("compile", [False, True])
def test_refresh_no_wait_dispatches_once_and_never_polls(refresh_cli, compile):
    invoke, requests, _ = refresh_cli

    def handler(payload):
        assert payload["type"] == "refresh_unity"
        assert payload["params"]["wait_for_ready"] is False
        return httpx.Response(200, json=ACKNOWLEDGED)

    result = invoke(handler, ["--no-wait", *(["--compile"] if compile else [])])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == ACKNOWLEDGED
    assert len(requests) == 1


def test_refresh_definitive_rejection_is_preserved_without_polling(refresh_cli):
    invoke, requests, _ = refresh_cli
    rejected = {"success": False, "error": "Compilation is unavailable", "data": {"reason": "unsupported"}}
    result = invoke(lambda payload: httpx.Response(200, json=rejected), ["--compile"])
    assert result.exit_code == 1, result.output
    assert json.loads(result.stdout) == rejected
    assert len(requests) == 1


def test_refresh_lost_response_does_not_synthesize_success_or_replay(refresh_cli):
    invoke, requests, _ = refresh_cli
    lost = {"success": False, "error": "Unity plugin disconnected while awaiting command_result"}
    result = invoke(lambda payload: httpx.Response(200, json=lost))
    assert result.exit_code == 1, result.output
    assert json.loads(result.stdout) == lost
    assert len(requests) == 1


def test_refresh_deadline_covers_dispatch_and_polls_and_rejects_late_ready(refresh_cli):
    invoke, requests, now = refresh_cli

    def handler(payload):
        if payload["type"] == "refresh_unity":
            now[0] += 0.4
            return httpx.Response(200, json=ACKNOWLEDGED)
        now[0] += 0.3
        return httpx.Response(200, json=ready_state(compiling=len(requests) == 2))

    result = invoke(handler, timeout=1)
    assert result.exit_code == 1, result.output
    response = json.loads(result.stdout)
    assert response["success"] is False
    assert response["error"] == "refresh_timeout_waiting_for_ready"
    assert response["data"]["refresh_response"] == ACKNOWLEDGED
    assert requests[0][1] == 1
    assert requests[1][1] == pytest.approx(0.6)
    assert 0 < requests[2][1] < 0.1
    assert len(requests) == 3


def test_refresh_readiness_definitive_rejection_is_not_retried(refresh_cli):
    invoke, requests, _ = refresh_cli
    rejected = {"success": False, "error": "Invalid editor state query", "data": {"reason": "invalid_params"}}

    def handler(payload):
        return httpx.Response(200, json=ACKNOWLEDGED if payload["type"] == "refresh_unity" else rejected)

    result = invoke(handler)
    assert result.exit_code == 1, result.output
    assert json.loads(result.stdout) == rejected
    assert len(requests) == 2


def test_refresh_readiness_authentication_error_is_not_retried(refresh_cli):
    invoke, requests, _ = refresh_cli

    def handler(payload):
        if payload["type"] == "refresh_unity":
            return httpx.Response(200, json=ACKNOWLEDGED)
        return httpx.Response(401, json={"error": "Unauthorized"})

    result = invoke(handler)
    assert result.exit_code == 1
    assert "HTTP error from server: 401" in result.stderr
    assert len(requests) == 2


def test_canonical_advice_import_does_not_start_threads_or_connect(tmp_path):
    script = '''
import socket
import sys
import threading
sys.path.insert(0, "src")
def forbidden(*args, **kwargs):
    raise AssertionError("Canonical advice import must not start runtime services")
socket.socket.connect = forbidden
threading.Thread.start = forbidden
from services.resources.editor_state import _enrich_advice_and_staleness
from services.registry import get_registered_resources
from services.state.external_changes_scanner import external_changes_scanner
assert not external_changes_scanner._states
assert any(resource["name"] == "editor_state" for resource in get_registered_resources())
assert _enrich_advice_and_staleness({})["advice"]["ready_for_tools"] is True
'''
    env = {**os.environ, "UNITY_MCP_DISABLE_TELEMETRY": "true"}
    for key in ("APPDATA", "XDG_DATA_HOME", "UNITY_MCP_LOG_DIR"):
        directory = tmp_path / key
        directory.mkdir()
        env[key] = str(directory)
    result = subprocess.run([sys.executable, "-c", script],
                            cwd=Path(__file__).resolve().parents[1], env=env,
                            capture_output=True, text=True, timeout=30, check=False)
    assert result.returncode == 0, result.stdout + result.stderr
