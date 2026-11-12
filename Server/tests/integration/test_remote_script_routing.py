"""Remote script reads and writes must never use host-local Unity sockets."""

import importlib
import hashlib
from unittest.mock import AsyncMock, Mock

import pytest

from core.config import config
from tests.remote_credentials import remote_credentials
from transport import unity_transport
from transport.legacy import unity_connection

edits_tool = importlib.import_module("services.tools.script_apply_edits")


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["preview", "text", "structured", "mixed"])
async def test_script_operations_stay_in_authenticated_plugin_session(monkeypatch, mode):
    monkeypatch.setattr(config, "transport_mode", "http")
    monkeypatch.setattr(config, "http_remote_hosted", True)
    monkeypatch.setattr(
        unity_transport, "_resolve_user_id_from_request", AsyncMock(return_value="tenant-a")
    )
    legacy = Mock(side_effect=AssertionError("Host-local Unity must not be accessed"))
    monkeypatch.setattr(unity_connection, "get_unity_connection_pool", legacy)
    calls = []
    source = "class Foo {}\n"

    async def plugin(instance, command, params, **kwargs):
        nonlocal source
        assert instance == "SameName@samehash"
        assert kwargs["user_id"] == "tenant-a"
        assert command == "manage_script"
        action = params["action"]
        calls.append(action)
        if action in {"preview_text_edits", "preview_edit"}:
            from tests.test_script_preparation import prepared

            candidate = (
                edits_tool._preview_text_spans(source, params["edits"])
                if action == "preview_text_edits"
                else source.replace("class Foo {}", "class Foo { void M() {} }")
            )
            return prepared(source, candidate)
        if action in {"apply_text_edits", "edit"}:
            assert params["precondition_sha256"] == hashlib.sha256(source.encode()).hexdigest()
            source = (
                edits_tool._preview_text_spans(source, params["edits"])
                if action == "apply_text_edits"
                else source.replace("class Foo {}", "class Foo { void M() {} }")
            )
        return {
            "success": True,
            "data": {"contents": source, "sha256": hashlib.sha256(source.encode()).hexdigest()},
        }

    monkeypatch.setattr(unity_transport.PluginHub, "send_command_for_instance", plugin)
    ctx = AsyncMock()
    ctx.get_state.return_value = "SameName@samehash"
    text = {
        "op": "replace_range",
        "startLine": 1,
        "startCol": 1,
        "endLine": 1,
        "endCol": 1,
        "text": "// remote edit\n",
    }
    structured = {"op": "insert_method", "className": "Foo", "replacement": "void M() {}"}
    edits = (
        [structured] if mode == "structured" else [text, structured] if mode == "mixed" else [text]
    )
    async with remote_credentials({"requester:tenant-a": "tenant-a"}) as auth:
        result = await auth.call(
            "requester:tenant-a",
            edits_tool.script_apply_edits,
            ctx,
            "Foo",
            "Assets",
            edits,
            {"preview": mode == "preview"},
        )
    assert result["success"], result
    expected_calls = {
        "preview": ["read", "preview_text_edits"],
        "text": ["read", "preview_text_edits", "apply_text_edits"],
        "structured": ["preview_edit", "edit"],
        "mixed": [
            "validate_edit",
            "read",
            "preview_text_edits",
            "apply_text_edits",
            "preview_edit",
            "edit",
        ],
    }
    assert calls == expected_calls[mode]
    if mode == "preview":
        assert result["data"]["native_apply"]["status"] == "unavailable_remote"
    legacy.assert_not_called()


def test_remote_mode_rejects_legacy_pool_and_existing_connection(monkeypatch):
    connection = unity_connection.UnityConnection(port=6400)
    socket_mock = Mock()
    connection.sock = socket_mock
    monkeypatch.setattr(config, "http_remote_hosted", True)
    for operation in (
        unity_connection.get_unity_connection_pool,
        connection.connect,
        lambda: connection.send_command("manage_script", {"action": "read"}),
    ):
        with pytest.raises(RuntimeError, match="disabled in remote-hosted"):
            operation()
    socket_mock.sendall.assert_not_called()
