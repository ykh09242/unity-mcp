"""The read-only package probe requires no settings asset or checkpoint."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from services.tools import manage_addressables as module


@pytest.mark.parametrize(
    "unexpected",
    [
        {"settings_path": "Assets/Owned.asset"},
        {"page_number": 2},
        {"page_number": True},
        {"operation_id": "a" * 32},
    ],
)
def test_capability_probe_rejects_other_operation_arguments(monkeypatch, unexpected):
    send = AsyncMock()
    monkeypatch.setattr(module, "send_with_unity_instance", send)
    response = asyncio.run(
        module.manage_addressables(SimpleNamespace(), action="capabilities", **unexpected)
    )
    assert response["success"] is False
    send.assert_not_awaited()


def test_capability_probe_uses_current_instance_without_settings_or_receipt(monkeypatch):
    data = {
        "success": True,
        "data": {
            "operations": {"inspect": {"available": False, "code": "addressables_unavailable"}}
        },
    }
    send = AsyncMock(return_value=data)
    monkeypatch.setattr(module, "send_with_unity_instance", send)
    monkeypatch.setattr(
        module, "get_unity_instance_from_context", AsyncMock(return_value="current")
    )
    assert asyncio.run(module.manage_addressables(SimpleNamespace(), action="capabilities")) == data
    assert send.await_args.args[1:] == (
        "current",
        "manage_addressables",
        {"action": "capabilities"},
    )
