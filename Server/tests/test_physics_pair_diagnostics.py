"""Portable admission and real SDK-schema tests for read-only collider-pair diagnosis."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import anyio
import pytest

from services.tools.manage_physics import manage_physics


@pytest.fixture
def transport(monkeypatch):
    sender = AsyncMock(return_value={"success": True, "data": {"dimension": "2d"}})
    monkeypatch.setattr("services.tools.manage_physics.send_with_unity_instance", sender)
    monkeypatch.setattr(
        "services.tools.manage_physics.get_unity_instance_from_context",
        AsyncMock(return_value="owned"),
    )
    return sender


def invoke(**kwargs):
    async def run():
        return await manage_physics(SimpleNamespace(), **kwargs)

    return anyio.run(run)


def test_explicit_pair_selection_reaches_native_without_losing_zero_false(transport):
    # Given explicit selections for two potentially compound objects.
    result = invoke(
        action="diagnose_pair",
        target="1",
        other_target="2",
        dimension="2d",
        collider_type="BoxCollider2D",
        component_index=0,
        other_collider_type="CircleCollider2D",
        other_component_index=0,
        other_search_method="by_id",
        contact_limit=8,
        observe_contacts=False,
    )
    # When the ordinary tool adapter forwards them, then all selection/observation values survive.
    assert result["success"]
    assert transport.call_args.args[3] == {
        "action": "diagnose_pair",
        "target": "1",
        "other_target": "2",
        "dimension": "2d",
        "collider_type": "BoxCollider2D",
        "componentIndex": 0,
        "other_collider_type": "CircleCollider2D",
        "otherComponentIndex": 0,
        "other_search_method": "by_id",
        "contact_limit": 8,
        "observe_contacts": False,
    }


@pytest.mark.parametrize(
    "options",
    [
        {},
        {"target": "1"},
        {"target": "1", "other_target": ""},
        {"target": "1", "other_target": "2", "dimension": "both"},
        {"target": "1", "other_target": "2", "component_index": -1},
        {"target": "1", "other_target": "2", "other_component_index": True},
        {"target": "1", "other_target": "2", "contact_limit": 129},
        {"target": "1", "other_target": "2", "contact_limit": 0},
        {"target": "1", "other_target": "2", "observe_contacts": " true "},
    ],
)
def test_invalid_pair_request_is_refused_before_transport(transport, options):
    # Given an incomplete or malformed pair selection.
    result = invoke(action="diagnose_pair", **options)
    # When admission runs, then no native diagnostic or observer is started.
    assert not result["success"]
    transport.assert_not_awaited()


def test_registered_sdk_accepts_pair_action_and_preserves_omitted_observer(transport):
    from fastmcp import Client, FastMCP

    server = FastMCP("physics-pair-contract")
    server.tool(manage_physics)

    async def run():
        async with Client(server) as client:
            # Given a real registered SDK schema, when a read-only pair diagnosis is requested.
            result = await client.call_tool(
                "manage_physics",
                {
                    "action": "diagnose_pair",
                    "target": "1",
                    "other_target": "2",
                },
            )
            assert not result.is_error

    anyio.run(run)
    # Then no observer opt-in or new default is silently inserted.
    assert transport.call_args.args[3] == {
        "action": "diagnose_pair",
        "target": "1",
        "other_target": "2",
    }


def test_legacy_action_defaults_are_unchanged(transport):
    # Given an existing request, when the wrapper forwards it, then no pair options are introduced.
    assert invoke(action="get_settings")["success"]
    assert transport.call_args.args[3] == {"action": "get_settings"}
