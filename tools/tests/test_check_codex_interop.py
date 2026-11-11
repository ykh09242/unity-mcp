"""Contracts for actual-client evidence and isolated Codex configuration."""

import os
import json
from pathlib import Path
import sys
import tomllib
import asyncio
from contextlib import suppress
from types import SimpleNamespace

import anyio
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


@pytest.mark.parametrize("outcome", ["passed", "failed", "grouped", "mismatch", "mutable"])
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
        if outcome == "grouped":
            raise ExceptionGroup(
                "owned task failure", [InteropFailure("actual-client child failed")]
            )
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
        with pytest.raises(ExceptionGroup if outcome == "grouped" else InteropFailure):
            check_codex_interop.main(arguments)
    # Then: the receipt exposes actual identity and mismatches never execute the client.
    receipt = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
    assert receipt["receiving_sha"] == actual
    assert receipt["status"] == ("passed" if outcome == "passed" else "failed")
    assert bool(executed) is (outcome in {"passed", "failed", "grouped"})


class RpcPipe:
    """Owned byte transport double; the production demultiplexer parses every frame."""

    def __init__(self):
        self.sent = asyncio.Queue()
        self.received = asyncio.Queue()

    async def send(self, value):
        await self.sent.put(json.loads(value))

    async def receive(self, _size):
        value = await self.received.get()
        if value is None:
            raise anyio.EndOfStream
        return value


@pytest.mark.asyncio
async def test_rpc_overlap_routes_reverse_responses_and_retains_notification_payload():
    pipe = RpcPipe()
    client = check_codex_interop.AppServer(SimpleNamespace(stdin=pipe, stdout=pipe))
    # Missing one reader loses notifications or delivers another request's result.
    reader = (
        asyncio.create_task(client.read_messages()) if hasattr(client, "read_messages") else None
    )
    first = asyncio.create_task(client.request("first", {}))
    second = asyncio.create_task(client.request("second", {}))
    try:
        with anyio.fail_after(5):
            sent = [await pipe.sent.get(), await pipe.sent.get()]
            ids = {item["method"]: item["id"] for item in sent}
            notice = {"method": "thread/closed", "params": {"threadId": "owned-thread"}}
            frames = [
                notice,
                {"id": ids["second"], "result": {"owner": "second"}},
                {"id": ids["first"], "result": {"owner": "first"}},
            ]
            encoded = b"".join(json.dumps(frame).encode() + b"\n" for frame in frames)
            await pipe.received.put(encoded[:17])
            await pipe.received.put(encoded[17:])
            assert await second == {"owner": "second"}
            assert await first == {"owner": "first"}
            assert await client.wait_notification("thread/closed", "owned-thread") == notice
            assert client.notifications == [notice]
    finally:
        for task in (first, second, reader):
            if task is None:
                continue
            task.cancel()
            with suppress(asyncio.CancelledError, InteropFailure):
                await task


@pytest.mark.asyncio
async def test_rpc_reader_failure_releases_all_pending_waiters():
    pipe = RpcPipe()
    client = check_codex_interop.AppServer(SimpleNamespace(stdin=pipe, stdout=pipe))
    reader = (
        asyncio.create_task(client.read_messages()) if hasattr(client, "read_messages") else None
    )
    requests = [asyncio.create_task(client.request(name, {})) for name in ("one", "two")]
    try:
        with anyio.fail_after(5):
            await pipe.sent.get()
            await pipe.sent.get()
            await pipe.received.put(None)
            await pipe.received.put(None)
            results = await asyncio.gather(*requests, return_exceptions=True)
            assert all(isinstance(result, InteropFailure) for result in results)
            if reader is not None:
                await reader
            with pytest.raises(InteropFailure):
                await client.request("after-eof", {})
    finally:
        for task in (*requests, reader):
            if task is None:
                continue
            task.cancel()
            with suppress(asyncio.CancelledError, anyio.EndOfStream, InteropFailure):
                await task


def test_deadline_oracle_requires_matching_request_not_another_threads_cancel():
    observations = [{"event": "slow_cancelled", "marker": "other-thread"}]
    with pytest.raises(InteropFailure):
        require_deadline_cancellation(observations, marker="owned-thread")


def test_observations_merge_owned_process_journals_without_reading_partial_tail(tmp_path):
    # Different writers own different files; timestamps order their independent observations.
    (tmp_path / "observations-a.jsonl").write_text(
        '{"event":"process_started","observed_ns":10}\n'
        '{"event":"request","observed_ns":30}\n{"partial":',
        encoding="utf-8",
    )
    (tmp_path / "observations-b.jsonl").write_text(
        '{"event":"process_started","observed_ns":20}\n', encoding="utf-8"
    )
    assert [item["observed_ns"] for item in check_codex_interop.observations(tmp_path)] == [
        10,
        20,
        30,
    ]
