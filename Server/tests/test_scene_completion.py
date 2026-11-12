"""Lifecycle verification preserves exact additive scene identities through transport."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from services.tools.manage_scene import manage_scene


def test_save_routes_exact_scene_and_preserves_verification(monkeypatch):
    monkeypatch.setattr(
        "services.tools.manage_scene.get_unity_instance_from_context",
        AsyncMock(return_value="owned"),
    )
    monkeypatch.setattr("services.tools.manage_scene.preflight", AsyncMock(return_value=None))
    reply = {
        "success": True,
        "data": {
            "scene_state": {"active": False},
            "verification": {"status": "incomplete", "continuation": "same-scene"},
        },
    }
    send = AsyncMock(return_value=reply)
    monkeypatch.setattr("services.tools.manage_scene.send_with_unity_instance", send)
    result = asyncio.run(manage_scene(SimpleNamespace(), "save", scene_handle=123, verify=True))
    assert result["data"] == reply["data"]
    assert send.call_args.args[3] == {"action": "save", "verify": True, "scene_handle": 123}


def test_invalid_verification_never_dispatches(monkeypatch):
    send = AsyncMock()
    monkeypatch.setattr("services.tools.manage_scene.send_with_unity_instance", send)
    result = asyncio.run(manage_scene(SimpleNamespace(), "get_active", verify=True))
    assert result["success"] is False
    send.assert_not_awaited()
