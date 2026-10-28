"""Fresh main-thread state owns readiness without a separate ping roundtrip."""

import asyncio  # noqa: ANYIO_OK -- exercise the Hub's event-loop contract.
import statistics
import time

import pytest

from tests.http.test_plugin_read_sharing import peer, read  # noqa: F401 -- pytest fixture.
from tests.timing import watchdog_timeout
from transport.plugin_hub import PluginHub


@pytest.mark.parametrize(
    "configured,expected", [(None, 30), ("90", 90), ("nan", 30), ("999999", 300)]
)
def test_state_budget_is_separate_from_short_ping(configured, expected, monkeypatch):
    if configured is None:
        monkeypatch.delenv("UNITY_MCP_STATE_TIMEOUT", raising=False)
    else:
        monkeypatch.setenv("UNITY_MCP_STATE_TIMEOUT", configured)
    assert PluginHub._command_timeouts("get_editor_state", {}) == (expected, expected)
    assert PluginHub._command_timeouts("ping", {}) == (2, 2)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["ordinary", "authoritative"])
async def test_state_waits_for_actual_state_and_propagates_busy(peer, mode):
    caller = asyncio.create_task(read(peer, "get_editor_state", editor_state_read_mode=mode))
    command = await peer.next_command()
    assert command["name"] == "get_editor_state"
    assert not caller.done()
    state = {"success": True, "data": {"advice": {"ready_for_tools": False}}}
    await peer.reply(command, state)
    assert await caller == state
    assert peer.counts() == {"get_editor_state": 1}


@pytest.mark.asyncio
async def test_authoritative_state_failure_does_not_poison_next_fresh_read(peer):
    failed = asyncio.create_task(
        read(peer, "get_editor_state", editor_state_read_mode="authoritative")
    )
    command = await peer.next_command()
    assert command["name"] == "get_editor_state"
    error = {"success": False, "error": "Unity is reloading", "hint": "retry"}
    await peer.reply(command, error)
    assert await failed == error
    recovered = asyncio.create_task(
        read(peer, "get_editor_state", editor_state_read_mode="authoritative")
    )
    await peer.reply(await peer.next_command())
    assert (await recovered)["success"]
    assert peer.counts() == {"get_editor_state": 2}


@pytest.mark.asyncio
async def test_authoritative_state_deadline_cancels_pending_read(peer, monkeypatch):
    monkeypatch.setenv("UNITY_MCP_STATE_TIMEOUT", "0.03")
    caller = asyncio.create_task(
        read(peer, "get_editor_state", editor_state_read_mode="authoritative")
    )
    command = await peer.next_command()
    assert command["name"] == "get_editor_state"
    result = await asyncio.wait_for(caller, watchdog_timeout())
    assert result["success"] is False
    assert PluginHub._pending == {}
    assert PluginHub._retained_results == {}


@pytest.mark.asyncio
async def test_authoritative_state_delayed_peer_roundtrip_cost(peer):
    # Model a noisy editor queue/network leg, keeping real Hub admission/delivery.
    delays = [0.02, 0.04, 0.03, 0.05, 0.025, 0.045, 0.035]
    measured = []
    for delay in delays:
        started = time.perf_counter()
        caller = asyncio.create_task(
            read(peer, "get_editor_state", editor_state_read_mode="authoritative")
        )
        while not caller.done():
            command = await peer.next_command()
            await asyncio.sleep(delay)
            await peer.reply(command)
            # Await actual completion before attempting a next leg on this call.
            for _ in range(50):
                await asyncio.sleep(0)
            if command["name"] == "get_editor_state":
                await caller
        measured.append((time.perf_counter() - started) * 1000)
    print(
        f"authoritative-state n={len(measured)} median_ms={statistics.median(measured):.3f} "
        f"samples_ms={[round(value, 3) for value in measured]} wire={dict(peer.counts())}"
    )
    assert peer.counts() == {"get_editor_state": len(delays)}
