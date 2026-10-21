import importlib
from unittest.mock import AsyncMock

import pytest


@pytest.fixture
def batch(monkeypatch):
    module = importlib.import_module("services.tools.batch_execute")
    monkeypatch.setattr(module, "authorize_remote_operation", AsyncMock())
    monkeypatch.setattr(module, "get_unity_instance_from_context", AsyncMock(return_value="owned"))
    monkeypatch.setattr(module, "_get_max_commands_from_editor_state", AsyncMock(return_value=1000))
    return module


def receipt(commands, *, complete=True):
    return {
        "success": complete,
        "data": {
            "validation": {
                "contractVersion": 1,
                "validateOnly": True,
                "scope": "targets",
                "complete": complete,
                "valid": complete,
                "items": [
                    {
                        "index": i,
                        "tool": c["tool"],
                        "toolTruncated": False,
                        "status": "valid" if complete else "incomplete",
                        "reason": "ok" if complete else "unsupported_target_preview",
                    }
                    for i, c in enumerate(commands)
                ],
            },
            "preview": {
                "contractVersion": 1,
                "commandsHash": "A" * 44,
                "revisions": ["B" * 44 for _ in commands],
                "signature": "C" * 44,
            }
            if complete
            else None,
        },
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("complete", [True, False])
async def test_targets_preview_uses_dedicated_read_only_alias(batch, monkeypatch, complete):
    commands = [
        {
            "tool": "asset_edit",
            "params": {"action": "move", "path": "Assets/A", "destination": "Assets/B"},
        }
    ]
    reply = receipt(commands, complete=complete)
    send = AsyncMock(return_value=reply)
    monkeypatch.setattr(batch, "send_with_unity_instance", send)
    ctx = object()
    result = await batch.batch_execute(ctx, commands, validate_only=True, preview_scope="targets")
    assert result is reply
    assert send.call_count == 1
    assert send.call_args.args[2:] == (
        "batch_validate",
        {"commands": commands, "previewScope": "targets"},
    )
    batch.authorize_remote_operation.assert_awaited_once_with(
        "batch_execute", {"commands": commands}, ctx=ctx
    )


@pytest.mark.asyncio
async def test_guarded_apply_never_sends_to_legacy_execution_alias(batch, monkeypatch):
    commands = [
        {
            "tool": "gameobject_edit",
            "params": {"action": "modify", "target": 123, "position": [1, 2, 3]},
        }
    ]
    guard = {"contractVersion": 1, "signature": "native-opaque"}
    send = AsyncMock(return_value={"success": False, "error": "unknown_command"})
    monkeypatch.setattr(batch, "send_with_unity_instance", send)
    result = await batch.batch_execute(object(), commands, preview=guard)
    assert result["success"] is False
    assert send.call_count == 1
    assert send.call_args.args[2:] == (
        "batch_apply_preview",
        {"commands": commands, "preview": guard},
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["scope", "missing_item", "wrong_tool", "missing_guard"])
async def test_targets_preview_does_not_accept_partial_or_legacy_claims(batch, monkeypatch, change):
    commands = [
        {
            "tool": "asset_edit",
            "params": {"action": "move", "path": "Assets/A", "destination": "Assets/B"},
        }
    ]
    reply = receipt(commands)
    validation = reply["data"]["validation"]
    if change == "scope":
        validation["scope"] = "action_names"
    elif change == "missing_item":
        validation["items"] = []
    elif change == "wrong_tool":
        validation["items"][0]["tool"] = "other"
    else:
        reply["data"]["preview"] = None
    send = AsyncMock(return_value=reply)
    monkeypatch.setattr(batch, "send_with_unity_instance", send)
    result = await batch.batch_execute(
        object(), commands, validate_only=True, preview_scope="targets"
    )
    assert result["error"] == "batch_validation_unavailable"
    assert send.call_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "options",
    [
        {"preview_scope": "other"},
        {"preview_scope": "targets"},
        {"validate_only": True, "preview": {}},
        {"preview": "token"},
    ],
)
async def test_invalid_preview_modes_refuse_before_routing(batch, monkeypatch, options):
    send = AsyncMock()
    monkeypatch.setattr(batch, "send_with_unity_instance", send)
    with pytest.raises(ValueError):
        await batch.batch_execute(object(), [{"tool": "ping"}], **options)
    batch.get_unity_instance_from_context.assert_not_called()
    send.assert_not_called()


@pytest.mark.parametrize(
    "change", ["empty", "boolean_version", "missing_revision", "malformed_signature"]
)
def test_targets_receipt_requires_complete_guard_shape(batch, change):
    commands = [{"tool": "gameobject_edit"}]
    reply = receipt(commands)
    guard = reply["data"]["preview"]
    if change == "empty":
        guard.clear()
    elif change == "boolean_version":
        guard["contractVersion"] = True
    elif change == "missing_revision":
        guard["revisions"] = []
    else:
        guard["signature"] = None
    assert not batch._preview_reply_is_exact(reply, commands, "targets")


def test_guarded_alias_keeps_member_permissions_and_blocks_nesting():
    from services.operation_permissions import required_permissions, OperationPermissionError

    commands = [{"tool": "gameobject_edit", "params": {"action": "modify", "target": 123}}]
    assert required_permissions(
        "batch_apply_preview", {"commands": commands}, native=True
    ) == required_permissions("batch_execute", {"commands": commands}, native=True)
    with pytest.raises(OperationPermissionError):
        required_permissions(
            "batch_apply_preview",
            {"commands": [{"tool": "batch_apply_preview", "params": {"commands": commands}}]},
            native=True,
        )
