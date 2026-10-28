"""Isolated prefab capture rejects invalid requests and never replays callbacks on reload."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from models import MCPResponse
from services.tools import capture_prefab as module
from services.operation_permissions import Permission, required_permissions


@pytest.fixture
def boundary(monkeypatch):
    send = AsyncMock(
        return_value={
            "success": True,
            "data": {"cleanup_completed": True, "imageBase64": "aW1hZ2U="},
        }
    )
    preflight = AsyncMock(return_value=None)
    monkeypatch.setattr(module, "send_with_unity_instance", send)
    monkeypatch.setattr(
        module, "get_unity_instance_from_context", AsyncMock(return_value="Fixture@hash")
    )
    monkeypatch.setattr(module, "preflight", preflight)
    return SimpleNamespace(), send, preflight


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path,width",
    [
        ("../bad.prefab", 128),
        ("Assets/../bad.prefab", 128),
        ("Assets/a.prefab", True),
        ("Assets/a.prefab", 4096),
    ],
)
async def test_invalid_capture_does_not_dispatch(boundary, path, width):
    ctx, send, preflight = boundary
    result = await module.capture_prefab(ctx, path, width=width)
    assert result["success"] is False
    send.assert_not_awaited()
    preflight.assert_not_awaited()


@pytest.mark.asyncio
async def test_capture_returns_image_with_root_metadata_and_no_automatic_replay(boundary):
    ctx, send, _ = boundary
    result = await module.capture_prefab(ctx, "Assets/a.prefab", width=128, height=256)
    assert result.structured_content == {
        "success": True,
        "message": "",
        "data": {"cleanup_completed": True},
    }
    assert result.content[1].type == "image"
    assert send.await_args.kwargs["retry_on_reload"] is False
    assert send.await_args.args[3] == {
        "prefab_path": "Assets/a.prefab",
        "width": 128,
        "height": 256,
    }
    assert required_permissions("capture_prefab", {}, native=True) == {Permission.EDIT}


@pytest.mark.asyncio
async def test_missing_cleanup_evidence_cannot_claim_success(boundary):
    ctx, send, _ = boundary
    send.return_value = {"success": True, "data": {"imageBase64": "aW1hZ2U="}}
    result = await module.capture_prefab(ctx, "Assets/a.prefab")
    assert result["success"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("typed", [False, True])
@pytest.mark.parametrize("reason", ["reloading", "operation_deadline_exceeded"])
async def test_capture_preserves_transport_refusal_without_replaying(boundary, typed, reason):
    ctx, send, _ = boundary
    response = MCPResponse(
        success=False,
        code=reason,
        error="Capture was not dispatched",
        hint="retry" if reason == "reloading" else None,
        data={"reason": reason, "retry_after_ms": 500, "not_executed": True},
    )
    expected = response.model_dump()
    send.return_value = response if typed else expected
    result = await module.capture_prefab(ctx, "Assets/a.prefab")
    assert result == expected
    send.assert_awaited_once()
    assert send.await_args.kwargs["retry_on_reload"] is False
