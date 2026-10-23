from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from services.tools.manage_camera import manage_camera


@pytest.mark.asyncio
@pytest.mark.parametrize("seconds", [300, 7200, 86400])
async def test_scene_capture_carries_its_budget_without_reload_replay(monkeypatch, seconds):
    send = AsyncMock(return_value={"success": True})
    monkeypatch.setattr(
        "services.tools.manage_camera.get_unity_instance_from_context",
        AsyncMock(return_value="owned"),
    )
    monkeypatch.setattr("services.tools.manage_camera.send_with_unity_instance", send)
    result = await manage_camera(
        SimpleNamespace(), action="screenshot", capture_source="scene_view", timeout_seconds=seconds
    )
    assert result["success"] is True
    assert send.call_args.args[3]["timeout_seconds"] == seconds
    assert send.call_args.kwargs["retry_on_reload"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("seconds", [0, -1, 86401, True, 1.5, "300", float("inf")])
async def test_scene_capture_invalid_budget_never_resolves_or_dispatches(monkeypatch, seconds):
    resolve = AsyncMock()
    send = AsyncMock()
    monkeypatch.setattr("services.tools.manage_camera.get_unity_instance_from_context", resolve)
    monkeypatch.setattr("services.tools.manage_camera.send_with_unity_instance", send)
    result = await manage_camera(
        SimpleNamespace(), action="screenshot", capture_source="scene_view", timeout_seconds=seconds
    )
    assert result["success"] is False
    resolve.assert_not_called()
    send.assert_not_called()
