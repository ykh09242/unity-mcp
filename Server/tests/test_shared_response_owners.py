"""Production Hub + shared-read response reservation regressions, with owned fakes."""

import asyncio
import importlib
from copy import deepcopy
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from starlette.websockets import WebSocketState

from models.response_limits import ResponseOwner, response_owner, response_size
from services.tools.shared_tool_reads import SharedToolReads
from transport.models import CommandResultMessage
from transport.plugin_hub import PluginHub
from transport.charge_ledger import ChargeLedger
from transport.plugin_registry import PluginSession
from tests.timing import await_signal

RESULT = {"success": True, "data": {"status": "succeeded", "tests": [{"name": "owned-test"}]}}


@pytest.fixture
def peer(monkeypatch):
    sent = asyncio.Event()
    messages = []
    now = datetime.now(timezone.utc)
    selected = PluginSession(
        session_id="owned-session",
        project_name="Owned",
        project_hash="owned",
        unity_version="test",
        registered_at=now,
        connected_at=now,
        user_id="owned-user",
    )

    async def send_json(message):
        messages.append(message)
        sent.set()

    async def session(session_id):
        assert session_id == selected.session_id
        return selected

    websocket = SimpleNamespace(
        state=SimpleNamespace(plugin_generation="owned-generation", user_id="owned-user"),
        client_state=WebSocketState.CONNECTED,
        application_state=WebSocketState.CONNECTED,
        send_json=send_json,
    )
    monkeypatch.setattr(PluginHub, "_registry", SimpleNamespace(get_session=session))
    monkeypatch.setattr(PluginHub, "_lock", asyncio.Lock())
    monkeypatch.setattr(PluginHub, "_connections", {"owned-session": websocket})
    monkeypatch.setattr(PluginHub, "_pending", {})
    monkeypatch.setattr(PluginHub, "_retained_results", ChargeLedger())
    monkeypatch.setattr(PluginHub, "_raw_results", ChargeLedger())
    monkeypatch.setattr(PluginHub, "_large_results", None)
    handler = PluginHub({"type": "websocket"}, None, None)

    async def deliver(*producers):
        await await_signal(sent, "owned Hub command dispatched", producers=producers)
        await handler._handle_command_result(
            websocket, CommandResultMessage(id=messages[-1]["id"], result=RESULT)
        )

    return SimpleNamespace(sent=sent, messages=messages, deliver=deliver)


async def wait(reads, owner, accepted=None, release=None):
    token = response_owner.set(owner)
    try:
        async with reads.session("owned-job") as read:
            result = await read.fetch(
                lambda: PluginHub.send_command("owned-session", "get_test_job", {})
            )
            assert result == RESULT
            if accepted is not None:
                accepted.set()
            if release is not None:
                await release.wait()
            return result
    except asyncio.CancelledError:
        owner.release()
        raise
    finally:
        response_owner.reset(token)


@pytest.mark.asyncio
async def test_cancelled_first_owner_cannot_receive_late_shared_reservation(peer):
    reads = SharedToolReads[dict](freshness_s=2)
    first_owner, second_owner = ResponseOwner(), ResponseOwner()
    first = asyncio.create_task(wait(reads, first_owner))
    await await_signal(peer.sent, "first shared command dispatched", producers=(first,))
    second = asyncio.create_task(wait(reads, second_owner))
    await asyncio.sleep(0.02)
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first
    await peer.deliver()
    assert (await second)["success"]
    assert first_owner.entries == []
    assert len(second_owner.entries) == 1
    assert len(PluginHub._retained_results) == 1
    second_owner.release()
    assert PluginHub._retained_results == {}


