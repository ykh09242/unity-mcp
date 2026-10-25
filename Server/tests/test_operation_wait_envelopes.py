"""Native command acknowledgement must not terminate an active operation wait."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastmcp import Client, FastMCP

from services.tools import wait_for_operation as wait_module

JOB_ID = "a" * 32


def envelope(kind, state, **evidence):
    """Use the raw HTTP reply shape returned by PluginHub.send_command."""
    return {
        "status": "success",
        "result": {
            "success": True,
            "data": {
                "job_id": JOB_ID,
                "result" if kind == "build" else "status": state,
                **evidence,
            },
        },
    }


@pytest.fixture
def native_status(monkeypatch):
    target = wait_module.ObservationTarget("Fixture@owned", "session", "one", None)
    monkeypatch.setattr(wait_module, "capture_target", AsyncMock(return_value=target))
    monkeypatch.setattr(wait_module, "POLL_INTERVAL", 0)
    monkeypatch.setattr(wait_module.config, "transport_mode", "http")
    identity = SimpleNamespace(generation="one", user_id=None)
    monkeypatch.setattr(wait_module.PluginHub, "_read_identity", AsyncMock(return_value=identity))
    send = AsyncMock()
    monkeypatch.setattr(wait_module.PluginHub, "send_command", send)
    return send


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kind,active,terminal",
    [
        ("scenario", "running", "succeeded"),
        ("scenario", "running", "failed"),
        ("scenario", "running", "cancelled"),
        ("scenario", "running", "timed_out"),
        ("scenario", "running", "interrupted"),
        ("build", "building", "failed"),
        ("package", "running", "succeeded"),
    ],
)
async def test_wait_observes_native_job_when_command_envelope_succeeds(
    native_status, kind, active, terminal
):
    # Given: a successful HTTP status command first reports an active native job.
    native_status.side_effect = [envelope(kind, active), envelope(kind, terminal)]
    # When: the caller waits for the existing job.
    result = await wait_module.wait_for_operation(AsyncMock(), kind, JOB_ID, 15)
    # Then: the acknowledgement cannot finish the wait; only the native outcome does.
    assert result["success"] is True
    assert result["data"]["terminal"] is True
    assert result["data"]["job_status"] == terminal
    assert native_status.await_count == 2
    assert all(
        call.args
        == ("session", wait_module._COMMANDS[kind], {"action": "status", "job_id": JOB_ID})
        and call.kwargs == {"_expected_generation": "one"}
        for call in native_status.await_args_list
    )


@pytest.mark.asyncio
async def test_wait_preserves_background_starting_state_when_shared_budget_expires(
    monkeypatch, native_status
):
    # Given: the unfocused native Editor has not started Play before the original budget.
    native_status.return_value = envelope(
        "scenario", "running", phase="starting", is_focused=False, play_requested=False
    )
    monkeypatch.setattr(wait_module, "POLL_INTERVAL", 1)
    token = wait_module.test_job_wait_deadline.set(asyncio.get_running_loop().time() + 0.02)
    try:
        # When: this wait inherits the small remaining budget instead of starting a new one.
        result = await wait_module.wait_for_operation(AsyncMock(), "scenario", JOB_ID, 15)
    finally:
        wait_module.test_job_wait_deadline.reset(token)
    # Then: expiry retains honest progress and sends no cancellation, restart or focus command.
    assert result["data"]["wait_status"] == "expired"
    assert result["data"]["terminal"] is False
    assert result["data"]["job_status"] == "running"
    assert result["data"]["job"]["phase"] == "starting"
    assert result["data"]["job"]["is_focused"] is False
    native_status.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["2026-07-28", "legacy"])
async def test_sdk_wait_returns_job_outcome_when_http_command_succeeds(native_status, mode):
    # Given: both public SDK protocols receive the actual raw HTTP envelope.
    native_status.side_effect = [
        envelope("scenario", "running"),
        envelope("scenario", "running", phase="finalizing", runner_resources_released=False),
        envelope("scenario", "failed", runner_resources_released=True),
    ]
    app = FastMCP("operation-wait-envelope")
    app.tool(wait_module.wait_for_operation)
    # When: a public caller requests a bounded wait.
    async with Client(app, mode=mode) as client:
        reply = await client.call_tool(
            "wait_for_operation", {"kind": "scenario", "job_id": JOB_ID, "wait_timeout": 15}
        )
    # Then: the result contains the native failure after finalization, not command success.
    result = json.loads(reply.content[0].text)
    assert result["success"] is True
    assert result["data"]["terminal"] is True
    assert result["data"]["job_status"] == "failed"
    assert result["data"]["job"]["runner_resources_released"] is True
    assert native_status.await_count == 3


@pytest.mark.parametrize("kind", ["scenario", "package"])
def test_projection_preserves_unknown_state_when_http_command_succeeds(kind):
    # Given: successful observation cannot establish the native job outcome.
    native = envelope(kind, "unknown")
    # When: the common adapter projects the raw command envelope.
    result = wait_module.project_observation(kind, JOB_ID, native)
    # Then: unknown remains observable and cannot be promoted to terminal success.
    assert result["data"]["phase"] == "unknown"
    assert result["data"]["terminal"] is False
    assert result["data"]["job"] is native["result"]["data"]


def test_projection_rejects_foreign_job_when_http_command_succeeds():
    # Given: a successful status query contains evidence for another native job.
    native = envelope("scenario", "succeeded")
    native["result"]["data"]["job_id"] = "b" * 32
    # When: the common adapter checks the selected job identity.
    result = wait_module.project_observation("scenario", JOB_ID, native)
    # Then: the transport envelope cannot bypass the owner job identity check.
    assert result["success"] is False
    assert result["data"]["reason"] == "job_observation_identity_mismatch"


@pytest.mark.asyncio
async def test_target_capture_uses_remaining_wait_budget_when_identity_read_is_slow(
    monkeypatch, native_status
):
    # Given: identity capture outlives the remaining Tasks/fallback observation budget.
    completed = []

    async def slow_capture(_ctx):
        await asyncio.sleep(0.05)
        completed.append(True)
        return wait_module.ObservationTarget("Fixture@owned", "session", "one", None)

    monkeypatch.setattr(wait_module, "capture_target", slow_capture)
    token = wait_module.test_job_wait_deadline.set(asyncio.get_running_loop().time() + 0.01)
    try:
        # When: the caller observes an existing scenario with that inherited deadline.
        result = await wait_module.wait_for_operation(AsyncMock(), "scenario", JOB_ID, 15)
    finally:
        wait_module.test_job_wait_deadline.reset(token)
    # Then: capture consumes only the remaining budget and no native status request is sent.
    assert completed == []
    assert result["data"]["reason"] == "observation_wait_expired_before_status"
    native_status.assert_not_awaited()


@pytest.mark.asyncio
async def test_wait_stops_when_captured_generation_changes(native_status, monkeypatch):
    # Given: an active job belongs to the original authenticated Editor generation.
    native_status.return_value = envelope("scenario", "running")
    monkeypatch.setattr(
        wait_module.PluginHub,
        "_read_identity",
        AsyncMock(
            side_effect=[
                SimpleNamespace(generation="one", user_id=None),
                SimpleNamespace(generation="two", user_id=None),
            ]
        ),
    )
    # When: the next observation sees a replacement connection generation.
    result = await wait_module.wait_for_operation(AsyncMock(), "scenario", JOB_ID, 15)
    # Then: it cannot poll a new owner, restart the scenario, or report old work complete.
    assert result["success"] is False
    assert result["data"]["reason"] == "observation_generation_changed"
    native_status.assert_awaited_once()


@pytest.mark.asyncio
async def test_zero_wait_returns_one_nonterminal_observation_when_command_succeeds(native_status):
    # Given: a successful query reports an active scenario.
    native_status.return_value = envelope("scenario", "running")
    # When: the caller explicitly requests one observation without a completion wait.
    result = await wait_module.wait_for_operation(AsyncMock(), "scenario", JOB_ID, 0)
    # Then: one poll returns progress without implying completion.
    assert result["data"]["wait_status"] == "observed"
    assert result["data"]["terminal"] is False
    native_status.assert_awaited_once()
