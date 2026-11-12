"""Derived graph observations retain the original mutation receipt and never replay writes."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from services.reference_graph.mutation_tracking import advance_receipt, tracked_mutation


def test_active_receipt_advances_once_and_returns_first_diff():
    service = SimpleNamespace(
        execute=AsyncMock(
            side_effect=[
                {"success": True, "data": {"receipt": {"id": "original", "state": "synchronized"}}},
                {"success": True, "data": {"changes": [{"kind": "changed"}], "next_cursor": None}},
            ]
        )
    )
    result = asyncio.run(
        advance_receipt(
            None, service, {"id": "original", "scope": "scope", "outcome": "accepted"}, 1
        )
    )
    assert result["id"] == "original"
    assert result["first_diff"]["changes"] == [{"kind": "changed"}]
    assert [call.args[1] for call in service.execute.await_args_list] == [
        "await_mutation",
        "mutation_diff",
    ]


def test_graph_failure_preserves_receipt_identity_and_outcome():
    service = SimpleNamespace(execute=AsyncMock(side_effect=RuntimeError("unavailable")))
    result = asyncio.run(
        advance_receipt(
            None, service, {"id": "original", "scope": "scope", "outcome": "accepted"}, 1
        )
    )
    assert result == {
        "id": "original",
        "scope": "scope",
        "outcome": "accepted",
        "state": "unavailable",
        "reason": "RuntimeError",
        "continue_action": "await_mutation",
    }


def test_one_deadline_covers_await_and_first_diff():
    async def execute(_ctx, action, _options):
        if action == "await_mutation":
            return {"success": True, "data": {"receipt": {"state": "synchronized"}}}
        await asyncio.sleep(1)

    service = SimpleNamespace(execute=execute)
    result = asyncio.run(
        advance_receipt(
            None, service, {"id": "original", "scope": "scope", "outcome": "accepted"}, 0.01
        )
    )
    assert result["id"] == "original"
    assert result["state"] == "pending"
    assert result["reason"] == "observation_deadline"


def test_opt_in_wait_never_activates_inactive_graph_or_replays_write(monkeypatch):
    monkeypatch.setattr(
        "services.reference_graph.mutation_tracking.active_service", lambda *_: None
    )
    saved = {"success": True, "data": {"saved": True}}
    invoke = AsyncMock(return_value=saved)
    result = asyncio.run(
        tracked_mutation(
            None,
            "selected",
            "manage_asset",
            {"action": "modify", "path": "Assets/Only.asset"},
            invoke,
            await_graph_seconds=0,
        )
    )
    assert result["success"] and result["data"]["saved"]
    assert result["data"]["reference_graph"] == {
        "state": "unavailable",
        "reason": "graph_not_active",
    }
    invoke.assert_awaited_once_with()
