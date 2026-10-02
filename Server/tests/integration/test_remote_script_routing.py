"""Remote script reads and writes must never use host-local Unity sockets."""
import importlib
from unittest.mock import AsyncMock, Mock

import pytest

from core.config import config
from transport import unity_transport
from transport.legacy import unity_connection

edits_tool = importlib.import_module("services.tools.script_apply_edits")


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["preview", "text", "structured", "mixed"])
async def test_script_operations_stay_in_authenticated_plugin_session(monkeypatch, mode):
    monkeypatch.setattr(config, "transport_mode", "http")
    monkeypatch.setattr(config, "http_remote_hosted", True)
    monkeypatch.setattr(unity_transport, "_resolve_user_id_from_request", AsyncMock(return_value="tenant-a"))
    legacy = Mock(side_effect=AssertionError("Host-local Unity must not be accessed"))
    monkeypatch.setattr(unity_connection, "get_unity_connection_pool", legacy)
    calls = []

    async def plugin(instance, command, params, **kwargs):
        assert instance == "SameName@samehash"
        assert kwargs["user_id"] == "tenant-a"
        calls.append(params["action"])
        return {"success": True, "data": {"contents": "class Foo {}\n", "sha256": "remote-sha"}}

    monkeypatch.setattr(unity_transport.PluginHub, "send_command_for_instance", plugin)
    ctx = AsyncMock()
    ctx.get_state.return_value = "SameName@samehash"
    text = {"op": "replace_range", "startLine": 1, "startCol": 1,
            "endLine": 1, "endCol": 1, "text": "// remote edit\n"}
    structured = {"op": "insert_method", "className": "Foo", "replacement": "void M() {}"}
    edits = [structured] if mode == "structured" else [text, structured] if mode == "mixed" else [text]
    result = await edits_tool.script_apply_edits(ctx, "Foo", "Assets", edits, {"preview": mode == "preview"})
    assert result["success"], result
    assert calls[0] == ("get_sha" if mode == "structured" else "read")
    if mode == "preview":
        assert calls == ["read"]
    else:
        assert len(calls) >= 2
    legacy.assert_not_called()


def test_remote_mode_rejects_legacy_pool_and_existing_connection(monkeypatch):
    connection = unity_connection.UnityConnection(port=6400)
    connection.sock = Mock()
    monkeypatch.setattr(config, "http_remote_hosted", True)
    for operation in (unity_connection.get_unity_connection_pool, connection.connect,
                      lambda: connection.send_command("manage_script", {"action": "read"})):
        with pytest.raises(RuntimeError, match="disabled in remote-hosted"):
            operation()
    connection.sock.sendall.assert_not_called()
