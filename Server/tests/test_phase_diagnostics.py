"""Payload-free diagnostic ownership and negotiated wire propagation."""

import asyncio
from contextvars import copy_context

import pytest
from tests.test_http_command_outcome import command_hub, dispatch  # pylint: disable=unused-import
from core.config import config
from core import phase_diagnostics as diag


def test_disabled_diagnostics_never_read_clock_or_generate_tokens(monkeypatch):
    monkeypatch.setattr(config, "phase_diagnostics", False)

    def forbidden():
        raise AssertionError("Disabled diagnostics did work")

    monkeypatch.setattr(diag, "clock", forbidden)
    monkeypatch.setattr(diag, "new_token", forbidden)
    with diag.invocation("command"):
        assert diag.request("command", True) is None
        diag.step("command", "send")
    assert diag.current() is None


@pytest.mark.asyncio
async def test_concurrent_invocations_have_distinct_tokens_and_closed_inherited_owners(monkeypatch):
    monkeypatch.setattr(config, "phase_diagnostics", True)
    monkeypatch.setattr(diag, "sample", lambda: True)
    rows = []
    monkeypatch.setattr(diag, "offer", rows.append)
    ready, release = asyncio.Event(), asyncio.Event()
    tokens = []
    contexts = []

    async def work():
        with diag.invocation("command"):
            token = diag.request("command", True)
            assert token and len(token["correlation"]) == 32
            tokens.append(token["correlation"])
            contexts.append(copy_context())
            assert diag.request("probe", True) is None
            diag.step("command", "send")
            if len(tokens) == 2:
                ready.set()
            await release.wait()
            diag.step("command", "response")

    tasks = [asyncio.create_task(work()) for _ in range(2)]
    await ready.wait()
    release.set()
    await asyncio.gather(*tasks)
    assert len(set(tokens)) == 2
    assert {r["correlation"] for r in rows} == set(tokens)
    assert all(r["outcome"] == "returned" for r in rows)
    for context in contexts:
        assert context.run(diag.request, "command", True) is None
    assert diag.current() is None
    assert all(set(r) <= diag.RECORD_FIELDS for r in rows)


def test_unnegotiated_and_cancellation_keep_local_observation_without_payload(monkeypatch):
    monkeypatch.setattr(config, "phase_diagnostics", True)
    monkeypatch.setattr(diag, "sample", lambda: True)
    rows = []
    monkeypatch.setattr(diag, "offer", rows.append)
    with pytest.raises(asyncio.CancelledError):
        with diag.invocation("SECRET /private/path"):
            assert diag.request("SECRET /private/path", False) is None
            raise asyncio.CancelledError("credentials")
    assert len(rows) == 1
    assert rows[0]["outcome"] == "canceled"
    assert not rows[0]["native_requested"]
    assert "SECRET" not in str(rows) and "credentials" not in str(rows)
    assert diag.current() is None


def test_optional_sink_failure_cannot_replace_command_outcome(monkeypatch):
    monkeypatch.setattr(config, "phase_diagnostics", True)
    monkeypatch.setattr(diag, "sample", lambda: True)

    def broken(_):
        raise OSError("sensitive disk error")

    monkeypatch.setattr(diag, "offer", broken)
    with diag.invocation("command"):
        pass
    assert diag.current() is None


def test_bounded_sink_drops_observations_without_waiting_for_disk():
    import threading

    entered, release = threading.Event(), threading.Event()

    def blocked(_):
        entered.set()
        release.wait(5)

    sink = diag.BufferedSink(blocked)
    try:
        sink.offer({"event": "test"})
        assert entered.wait(2)
        for _ in range(1000):
            sink.offer({"event": "test"})
        assert sink.queue.qsize() == 256
    finally:
        release.set()
        sink.close()
    assert not sink.worker.is_alive()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "enabled,negotiated", [(False, False), (False, True), (True, False), (True, True)]
)
async def test_actual_hub_wire_adds_only_negotiated_metadata_and_preserves_response(
    command_hub, monkeypatch, enabled, negotiated
):
    monkeypatch.setattr(config, "phase_diagnostics", enabled)
    monkeypatch.setattr(diag, "sample", lambda: True)
    rows = []
    monkeypatch.setattr(diag, "offer", rows.append)
    command_hub.websocket.state.plugin_features = frozenset({diag.CAPABILITY} if negotiated else {})
    result = await dispatch()
    assert result["success"] is True
    assert len(command_hub.sent) == 1
    message = command_hub.sent[0]
    assert message["params"] == {}
    assert ("diagnostics" in message) == (enabled and negotiated)
    if enabled:
        assert len(rows) == 1
        assert rows[0]["native_requested"] == negotiated
        if negotiated:
            assert message["diagnostics"] == {"correlation": rows[0]["correlation"]}
        assert "preflight_ms" in rows[0]
    else:
        assert rows == []


def test_nested_unsampled_scope_cannot_borrow_parent_token(monkeypatch):
    monkeypatch.setattr(config, "phase_diagnostics", True)
    samples = iter([True, False])
    monkeypatch.setattr(diag, "sample", lambda: next(samples))
    monkeypatch.setattr(diag, "offer", lambda _: None)
    with diag.invocation("command"):
        parent = diag.current()
        with diag.invocation("command"):
            assert diag.request("command", True) is None
        assert diag.current() is parent
