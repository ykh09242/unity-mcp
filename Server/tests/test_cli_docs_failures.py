"""Documentation transport failures affect CLI status without confusing not-found."""

import importlib
import asyncio
import json
import subprocess
import sys
from unittest.mock import AsyncMock

import pytest
from click.testing import CliRunner

from cli.main import cli


@pytest.mark.parametrize("outcome,expected_exit", [("network", 1), (503, 1), (404, 0), (200, 0)])
@pytest.mark.parametrize("output_format", ["text", "json"])
def test_docs_cli_exit_follows_fetch_success(monkeypatch, outcome, expected_exit, output_format):
    module = importlib.import_module("services.tools.unity_docs")
    fetch = (
        AsyncMock(side_effect=ConnectionError("offline"))
        if outcome == "network"
        else AsyncMock(side_effect=lambda url: (outcome, "<h1>Physics</h1>", url))
    )
    monkeypatch.setattr(module, "_fetch_url_full", fetch)
    command = importlib.import_module("cli.commands.docs")
    monkeypatch.setattr(
        command,
        "_lookup_doc",
        lambda name, member, version, _timeout: asyncio.run(module._get_doc(name, member, version)),
    )
    result = CliRunner().invoke(cli, ["--format", output_format, "docs", "get", "Physics"])
    assert result.exit_code == expected_exit, (result.output, result.exception)
    assert fetch.await_count == 1
    if output_format == "json":
        payload = json.loads(result.output)
        assert payload["success"] is (expected_exit == 0)
        assert payload["data"]["found"] is (outcome == 200)


@pytest.mark.parametrize("output_format", ["text", "json"])
def test_docs_cli_kills_and_reaps_blocked_executor_worker(monkeypatch, output_format):
    module = importlib.import_module("cli.commands.docs")
    # A completed coroutine/result does not let Python exit while a real
    # executor thread is still blocked. No external network is involved.
    source = """
from concurrent.futures import ThreadPoolExecutor
from threading import Event
print('{"success": true}', flush=True)
ThreadPoolExecutor(1).submit(Event().wait)
"""
    monkeypatch.setattr(module, "_worker_command", lambda: [sys.executable, "-c", source])
    launched = []
    popen = subprocess.Popen

    def record_process(*args, **kwargs):
        process = popen(*args, **kwargs)
        launched.append(process)
        return process

    monkeypatch.setattr(module.subprocess, "Popen", record_process)
    result = CliRunner().invoke(
        cli, ["--timeout", "1", "--format", output_format, "docs", "get", "Physics"]
    )
    assert result.exit_code == 1, (result.output, result.exception)
    assert len(launched) == 1
    assert launched[0].poll() is not None
    assert result.stderr == ""
    if output_format == "json":
        payload = json.loads(result.stdout)
        assert payload["success"] is False
        assert payload["error_type"] == "TimeoutError"
        assert "1s" in payload["message"]


def test_docs_cli_worker_preserves_arguments_and_unicode_result(monkeypatch):
    module = importlib.import_module("cli.commands.docs")
    source = """
import services.tools.unity_docs as service
from cli.commands.docs import _run_lookup_worker
async def fake_doc(name, member, version):
    return {"success": True, "data": {"class": name, "member": member,
                                     "version": version, "description": "caf\\u00e9"}}
service._get_doc = fake_doc
_run_lookup_worker()
"""
    monkeypatch.setattr(module, "_worker_command", lambda: [sys.executable, "-c", source])
    result = CliRunner().invoke(
        cli, ["--format", "json", "docs", "get", "Physics", "Raycast", "-v", "6000.0"]
    )
    assert result.exit_code == 0, (result.output, result.exception)
    assert json.loads(result.stdout) == {
        "success": True,
        "data": {
            "class": "Physics",
            "member": "Raycast",
            "version": "6000.0",
            "description": "caf\u00e9",
        },
    }


@pytest.mark.parametrize(
    "source", ["raise SystemExit(3)", "print('invalid result')", "print('[]')"]
)
def test_docs_cli_worker_failures_are_one_structured_document(monkeypatch, source):
    module = importlib.import_module("cli.commands.docs")
    monkeypatch.setattr(module, "_worker_command", lambda: [sys.executable, "-c", source])
    result = CliRunner().invoke(cli, ["--format", "json", "docs", "get", "Physics"])
    assert result.exit_code == 1, (result.output, result.exception)
    assert json.loads(result.stdout)["error_type"] == "DocumentationWorkerError"
    assert result.stderr == ""


def test_docs_cli_worker_start_failure_is_structured(monkeypatch):
    module = importlib.import_module("cli.commands.docs")

    def fail_start(*_args, **_kwargs):
        raise OSError("fixture start failure")

    monkeypatch.setattr(module.subprocess, "run", fail_start)
    result = CliRunner().invoke(cli, ["--format", "json", "docs", "get", "Physics"])
    assert result.exit_code == 1, (result.output, result.exception)
    assert json.loads(result.stdout)["error_type"] == "DocumentationWorkerError"
    assert result.stderr == ""