@pytest.mark.asyncio
async def test_first_delivery_keeps_shared_snapshot_and_second_copy_charged(peer):
    reads = SharedToolReads[dict](freshness_s=2)
    first_owner, second_owner = ResponseOwner(), ResponseOwner()
    accepted, release = asyncio.Event(), asyncio.Event()
    first = asyncio.create_task(wait(reads, first_owner))
    await await_signal(peer.sent, "first shared command dispatched", producers=(first,))
    second = asyncio.create_task(wait(reads, second_owner, accepted, release))
    await asyncio.sleep(0.02)
    await peer.deliver()
    assert (await first)["success"]
    await await_signal(accepted, "shared response adopted", producers=(second,))
    assert len(PluginHub._retained_results) == 3
    first_owner.release()
    assert len(PluginHub._retained_results) == 2
    release.set()
    assert (await second)["success"]
    assert len(PluginHub._retained_results) == 1
    second_owner.release()
    assert PluginHub._retained_results == {}


@pytest.mark.asyncio
async def test_fanout_reserves_before_copy_and_refuses_capacity_without_second_rpc(
    peer, monkeypatch
):
    reads = SharedToolReads[dict](freshness_s=2)
    charge = response_size(RESULT)
    for cap in (
        "MAX_RETAINED_RESULT_BYTES",
        "MAX_RETAINED_RESULT_BYTES_PER_USER",
        "MAX_RETAINED_RESULT_BYTES_PER_SESSION",
    ):
        monkeypatch.setattr(PluginHub, cap, 2 * charge)
    first_owner, second_owner = ResponseOwner(), ResponseOwner()
    allocations = []

    def allocate(value):
        allocations.append(value)
        return deepcopy(value)

    monkeypatch.setattr(
        importlib.import_module("services.tools.shared_tool_reads"), "deepcopy", allocate
    )
    accepted, release = asyncio.Event(), asyncio.Event()
    first = asyncio.create_task(wait(reads, first_owner, accepted, release))
    await await_signal(peer.sent, "first shared command dispatched", producers=(first,))
    second = asyncio.create_task(wait(reads, second_owner))
    await asyncio.sleep(0.02)
    await peer.deliver()
    await await_signal(accepted, "first response adopted", producers=(first,))
    with pytest.raises(RuntimeError, match="result_capacity"):
        await second
    assert len(peer.messages) == 1
    assert second_owner.entries == []
    assert len(allocations) == 1
    assert len(PluginHub._retained_results) == 2
    release.set()
    await first
    first_owner.release()
    assert PluginHub._retained_results == {}


@pytest.mark.asyncio
async def test_expired_snapshot_releases_its_charge_without_releasing_delayed_copy(peer):
    reads = SharedToolReads[dict](freshness_s=0.02)
    first_owner, second_owner = ResponseOwner(), ResponseOwner()
    async with reads.session("owned-job") as read:
        token = response_owner.set(first_owner)
        try:
            first = asyncio.create_task(
                read.fetch(lambda: PluginHub.send_command("owned-session", "get_test_job", {}))
            )
            await peer.deliver(first)
            await first
        finally:
            response_owner.reset(token)
        assert len(PluginHub._retained_results) == 2
        await asyncio.sleep(0.03)
        peer.sent.clear()
        token = response_owner.set(second_owner)
        try:
            second = asyncio.create_task(
                read.fetch(lambda: PluginHub.send_command("owned-session", "get_test_job", {}))
            )
            await await_signal(peer.sent, "fresh snapshot command dispatched", producers=(second,))
            # Expiry releases the shared source, retaining the first HTTP copy.
            assert len(PluginHub._retained_results) == 1
            await peer.deliver()
            await second
        finally:
            response_owner.reset(token)
        assert len(PluginHub._retained_results) == 3
    assert len(PluginHub._retained_results) == 2
    first_owner.release()
    second_owner.release()
    assert PluginHub._retained_results == {}


@pytest.mark.asyncio
async def test_copy_failure_returns_new_capacity_and_final_snapshot_cleanup(peer, monkeypatch):
    reads = SharedToolReads[dict](freshness_s=2)
    owner = ResponseOwner()

    def fail_copy(value):
        raise RuntimeError("owned copy failure")

    monkeypatch.setattr(
        importlib.import_module("services.tools.shared_tool_reads"), "deepcopy", fail_copy
    )
    task = asyncio.create_task(wait(reads, owner))
    await peer.deliver(task)
    with pytest.raises(RuntimeError, match="owned copy failure"):
        await task
    assert owner.entries == []
    assert PluginHub._retained_results == {}


