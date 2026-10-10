"""Shader validation, response shapes and registered Click input regressions."""

import base64
import copy
import importlib
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from click.testing import CliRunner
from fastmcp import FastMCP

from cli.main import cli
from cli.utils import connection

shader_tool = importlib.import_module("services.tools.manage_shader")


def inline(coroutine):
    """Run only immediate in-memory awaits; never create an event loop."""
    try:
        coroutine.send(None)
    except StopIteration as complete:
        return complete.value
    coroutine.close()
    raise AssertionError("Test coroutine unexpectedly suspended")


@pytest.fixture
def shader_route(monkeypatch):
    instance = AsyncMock(return_value="Owned@fixture")
    sender = AsyncMock(return_value={"success": True})
    monkeypatch.setattr(shader_tool, "get_unity_instance_from_context", instance)
    monkeypatch.setattr(shader_tool, "send_with_unity_instance", sender)
    return instance, sender


@pytest.mark.parametrize("action", ["create", "read", "update", "delete"])
def test_missing_shader_name_rejects_before_instance_discovery(shader_route, action):
    instance, sender = shader_route
    result = inline(shader_tool.manage_shader(None, action=action))
    assert result["success"] is False and "name" in result["message"]
    instance.assert_not_called()
    sender.assert_not_called()


@pytest.mark.parametrize("paging", [{"page_size": "invalid"}, {"page_size": 0}, {"page_number": 0}])
def test_invalid_graph_page_rejects_before_instance_discovery(shader_route, paging):
    instance, sender = shader_route
    result = inline(
        shader_tool.manage_shader(
            None, action="inspect_graph", path="Assets/Owned.shadergraph", **paging
        )
    )
    assert result["success"] is False and "page_" in result["message"]
    instance.assert_not_called()
    sender.assert_not_called()


@pytest.fixture
def shader_transport(monkeypatch):
    """Exercise production envelope normalization without a listener or Client."""
    transport = importlib.import_module("transport.unity_transport")
    instance = AsyncMock(return_value="Owned@fixture")
    sender = AsyncMock()
    monkeypatch.setattr(shader_tool, "get_unity_instance_from_context", instance)
    monkeypatch.setattr(shader_tool, "send_with_unity_instance", transport.send_with_unity_instance)
    monkeypatch.setattr(transport, "_is_http_transport", lambda: True)
    monkeypatch.setattr(transport.config, "http_remote_hosted", False)
    monkeypatch.setattr(transport.PluginHub, "send_command_for_instance", sender)
    return instance, sender


@pytest.mark.parametrize("wrapped", [False, True])
@pytest.mark.parametrize(
    "data", [None, False, 0, "", [], {"zero": 0, "false": False, "null": None}]
)
def test_shader_success_retains_nullable_and_scalar_data(shader_transport, data, wrapped):
    instance, sender = shader_transport
    expected = {"success": True, "message": "Done", "data": data}
    sender.return_value = copy.deepcopy(
        {"status": "success", "result": expected} if wrapped else expected
    )
    result = inline(shader_tool.manage_shader(None, action="read", name="Owned"))
    assert result == expected
    instance.assert_awaited_once()
    assert sender.call_args.args[:2] == ("Owned@fixture", "manage_shader")
    tool = FastMCP("shader-result-regression").add_tool(shader_tool.manage_shader)
    encoded = tool.convert_result(result)
    assert encoded.structured_content == expected
    assert json.loads(encoded.content[0].text) == expected


@pytest.mark.parametrize("contents", [None, "", "Shader Ω\n\ud55c\uae00"])
def test_shader_encoding_and_native_failure_remain_unchanged(shader_route, contents):
    _, sender = shader_route
    failure = {
        "success": False,
        "error": "Native rejection",
        "data": {"zero": 0, "false": False, "null": None},
    }
    sender.return_value = failure
    result = inline(
        shader_tool.manage_shader(None, action="update", name="Owned", contents=contents)
    )
    expected = {"action": "update", "name": "Owned", "path": "Assets/"}
    if contents is not None:
        expected.update(
            encodedContents=base64.b64encode(contents.encode("utf-8")).decode("utf-8"),
            contentsEncoded=True,
        )
    assert sender.call_args.args[3] == expected
    assert result is failure


