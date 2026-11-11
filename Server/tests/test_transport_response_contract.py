"""Public wrappers retain typed transport recovery responses as structured data."""

import importlib
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from core.config import config
from models.models import MCPResponse
from transport import unity_transport


@pytest.mark.asyncio
@pytest.mark.parametrize("transport", ["stdio", "http"])
@pytest.mark.parametrize("tool", ["execute_code", "manage_audio"])
@pytest.mark.parametrize(
    ("reason", "hint", "success"),
    [
        ("outcome_unknown", "inspect_state_before_retry", False),
        ("read_invalidated", "inspect_state_before_retry", False),
        ("instance_selection_required", "select_instance", False),
        ("command_not_dispatched", "retry", False),
        ("completed", None, True),
    ],
)
async def test_public_wrapper_preserves_model_and_dictionary_responses(
    monkeypatch, transport, tool, reason, hint, success
):
    # Given: either transport delivers the same response as a model or dictionary.
    module = importlib.import_module(f"services.tools.{tool}")
    monkeypatch.setattr(config, "transport_mode", transport)
    monkeypatch.setattr(config, "http_remote_hosted", False)
    monkeypatch.setattr(module, "get_unity_instance_from_context", AsyncMock(return_value="P@id"))
    response = MCPResponse(
        success=success,
        code=reason,
        error=None if success else "Recovery required",
        hint=hint,
        data={"reason": reason, "job_id": "owned-job", "value": False},
    )
    send = AsyncMock()
    monkeypatch.setattr(module, "async_send_command_with_retry", send)
    monkeypatch.setattr(unity_transport.PluginHub, "send_command_for_instance", send)
    params = (
        {"action": "get_history"}
        if tool == "execute_code"
        else {"action": "diagnose", "target": 42}
    )
    results = []

    # When: the real public wrapper consumes each representation through its transport.
    for representation in (response.model_dump(), response):
        send.return_value = representation
        results.append(await getattr(module, tool)(SimpleNamespace(), **params))

    # Then: every recovery field and successful false value remains structured and equivalent.
    assert results[0] == results[1]
    assert results[1]["success"] is success
    assert results[1]["code"] == reason
    assert results[1]["error"] == response.error
    assert results[1]["hint"] == hint
    assert results[1]["data"] == response.data


@pytest.mark.asyncio
@pytest.mark.parametrize("transport", ["stdio", "http"])
@pytest.mark.parametrize("preserve", [False, True])
@pytest.mark.parametrize("typed", [False, True])
async def test_private_preflight_preservation_never_reaches_native_dispatch(
    monkeypatch, transport, preserve, typed
):
    monkeypatch.setattr(config, "transport_mode", transport)
    monkeypatch.setattr(config, "http_remote_hosted", False)
    response = MCPResponse(success=False, hint="retry", data={"reason": "reloading"})
    raw = response if typed else response.model_dump()
    send = AsyncMock(return_value=raw)
    monkeypatch.setattr(unity_transport.PluginHub, "send_command_for_instance", send)
    params = {"action": "create", "name": "Owned"}
    result = await unity_transport.send_with_unity_instance(
        send,
        "P@id",
        "manage_gameobject",
        params,
        retry_on_reload=False,
        _preserve_local_response=preserve,
    )
    if transport == "stdio" and preserve:
        assert result is raw
    else:
        assert result == response.model_dump()
    expected_args = ("manage_gameobject", params)
    expected_kwargs = {"retry_on_reload": False, "instance_id": "P@id"}
    if transport == "http":
        expected_args = ("P@id", *expected_args)
        expected_kwargs = {"user_id": None, "retry_on_reload": False}
    send.assert_awaited_once_with(*expected_args, **expected_kwargs)
    assert params == {"action": "create", "name": "Owned"}


@pytest.mark.asyncio
@pytest.mark.parametrize("value", [None, False, 0, "unexpected", [1, 2]])
async def test_stdio_boundary_does_not_coerce_arbitrary_values(monkeypatch, value):
    # Given: a legacy transport returns a non-response value.
    monkeypatch.setattr(config, "transport_mode", "stdio")
    send = AsyncMock(return_value=value)
    # When: the public boundary receives it.
    result = await unity_transport.send_with_unity_instance(send, "P@id", "fixture", {})
    # Then: only actual MCPResponse instances are normalized.
    assert result is value
