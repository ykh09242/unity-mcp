import asyncio
import time
from unittest.mock import AsyncMock

import pytest

from services import source_freshness as service
from tests.test_source_freshness_contracts import current


@pytest.fixture
def transport(monkeypatch):
    send = AsyncMock()
    monkeypatch.setattr(service.unity_transport, "send_with_unity_instance", send)
    monkeypatch.setattr(service, "remote_operation_allowed", AsyncMock(return_value=True))
    return send


async def ensure(**kwargs):
    return await service.ensure_source_freshness(
        object(),
        instance="project@exact",
        deadline=time.monotonic() + 1,
        refresh_policy="if_needed",
        **kwargs,
    )


@pytest.mark.asyncio
async def test_current_observation_does_not_refresh(transport):
    transport.return_value = {"success": True, "source_freshness": current()}
    result = await ensure()
    assert result.status == "observed_current"
    assert transport.await_args.args[1:3] == ("project@exact", "get_compile_job")
    assert transport.await_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("initial_missing_input", [False, True])
async def test_bootstrap_imports_then_requests_one_clean_cycle(transport, initial_missing_input):
    stale = {**current(), "status": "incomplete"}
    first = {**stale, "coverage": {**stale["coverage"], "complete": not initial_missing_input}}
    transport.side_effect = [
        {"success": True, "source_freshness": first},
        {"success": True},
        {"success": True, "source_freshness": stale},
        {"success": True},
        {"success": True, "source_freshness": current()},
    ]
    result = await ensure()
    assert result.status == "observed_current"
    calls = [c for c in transport.await_args_list if c.args[2] == "refresh_unity"]
    assert len(calls) == 2
    assert calls[0].args[3]["compile"] == "none"
    assert calls[1].args[3]["force_recompile"] is True
    assert all(c.kwargs["retry_on_reload"] is False for c in calls)


@pytest.mark.asyncio
async def test_uncertain_refresh_is_never_replayed(transport):
    transport.side_effect = [
        {"success": True, "source_freshness": {**current(), "status": "changed"}},
        TimeoutError(),
    ]
    assert (await ensure()).reason == "refresh_outcome_unknown"
    assert transport.await_count == 2


@pytest.mark.asyncio
async def test_denied_refresh_does_not_dispatch(transport, monkeypatch):
    monkeypatch.setattr(
        service,
        "remote_operation_allowed",
        AsyncMock(side_effect=lambda command, *_a, **_kw: command == "get_compile_job"),
    )
    transport.return_value = {
        "success": True,
        "source_freshness": {**current(), "status": "changed"},
    }
    assert (await ensure()).reason == "refresh_permission_required"
    assert transport.await_count == 1


@pytest.mark.asyncio
async def test_editor_restart_invalidates_observation(transport):
    transport.side_effect = [
        {"success": True, "source_freshness": {**current(), "status": "pending"}},
        {"success": True, "source_freshness": {**current(), "session_id": "other"}},
    ]
    assert (await ensure()).reason == "editor_identity_changed"


@pytest.mark.asyncio
async def test_old_peer_is_unsupported_without_refresh(transport):
    transport.return_value = {"success": True, "data": {}}
    assert (await ensure()).reason == "unsupported"
    assert transport.await_count == 1


@pytest.mark.asyncio
async def test_one_deadline_bounds_observation(transport):
    async def slow(*_a, **_kw):
        await asyncio.sleep(60)

    transport.side_effect = slow
    result = await service.ensure_source_freshness(
        object(), instance="exact", deadline=time.monotonic() + 0.01, refresh_policy="never"
    )
    assert result.reason == "observation_deadline"
    assert transport.await_count == 1


@pytest.mark.asyncio
async def test_permission_observation_uses_the_same_deadline(transport, monkeypatch):
    async def slow_permission(*_a, **_kw):
        await asyncio.sleep(60)

    monkeypatch.setattr(service, "remote_operation_allowed", slow_permission)
    result = await asyncio.wait_for(
        service.ensure_source_freshness(
            object(), instance="exact", deadline=time.monotonic() + 0.01, refresh_policy="never"
        ),
        0.2,
    )
    assert result.reason == "observation_deadline"
    transport.assert_not_awaited()
