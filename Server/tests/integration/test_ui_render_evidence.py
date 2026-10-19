"""Public render evidence and bounded warmup contract; native pixels live in Unity tests."""

import asyncio

import pytest

import services.tools.manage_ui as manage_ui_mod
from .test_helpers import DummyContext


@pytest.mark.parametrize("value", [-1, 5, True, 1.5, "2", [], {}])
def test_invalid_warmup_refuses_before_transport(monkeypatch, value):
    async def unexpected(*_args, **_kwargs):
        raise AssertionError("invalid warmup reached I/O")

    monkeypatch.setattr(manage_ui_mod, "get_unity_instance_from_context", unexpected)
    monkeypatch.setattr(manage_ui_mod, "send_mutation", unexpected)
    response = asyncio.run(
        manage_ui_mod.manage_ui(
            ctx=DummyContext(), action="render_ui", target="UIRoot", warmup_updates=value
        )
    )
    assert response["success"] is False
    assert "warmup_updates" in response["message"]


@pytest.mark.parametrize("action", ["get_visual_tree", "diagnose", "create"])
def test_warmup_is_render_only(monkeypatch, action):
    async def unexpected(*_args, **_kwargs):
        raise AssertionError("wrong-action warmup reached I/O")

    monkeypatch.setattr(manage_ui_mod, "get_unity_instance_from_context", unexpected)
    response = asyncio.run(
        manage_ui_mod.manage_ui(
            ctx=DummyContext(), action=action, target="UIRoot", warmup_updates=0
        )
    )
    assert response == {"success": False, "message": "warmup_updates requires render_ui."}


@pytest.mark.parametrize("warmup", [None, 0, 1, 4])
def test_warmup_forwarding_and_native_evidence_passthrough(monkeypatch, warmup):
    sent = []
    native = {
        "success": True,
        "data": {
            "capture_source": "panel_render_texture",
            "full_game_view": False,
            "readiness": "bounded_editor_updates_elapsed",
            "hasContent": False,
            "content_check": {
                "method": "full_alpha_scan",
                "meaning": "image_alpha_occupancy",
                "pixels_examined": 4096,
                "nonzero_alpha_pixels": 0,
                "ui_presence": "not_determined",
            },
        },
    }

    async def instance(_ctx):
        return None

    async def send(_ctx, _instance, command, params, **_kwargs):
        assert command == "manage_ui"
        sent.append(params)
        return native

    monkeypatch.setattr(manage_ui_mod, "get_unity_instance_from_context", instance)
    monkeypatch.setattr(manage_ui_mod, "send_mutation", send)
    response = asyncio.run(
        manage_ui_mod.manage_ui(
            ctx=DummyContext(), action="render_ui", target="UIRoot", warmup_updates=warmup
        )
    )
    assert response == native
    if warmup is None:
        assert "warmup_updates" not in sent[0]
    else:
        assert sent[0]["warmup_updates"] == warmup


def test_owner_poll_and_cancel_preserve_id_without_inventing_settings(monkeypatch):
    sent = []

    async def instance(_ctx):
        return None

    async def send(_ctx, _instance, _command, params, **_kwargs):
        sent.append(params)
        return {"success": True, "data": {"capture_id": "owner", "pending": True}}

    monkeypatch.setattr(manage_ui_mod, "get_unity_instance_from_context", instance)
    monkeypatch.setattr(manage_ui_mod, "send_mutation", send)
    for cancel in (None, True):
        asyncio.run(
            manage_ui_mod.manage_ui(
                ctx=DummyContext(), action="render_ui", capture_id="owner", cancel_capture=cancel
            )
        )
    assert sent[0] == {"action": "render_ui", "capture_id": "owner"}
    assert sent[1] == {"action": "render_ui", "capture_id": "owner", "cancel_capture": True}
