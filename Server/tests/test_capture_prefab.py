"""Isolated prefab capture rejects invalid requests and never replays callbacks on reload."""

from types import SimpleNamespace
from typing import get_type_hints
from unittest.mock import AsyncMock

import pytest
from pydantic import TypeAdapter, ValidationError

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


@pytest.mark.asyncio
@pytest.mark.parametrize("parent", [0, True, "123", 1.5, 2147483648, -2147483649, {}])
async def test_invalid_ui_parent_is_rejected_before_dispatch(boundary, parent):
    ctx, send, preflight = boundary
    result = await module.capture_prefab(ctx, "Assets/a.prefab", ui_parent=parent)
    assert result["success"] is False
    send.assert_not_awaited()
    preflight.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("parent", [-1234, 1234, None])
async def test_parent_context_and_provenance_survive_image_conversion(boundary, parent):
    ctx, send, _ = boundary
    metadata = {
        "source": "scene_parent" if parent else "default_canvas",
        "scale_factor": 2.0 if parent else 1.0,
        "reference_resolution": {"x": 128.0, "y": 128.0} if parent else None,
        "parent_instance_id": parent,
        "framing": "full_viewport",
    }
    send.return_value["data"].update(
        ui_context=metadata,
        limitations=["Parent layout is a static RectTransform snapshot."],
    )
    result = await module.capture_prefab(ctx, "Assets/a.prefab", ui_parent=parent)
    assert result.structured_content["data"]["ui_context"] == metadata
    assert result.structured_content["data"]["limitations"] == [
        "Parent layout is a static RectTransform snapshot."
    ]
    assert result.content[1].type == "image"
    payload = send.await_args.args[3]
    if parent is None:
        assert "ui_parent" not in payload
    else:
        assert payload["ui_parent"] == parent
    assert send.await_args.kwargs["retry_on_reload"] is False


def test_ui_parent_schema_preserves_integer_identity_and_null():
    annotation = get_type_hints(module.capture_prefab, include_extras=True)["ui_parent"]
    adapter = TypeAdapter(annotation)
    for value in (None, -2147483648, -1234, 1234, 2147483647):
        assert adapter.validate_python(value) == value
    for value in (True, "1234", 1.0, 2147483648, -2147483649):
        with pytest.raises(ValidationError):
            adapter.validate_python(value)
