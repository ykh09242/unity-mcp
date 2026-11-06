"""Tool boundaries preserve recovery evidence from either supported transport envelope."""

from importlib import import_module
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from models import MCPResponse


@pytest.mark.parametrize("name", ["navigation", "netcode", "localization", "timeline"])
@pytest.mark.parametrize("as_model", [False, True])
@pytest.mark.parametrize(
    "payload",
    [
        {"success": True, "message": "Observed", "data": {"ready": False}},
        {
            "success": False,
            "code": "completed_result_not_retained",
            "error": "Original Editor continuation is unavailable",
            "hint": "inspect_state_before_retry",
            "data": {"reason": "outcome_unknown", "execution": "completed"},
        },
        {
            "success": False,
            "code": 0,
            "error": "Editor is reloading",
            "hint": "retry",
            "data": {"retry_after_ms": 0},
        },
    ],
)
@pytest.mark.asyncio
async def test_supported_transport_envelopes_keep_machine_readable_evidence(
    monkeypatch, name, as_model, payload
):
    module = import_module(f"services.tools.manage_{name}")
    response = MCPResponse(**payload) if as_model else payload
    sender = AsyncMock(return_value=response)
    monkeypatch.setattr(module, "get_unity_instance_from_context", AsyncMock(return_value="owned"))
    monkeypatch.setattr(module, "send_with_unity_instance", sender)

    result = await getattr(module, f"manage_{name}")(SimpleNamespace(), action="capabilities")

    assert result == payload
    assert sender.await_count == 1


@pytest.mark.asyncio
async def test_localization_mutation_preserves_uncertain_model_response(monkeypatch):
    module = import_module("services.tools.manage_localization")
    payload = {
        "success": False,
        "error": "Original outcome is unavailable",
        "hint": "inspect_state_before_retry",
        "data": {"reason": "outcome_unknown"},
    }
    sender = AsyncMock(return_value=MCPResponse(**payload))
    monkeypatch.setattr(module, "get_unity_instance_from_context", AsyncMock(return_value="owned"))
    monkeypatch.setattr(module, "send_mutation", sender)

    result = await module.manage_localization(
        SimpleNamespace(),
        action="set_fallbacks",
        locale_path="Assets/Locale.asset",
        fallback_locale_paths=[],
        expected_revision="observed",
    )

    assert result == payload
    assert sender.await_count == 1


@pytest.mark.parametrize("name", ["navigation", "netcode", "localization", "timeline"])
@pytest.mark.parametrize("response", [None, "broken", []])
@pytest.mark.asyncio
async def test_malformed_transport_envelopes_remain_failures(monkeypatch, name, response):
    module = import_module(f"services.tools.manage_{name}")
    monkeypatch.setattr(module, "get_unity_instance_from_context", AsyncMock(return_value="owned"))
    monkeypatch.setattr(module, "send_with_unity_instance", AsyncMock(return_value=response))

    result = await getattr(module, f"manage_{name}")(SimpleNamespace(), action="capabilities")

    assert result["success"] is False
