"""REST retains dispatch knowledge across selection/admission disconnect races."""

import pytest

from tests.http.test_local_project_context import environment  # pylint: disable=unused-import
from tests.http.test_plugin_session_targeting_and_eviction import (
    hub_environment,  # pylint: disable=unused-import
)


@pytest.mark.asyncio
async def test_rest_disconnect_after_snapshot_reports_not_dispatched(environment, monkeypatch):
    # Given: a real REST route and Hub whose selected peer disappears after its snapshot.
    hub, _, a, b, client = environment
    get_sessions = hub.get_sessions

    async def snapshot_then_disconnect(*args, **kwargs):
        snapshot = await get_sessions(*args, **kwargs)
        await a.endpoint.on_disconnect(a.websocket, 1006)
        return snapshot

    monkeypatch.setattr(hub, "get_sessions", snapshot_then_disconnect)
    # When: selection succeeds using the old snapshot, then real command admission runs.
    response = await client.post(
        "/api/command", json={"type": "owned_mutation", "unity_instance": a.project_hash}
    )
    # Then: the response retains definite no-dispatch evidence and same-target retry guidance.
    assert response.status_code == 503, response.text
    result = response.json()
    assert result["success"] is False and result["hint"] == "retry"
    assert result["data"] == {
        "reason": "command_not_dispatched",
        "dispatch": "not_dispatched",
        "command": "owned_mutation",
        "unity_instance": a.project_hash,
    }
    assert a.executes == b.executes == [] and hub._pending == {}


@pytest.mark.asyncio
async def test_rest_disconnect_after_send_preserves_unknown_outcome(environment):
    # Given: the peer disconnects after observing the actual execute frame.
    hub, _, a, b, client = environment

    async def disconnect():
        await a.endpoint.on_disconnect(a.websocket, 1006)

    a.before_reply = disconnect
    # When: a command may have executed, but its response is lost with the connection.
    response = await client.post(
        "/api/command", json={"type": "owned_mutation", "unity_instance": a.project_hash}
    )
    # Then: no automatic replay or target switch occurs, and uncertainty stays explicit.
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["success"] is False and result["hint"] == "inspect_state_before_retry"
    assert result["data"]["reason"] == "outcome_unknown"
    assert result["data"].get("dispatch") != "not_dispatched"
    assert len(a.executes) == 1 and b.executes == [] and hub._pending == {}
