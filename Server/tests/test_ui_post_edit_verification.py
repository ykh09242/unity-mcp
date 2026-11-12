"""UI observation selection remains explicit and never causes a second mutation."""

import asyncio
import importlib
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest


def test_source_verification_passes_explicit_binding_once(monkeypatch):
    module = importlib.import_module("services.tools.manage_ui")
    response = {"success": True, "data": {"verification": {"status": "pending"}}}
    monkeypatch.setattr(module, "get_unity_instance_from_context", AsyncMock(return_value="owner"))
    send = AsyncMock(return_value=response)
    monkeypatch.setattr(module, "send_mutation", send)
    result = asyncio.run(
        module.manage_ui(
            SimpleNamespace(),
            action="update",
            path="Assets/UI/A.uss",
            contents="",
            verify_target="123",
            component_type="ui_document",
        )
    )
    assert result == response
    send.assert_awaited_once()
    assert send.await_args.args[3]["verify_target"] == "123"


@pytest.mark.parametrize("action,target", [("read", "123"), ("update", ""), ("update", "x" * 4097)])
def test_source_verification_rejects_invalid_selection_before_dispatch(monkeypatch, action, target):
    module = importlib.import_module("services.tools.manage_ui")
    send = AsyncMock()
    monkeypatch.setattr(module, "send_mutation", send)
    result = asyncio.run(
        module.manage_ui(
            SimpleNamespace(),
            action=action,
            path="Assets/UI/A.uss",
            contents="",
            verify_target=target,
        )
    )
    assert result["success"] is False
    send.assert_not_awaited()
