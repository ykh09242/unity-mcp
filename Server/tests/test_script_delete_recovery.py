"""Recovery must prove absence instead of treating a failed read as deletion."""

import importlib
from unittest.mock import AsyncMock

import pytest

scripts = importlib.import_module("services.tools.manage_script")
recovery = importlib.import_module("services.tools.refresh_unity")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "encoded_directory,expected_directory",
    [
        ("%252e%252e/Assets", "Assets/%2e%2e/Assets"),
        ("Scripts%252fNested", "Assets/Scripts%2fNested"),
    ],
)
async def test_delete_file_uri_decodes_percent_escapes_once(
    monkeypatch, encoded_directory, expected_directory
):
    # Given a file URI whose folder contains literal percent-encoded characters.
    sender = AsyncMock(return_value={"success": True})
    monkeypatch.setattr(scripts, "get_unity_instance_from_context", AsyncMock(return_value=None))
    monkeypatch.setattr(scripts, "send_mutation", sender)
    # When deleting through the public Python entrypoint.
    response = await scripts.delete_script(
        None, f"file:///C:/Project/Assets/{encoded_directory}/Foo.cs"
    )
    # Then the wire command addresses the literal directory, without decoding it twice.
    assert response["success"] is True
    assert sender.await_args.args[3] == {
        "action": "delete",
        "name": "Foo",
        "path": expected_directory,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("entrypoint", ["delete_script", "manage_script"])
@pytest.mark.parametrize(
    "verification,deleted",
    [
        ({"success": False, "error": "Timeout waiting for command response"}, False),
        (
            {
                "success": False,
                "error": "Failed to read script 'Assets/Scripts/Foo.cs': access denied",
            },
            False,
        ),
        ({"success": False, "data": {"reason": "reloading"}}, False),
        ({"success": True, "data": {"contents": "still present"}}, False),
        ({"success": False, "error": "Script not found at 'Assets/Scripts/Foo.cs'."}, True),
    ],
)
async def test_delete_recovery_requires_explicit_absence(
    monkeypatch, entrypoint, verification, deleted
):
    # Given: the actual mutation recovery path sees a disconnect followed by a read.
    disconnect = {"success": False, "error": "connection closed"}
    sender = AsyncMock(side_effect=[disconnect, verification])
    monkeypatch.setattr(scripts, "get_unity_instance_from_context", AsyncMock(return_value=None))
    monkeypatch.setattr(scripts, "send_with_unity_instance", sender)
    monkeypatch.setattr(recovery.unity_transport, "send_with_unity_instance", sender)
    monkeypatch.setattr(recovery, "wait_for_editor_ready", AsyncMock(return_value=(True, 0)))
    # When: deletion is invoked through either supported Python entrypoint.
    if entrypoint == "delete_script":
        response = await scripts.delete_script(AsyncMock(), "Assets/Scripts/Foo.cs")
    else:
        response = await scripts.manage_script(AsyncMock(), "delete", "Foo", "Assets/Scripts")
    # Then: success requires the distinct missing-file signal, preserving uncertainty otherwise.
    assert response["success"] is deleted
    if not deleted:
        assert response == disconnect
    assert sender.await_count == 2
