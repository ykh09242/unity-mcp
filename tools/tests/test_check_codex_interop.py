"""Contracts for actual-client evidence and isolated Codex configuration."""

import os
import json
from pathlib import Path
import sys
import tomllib

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import check_codex_interop
from check_codex_interop import (
    InteropFailure,
    isolated_environment,
    result_or_raise,
    require_deadline_cancellation,
    text_payload,
    write_config,
)


def test_config_keeps_command_at_server_scope_when_environment_is_first(tmp_path):
    # Given: nested environment data precedes scalar server settings.
    server = {"env": {"PYTHONPATH": "/selected/source"}, "command": "/owned/python"}
    # When: the actual subprocess configuration is serialized.
    write_config(tmp_path, server)
    # Then: TOML nesting cannot silently move the executable into env.
    config = tomllib.loads((tmp_path / "config.toml").read_text(encoding="utf-8"))
    assert config["mcp_servers"]["unity"]["command"] == "/owned/python"
    assert config["mcp_servers"]["unity"]["env"] == {"PYTHONPATH": "/selected/source"}


def test_child_environment_excludes_user_credentials_when_parent_contains_them(
    monkeypatch, tmp_path
):
    # Given: a caller with real-looking credentials and unrelated configuration.
    monkeypatch.setenv("OPENAI_API_KEY", "test-only-never-a-real-key")
    monkeypatch.setenv("CODEX_HOME", "/unrelated/user/config")
    monkeypatch.setenv("ARBITRARY_CREDENTIAL", "test-only")
    # When: the child environment is constructed.
    environment = isolated_environment(tmp_path)
    # Then: it uses only the owned home and process necessities.
    assert environment["CODEX_HOME"] == str(tmp_path)
    assert "OPENAI_API_KEY" not in environment
    assert "ARBITRARY_CREDENTIAL" not in environment
    assert environment.get("PATH") == os.environ.get("PATH")


def test_rpc_result_rejects_error_when_a_failure_response_arrives():
    # Given: a syntactically valid JSON-RPC failure.
    message = {"id": 9, "error": {"code": -32603, "message": "decoder failed"}}
    # When/Then: evidence must fail rather than turn into an empty successful result.
    with pytest.raises(InteropFailure, match="decoder failed"):
        result_or_raise(message)


@pytest.mark.parametrize("result", [{"content": []}, {"content": [{"type": "image"}]}])
def test_text_check_rejects_missing_payload_when_decoder_returns_wrong_content(result):
    # Given: output that does not contain the promised text.
    # When/Then: the oracle refuses to certify truncation or a different content kind.
    with pytest.raises(InteropFailure):
        text_payload(result)


def test_deadline_oracle_rejects_normal_completion_when_only_finally_marker_exists():
    # Given: a server task ran to completion after the client's deadline expired.
    observations = [{"event": "slow_started"}, {"event": "slow_finished"}]
    # When/Then: normal completion must not certify a propagated cancellation.
    with pytest.raises(InteropFailure):
        require_deadline_cancellation(observations)


def test_modern_config_enables_real_client_opt_in_when_selected(tmp_path):
    # Given: the isolated modern profile is selected for an actual-client case.
    # When: the child configuration is serialized.
    write_config(tmp_path, {"command": "/owned/python"}, modern=True)
    # Then: Codex receives the feature gate needed to negotiate modern MCP.
    config = tomllib.loads((tmp_path / "config.toml").read_text(encoding="utf-8"))
    assert config["features"]["mcp_2026_07_28"] is True


@pytest.mark.parametrize("outcome", ["passed", "failed", "mismatch", "mutable"])
def test_execution_receipt_records_the_verified_checkout(tmp_path, monkeypatch, outcome):
    # Given: the actual checkout SHA, without invoking any installed client or credentials.
    actual = check_codex_interop.subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=check_codex_interop.ROOT, text=True
    ).strip()
    expected = "beta" if outcome == "mutable" else "0" * 40 if outcome == "mismatch" else actual
    executed = []

    def execute(function, *args):
        executed.append(function)
        if outcome == "failed":
            raise InteropFailure("synthetic actual-client failure")
        return {"status": "passed", "cases": []}

    monkeypatch.setattr(check_codex_interop.anyio, "run", execute)
    arguments = [
        "--codex",
        "unused-test-binary",
        "--output",
        str(tmp_path),
        "--expected-sha",
        expected,
    ]
    # When: the CLI emits success or failure evidence for the selected checkout.
    if outcome == "passed":
        assert check_codex_interop.main(arguments) == 0
    else:
        with pytest.raises(InteropFailure):
            check_codex_interop.main(arguments)
    # Then: the receipt exposes actual identity and mismatches never execute the client.
    receipt = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
    assert receipt["receiving_sha"] == actual
    assert receipt["status"] == ("passed" if outcome == "passed" else "failed")
    assert bool(executed) is (outcome in {"passed", "failed"})
