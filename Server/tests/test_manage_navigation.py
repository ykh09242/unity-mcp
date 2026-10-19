"""Navigation transport controls preserve explicit identity and filter values."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from services.tools.manage_navigation import ALL_ACTIONS, manage_navigation


@pytest.fixture
def navigation_transport(monkeypatch):
    captured = {}

    async def send(_send, instance, command, params):
        captured.update(instance=instance, command=command, params=params)
        return {"success": True, "data": {"state": "queued", "job_id": "owned"}}

    monkeypatch.setattr(
        "services.tools.manage_navigation.get_unity_instance_from_context",
        AsyncMock(return_value="selected-editor"),
    )
    monkeypatch.setattr("services.tools.manage_navigation.send_with_unity_instance", send)
    return captured


def test_path_preserves_scene_agent_and_zero_area(navigation_transport):
    result = asyncio.run(
        manage_navigation(
            SimpleNamespace(),
            action="calculate_path",
            scene_handle=71,
            agent_type_id=0,
            area_mask=0,
            start=[-5, 0, 0],
            end=[5, 0, 0],
            max_distance=1.5,
        )
    )
    assert result["success"]
    assert navigation_transport == {
        "instance": "selected-editor",
        "command": "manage_navigation",
        "params": {
            "action": "calculate_path",
            "scene_handle": 71,
            "agent_type_id": 0,
            "area_mask": 0,
            "start": [-5, 0, 0],
            "end": [5, 0, 0],
            "max_distance": 1.5,
        },
    }


def test_configuration_retains_false_and_vector(navigation_transport):
    properties = {"startPoint": [1, 0, 0], "endPoint": [4, 0, 0], "bidirectional": False}
    asyncio.run(
        manage_navigation(
            SimpleNamespace(),
            action="configure",
            scene_handle=71,
            kind="link",
            target=-84,
            properties=properties,
        )
    )
    assert navigation_transport["params"]["properties"] == properties
    assert navigation_transport["params"]["target"] == -84


@pytest.mark.parametrize("action", ALL_ACTIONS)
def test_action_envelopes_use_normal_transport(action, navigation_transport):
    asyncio.run(manage_navigation(SimpleNamespace(), action=action, job_id="owned"))
    assert navigation_transport["params"] == {"action": action, "job_id": "owned"}


@pytest.mark.parametrize("limit,offset", [(0, 0), (101, 0), (True, 0), (1, -1), (1, True)])
def test_page_bounds_refuse_before_transport(limit, offset, navigation_transport):
    result = asyncio.run(
        manage_navigation(SimpleNamespace(), action="list", limit=limit, offset=offset)
    )
    assert result["success"] is False
    assert navigation_transport == {}


def test_unknown_action_refuses_before_transport(navigation_transport):
    result = asyncio.run(manage_navigation(SimpleNamespace(), action="execute_code"))
    assert result["success"] is False
    assert navigation_transport == {}


def test_transport_failure_is_preserved(monkeypatch, navigation_transport):
    refusal = {"success": False, "error": "ai_navigation_package_unavailable"}
    monkeypatch.setattr(
        "services.tools.manage_navigation.send_with_unity_instance", AsyncMock(return_value=refusal)
    )
    assert asyncio.run(manage_navigation(SimpleNamespace(), action="bake")) == refusal
