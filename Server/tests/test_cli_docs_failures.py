"""Documentation transport failures affect CLI status without confusing not-found."""

import importlib
import asyncio
import json
from unittest.mock import AsyncMock

import pytest
from click.testing import CliRunner

from cli.main import cli
from tests.timing import watchdog_timeout


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
    result = CliRunner().invoke(cli, ["--format", output_format, "docs", "get", "Physics"])
    assert result.exit_code == expected_exit, (result.output, result.exception)
    assert fetch.await_count == 1
    if output_format == "json":
        payload = json.loads(result.output)
        assert payload["success"] is (expected_exit == 0)
        assert payload["data"]["found"] is (outcome == 200)


@pytest.mark.parametrize("output_format", ["text", "json"])
def test_docs_cli_bounds_entire_request_and_cancels_fetch(monkeypatch, output_format):
    module = importlib.import_module("services.tools.unity_docs")
    cancelled = []

    async def blocked_fetch(_url):
        try:
            await asyncio.sleep(watchdog_timeout())
            return 200, "<h1>Physics</h1>", _url
        finally:
            cancelled.append(True)

    monkeypatch.setattr(module, "_fetch_url_full", blocked_fetch)
    result = CliRunner().invoke(
        cli, ["--timeout", "1", "--format", output_format, "docs", "get", "Physics"]
    )
    assert result.exit_code == 1, (result.output, result.exception)
    assert cancelled == [True]
    assert result.stderr == ""
    if output_format == "json":
        payload = json.loads(result.stdout)
        assert payload["success"] is False
        assert payload["error_type"] == "TimeoutError"
        assert "1s" in payload["message"]


def test_docs_cli_timeout_is_shared_across_fallback_fetches(monkeypatch):
    module = importlib.import_module("services.tools.unity_docs")
    calls = []

    async def missing_fetch(url):
        calls.append(url)
        await asyncio.sleep(0.6)
        return 404, "", url

    monkeypatch.setattr(module, "_fetch_url_full", missing_fetch)
    result = CliRunner().invoke(
        cli, ["--timeout", "1", "--format", "json", "docs", "get", "Physics", "-v", "6000.0"]
    )
    assert result.exit_code == 1, (result.output, result.exception)
    assert json.loads(result.stdout)["error_type"] == "TimeoutError"
    assert calls