def test_multi_reservation_refusal_rolls_back_only_new_copies():
    source, existing = ResponseOwner(), ResponseOwner()
    retained = {"source-a": 1, "source-b": 1, "existing": 1}
    source.entries.extend([(retained, "source-a"), (retained, "source-b")])
    existing.entries.append((retained, "existing"))

    def first(copy_owner):
        retained["new-copy"] = 1
        copy_owner.entries.append((retained, "new-copy"))
        return True

    source.copy_reservations.extend([first, lambda copy_owner: False])
    assert source.reserve_copy() is None
    assert retained == {"source-a": 1, "source-b": 1, "existing": 1}
    source.release()
    existing.release()
    assert retained == {}


@pytest.mark.asyncio
async def test_replacement_keeps_old_snapshot_until_suspended_fetch_reserves_copy(
    peer, monkeypatch
):
    reads = SharedToolReads[dict](freshness_s=0)
    owners = [ResponseOwner() for _ in range(3)]
    paused, resume = asyncio.Event(), asyncio.Event()
    original_shield = asyncio.shield

    async def delayed_shield(task):
        result = await original_shield(task)
        if asyncio.current_task().get_name() == "late-fetch":
            paused.set()
            await resume.wait()
        return result

    monkeypatch.setattr(asyncio, "shield", delayed_shield)

    async def fetch(read, owner):
        token = response_owner.set(owner)
        try:
            return await read.fetch(
                lambda: PluginHub.send_command("owned-session", "get_test_job", {})
            )
        finally:
            response_owner.reset(token)

    async with reads.session("owned-job") as read:
        first = asyncio.create_task(fetch(read, owners[0]))
        await await_signal(peer.sent, "initial snapshot command dispatched", producers=(first,))
        late = asyncio.create_task(fetch(read, owners[1]), name="late-fetch")
        await asyncio.sleep(0.02)
        await peer.deliver()
        await first
        await await_signal(paused, "late fetch paused", producers=(late,))
        peer.sent.clear()
        fresh = asyncio.create_task(fetch(read, owners[2]))
        await await_signal(peer.sent, "replacement snapshot command dispatched", producers=(fresh,))
        # The replaced shared source remains charged for the suspended reader.
        assert len(PluginHub._retained_results) == 2
        await peer.deliver()
        await fresh
        resume.set()
        assert (await late)["success"]
    assert len(PluginHub._retained_results) == 3
    for owner in owners:
        owner.release()
    assert PluginHub._retained_results == {}


@pytest.mark.asyncio
async def test_large_shared_hub_copies_keep_source_charged(peer, monkeypatch):
    value = {"rows": [{"id": index, "label": "payload-" * 128} for index in range(256)]}
    monkeypatch.setattr(importlib.import_module(__name__), "RESULT", value)
    charge = response_size(value)
    for name in (
        "MAX_RETAINED_RESULT_BYTES",
        "MAX_RETAINED_RESULT_BYTES_PER_USER",
        "MAX_RETAINED_RESULT_BYTES_PER_SESSION",
    ):
        monkeypatch.setattr(PluginHub, name, int(charge * 2.8))
    reads = SharedToolReads(freshness_s=1)
    owners = [ResponseOwner(), ResponseOwner()]
    try:
        tasks = [asyncio.create_task(wait(reads, owner)) for owner in owners]
        await await_signal(peer.sent, "large shared command dispatched", producers=tasks)
        await peer.deliver()
        outputs = await asyncio.gather(*tasks, return_exceptions=True)
        assert outputs == [value, value]
        assert len(peer.messages) == 1
        outputs[0]["rows"].clear()
        assert outputs[1] == value
        owners[0].release()
        assert sum(entry["bytes"] for entry in PluginHub._retained_results.values()) >= charge
    finally:
        for owner in owners:
            owner.release()
    assert PluginHub._retained_results == {}
