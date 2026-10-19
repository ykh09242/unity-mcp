"""Animation round trips route exact identities and reject malformed writes locally."""

import importlib
from unittest.mock import AsyncMock

import pytest

mod = importlib.import_module("services.tools.manage_animation")


@pytest.fixture
def bridge(monkeypatch):
    send = AsyncMock(return_value={"success": True, "data": {"revision": "current"}})
    monkeypatch.setattr(mod, "get_unity_instance_from_context", AsyncMock(return_value="fixture"))
    monkeypatch.setattr(mod, "send_with_unity_instance", send)
    return send


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "action,properties",
    [
        ("clip_get_curves", {"page_size": 1}),
        ("clip_get_curve", {"binding_id": "a" * 32, "offset": 1, "expected_revision": "current"}),
        (
            "clip_replace_curve",
            {
                "binding_id": "a" * 32,
                "expected_revision": "current",
                "keys": [{"time": 0, "value": {"guid": "b" * 32, "file_id": "21300000"}}],
            },
        ),
        ("clip_delete_curve", {"binding_id": "a" * 32, "expected_revision": "current"}),
        ("controller_get_graph", {"page_size": 50}),
        (
            "controller_modify_state",
            {"state_id": "110200001", "expected_revision": "current", "changes": {"speed": 2}},
        ),
        ("controller_delete_state", {"state_id": "110200001", "expected_revision": "current"}),
        (
            "controller_modify_transition",
            {
                "transition_id": "110100001",
                "expected_revision": "current",
                "changes": {"duration": 0.5},
            },
        ),
        (
            "controller_delete_transition",
            {"transition_id": "110100001", "expected_revision": "current"},
        ),
    ],
)
async def test_exact_round_trip_identity_and_revision_reach_transport(bridge, action, properties):
    result = await mod.manage_animation(
        None,
        action,
        clip_path="Assets/Clip.anim",
        controller_path="Assets/Graph.controller",
        properties=properties,
    )
    assert result["success"] is True
    assert bridge.call_args.args[3]["properties"] == properties
    assert bridge.call_args.args[3]["action"] == action


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "action,properties",
    [
        ("clip_replace_curve", {"binding_id": "curve", "keys": [{"time": 0, "value": 1}]}),
        ("clip_replace_curve", {"binding_id": "curve", "expected_revision": "current", "keys": []}),
        ("clip_get_curve", {"binding_id": True}),
        ("clip_get_curves", {"page_size": 101}),
        ("clip_get_curve", {"binding_id": "curve", "offset": 1}),
        (
            "controller_delete_transition",
            {"from_state": "Idle", "to_state": "Run", "expected_revision": "current"},
        ),
        (
            "controller_modify_state",
            {"state_id": "1102", "expected_revision": "current", "changes": {}},
        ),
        (
            "controller_modify_transition",
            {
                "transition_id": "1101",
                "expected_revision": "current",
                "changes": {"duration": float("nan")},
            },
        ),
    ],
)
async def test_invalid_round_trip_never_routes(bridge, action, properties):
    result = await mod.manage_animation(
        None,
        action,
        clip_path="Assets/Clip.anim",
        controller_path="Assets/Graph.controller",
        properties=properties,
    )
    assert result["success"] is False
    bridge.assert_not_awaited()
