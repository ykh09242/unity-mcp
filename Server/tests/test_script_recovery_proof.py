"""A changed file alone is not proof that a disconnected mutation succeeded."""

import importlib
from unittest.mock import AsyncMock

import pytest

refresh = importlib.import_module("services.tools.refresh_unity")
scripts = importlib.import_module("services.tools.manage_script")
structured = importlib.import_module("services.tools.script_apply_edits")


@pytest.mark.asyncio
@pytest.mark.parametrize("observed", ["b" * 64, "a" * 64, None])
async def test_changed_sha_without_expected_result_is_not_verified(monkeypatch, observed):
    send = AsyncMock(return_value={"success": True, "data": {"sha256": observed}})
    monkeypatch.setattr(refresh.unity_transport, "send_with_unity_instance", send)
    assert not await refresh.verify_edit_by_sha("Game@01234567", "Foo", "Assets", "a" * 64)


@pytest.mark.asyncio
@pytest.mark.parametrize("observed,verified", [("b" * 64, True), ("c" * 64, False), (None, False)])
async def test_verification_requires_the_exact_expected_result(monkeypatch, observed, verified):
    send = AsyncMock(return_value={"success": True, "data": {"sha256": observed}})
    monkeypatch.setattr(refresh.unity_transport, "send_with_unity_instance", send)
    assert (
        await refresh.verify_edit_by_sha(
            "Game@01234567", "Foo", "Assets", "a" * 64, expected_sha="b" * 64
        )
        is verified
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["text", "structured"])
async def test_disconnected_public_edit_reports_unconfirmed_without_replay(monkeypatch, kind):
    async def disconnected(ctx, instance, command, params, *, verify_after_disconnect):
        return await verify_after_disconnect()

    for module in (scripts, structured):
        monkeypatch.setattr(
            module, "get_unity_instance_from_context", AsyncMock(return_value="Game@01234567")
        )
        monkeypatch.setattr(module, "send_mutation", disconnected)
        monkeypatch.setattr(
            module,
            "send_with_unity_instance",
            AsyncMock(return_value={"success": True, "data": {"sha256": "a" * 64}}),
        )
    monkeypatch.setattr(
        refresh.unity_transport,
        "send_with_unity_instance",
        AsyncMock(return_value={"success": True, "data": {"sha256": "b" * 64}}),
    )
    if kind == "text":
        result = await scripts.apply_text_edits(
            AsyncMock(),
            "Assets/Foo.cs",
            [{"startLine": 1, "startCol": 1, "endLine": 1, "endCol": 1, "newText": "// requested"}],
            precondition_sha256="a" * 64,
        )
    else:
        result = await structured.script_apply_edits(
            AsyncMock(),
            "Foo",
            "Assets",
            [{"op": "insert_method", "replacement": "void Added() {}"}],
        )
    assert result["success"] is False
    assert result["data"]["execution_status"] == "unknown"
    assert result["data"]["retry_safe"] is False
