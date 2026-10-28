"""Documentation transport failures affect CLI status without confusing not-found."""

import importlib
import json
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
    result = CliRunner().invoke(cli, ["--format", output_format, "docs", "get", "Physics"])
    assert result.exit_code == expected_exit, (result.output, result.exception)
    assert fetch.await_count == 1
    if output_format == "json":
        payload = json.loads(result.output)
        assert payload["success"] is (expected_exit == 0)
        assert payload["data"]["found"] is (outcome == 200)