@pytest.mark.parametrize("contents", ["", "Shader Ω\n\ud55c\uae00"])
def test_encoded_shader_reply_still_replaces_original_contents(shader_route, contents):
    _, sender = shader_route
    sender.return_value = {
        "success": True,
        "data": {
            "contents": "old",
            "encodedContents": base64.b64encode(contents.encode("utf-8")).decode("utf-8"),
            "contentsEncoded": True,
        },
    }
    result = inline(shader_tool.manage_shader(None, action="read", name="Owned"))
    assert result == {
        "success": True,
        "message": "Operation successful.",
        "data": {"contents": contents},
    }


@pytest.fixture
def shader_http(monkeypatch):
    raw, requests = {"success": True}, []
    client_type = httpx.AsyncClient

    def respond(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json=raw)

    monkeypatch.setattr(
        connection.httpx, "AsyncClient", lambda: client_type(transport=httpx.MockTransport(respond))
    )
    monkeypatch.setattr(connection, "asyncio", SimpleNamespace(run=inline))
    monkeypatch.setattr(connection, "_auth_headers", lambda _config: {})
    return raw, requests


@pytest.mark.parametrize("action", ["create", "update"])
def test_shader_file_directory_reports_option_diagnostic_before_request(
    shader_http, tmp_path, action
):
    _, requests = shader_http
    args = [
        "shader",
        action,
        "Owned" if action == "create" else "Assets/Owned.shader",
        "--file",
        str(tmp_path),
    ]
    result = CliRunner().invoke(cli, args)
    assert result.exit_code == 2, (result.output, result.exception)
    assert "Invalid value for" in result.output and "--file" in result.output
    assert not requests


@pytest.mark.parametrize("data", [None, False, 0, "", []])
@pytest.mark.parametrize("wrapped", [False, True])
def test_shader_read_text_retains_success_for_nonobject_data(shader_http, data, wrapped):
    raw, requests = shader_http
    expected = {"success": True, "data": data}
    raw.clear()
    raw.update({"status": "success", "result": expected} if wrapped else expected)
    result = CliRunner().invoke(cli, ["--format", "text", "shader", "read", "Assets/Owned.shader"])
    assert result.exit_code == 0, (result.output, result.exception)
    assert len(requests) == 1


@pytest.mark.parametrize("wrapped", [False, True])
def test_shader_native_failure_envelope_keeps_partial_diagnostics(shader_transport, wrapped):
    _, sender = shader_transport
    failure = {
        "success": False,
        "code": "native_rejected",
        "error": "Native rejection",
        "hint": "repair",
        "data": {"partial": [], "zero": 0, "false": False, "null": None},
    }
    sender.return_value = {"status": "success", "result": failure} if wrapped else failure
    result = inline(shader_tool.manage_shader(None, action="update", name="Owned", contents=""))
    assert result == failure


@pytest.mark.parametrize("encoded", [None, [], "/w==", "a"])
def test_shader_malformed_encoded_content_remains_a_diagnostic_failure(shader_route, encoded):
    _, sender = shader_route
    sender.return_value = {
        "success": True,
        "data": {"encodedContents": encoded, "contentsEncoded": True},
    }
    result = inline(shader_tool.manage_shader(None, action="read", name="Owned"))
    assert result["success"] is False
    assert "Python error managing shader" in result["message"]


@pytest.mark.parametrize("flag", [False, 0, None])
def test_shader_false_encoding_control_keeps_payload_unchanged(shader_route, flag):
    _, sender = shader_route
    data = {"encodedContents": "/w==", "contentsEncoded": flag, "reference": None}
    sender.return_value = {"success": True, "message": "Done", "data": copy.deepcopy(data)}
    result = inline(shader_tool.manage_shader(None, action="read", name="Owned"))
    assert result == {"success": True, "message": "Done", "data": data}
