"""A settled native batch starts one check over confirmed final source identities."""

from unittest.mock import AsyncMock

import pytest

import services.verification.batch as module


@pytest.mark.asyncio
async def test_partial_batch_coalesces_final_source_and_deletion_once(monkeypatch):
    commands = [
        {"tool": "manage_script", "params": {"action": action}}
        for action in ("update", "update", "delete", "update")
    ]
    data = {
        "executionStatus": "partial",
        "results": [
            {
                "tool": "manage_script",
                "result": {
                    "success": True,
                    "data": {"path": "Assets/A.cs", "bytes_sha256": "a" * 64},
                },
            },
            {
                "tool": "manage_script",
                "result": {
                    "success": True,
                    "data": {"path": "Assets/A.cs", "bytes_sha256": "b" * 64},
                },
            },
            {"tool": "manage_script", "result": {"success": True, "data": {"path": "Assets/B.cs"}}},
            {"tool": "manage_script", "result": {"success": False, "error": "rejected"}},
        ],
        "outcomes": [
            {"index": index, "state": "succeeded" if index < 3 else "failed"} for index in range(4)
        ],
    }
    response = {"success": False, "data": data}
    check = AsyncMock(return_value=response)
    monkeypatch.setattr(module, "complete_edit", check)
    result = await module.complete_batch(None, "Project@original", response, commands)
    assert result is response and result["success"] is False
    check.assert_awaited_once_with(
        None,
        "Project@original",
        response,
        ["Assets/A.cs"],
        expected_sources={
            "Assets/A.cs": {"bytes_sha256": "b" * 64},
            "Assets/B.cs": {"absent": True},
        },
    )
    assert data["batch_verification_selection"]["excluded_commands"] == 1


@pytest.mark.asyncio
async def test_unknown_or_unsupported_batch_does_not_start_checks(monkeypatch):
    check = AsyncMock()
    monkeypatch.setattr(module, "complete_edit", check)
    commands = [{"tool": "manage_components", "params": {"action": "add"}}]
    response = {
        "success": False,
        "data": {"results": [], "outcomes": [], "executionStatus": "partial"},
    }
    result = await module.complete_batch(None, "Project@original", response, commands)
    assert result["data"]["verification"]["complete"] is False
    check.assert_not_awaited()
