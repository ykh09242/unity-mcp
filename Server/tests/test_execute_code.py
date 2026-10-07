"""Tests for execute_code tool."""

import asyncio
import os
import subprocess
import sys
import textwrap
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from services.tools.execute_code import execute_code


@pytest.fixture
def mock_unity(monkeypatch):
    captured: dict[str, object] = {}

    async def fake_send(send_fn, unity_instance, tool_name, params):
        captured["tool_name"] = tool_name
        captured["params"] = params
        return {"success": True, "message": "Code executed successfully.", "data": {"result": 42}}

    monkeypatch.setattr(
        "services.tools.execute_code.get_unity_instance_from_context",
        AsyncMock(return_value="unity-instance-1"),
    )
    monkeypatch.setattr(
        "services.tools.execute_code.send_with_unity_instance",
        fake_send,
    )
    return captured


@pytest.fixture
def mock_unity_error(monkeypatch):
    async def fake_send(send_fn, unity_instance, tool_name, params):
        return {"success": False, "error": "Compilation failed"}

    monkeypatch.setattr(
        "services.tools.execute_code.get_unity_instance_from_context",
        AsyncMock(return_value="unity-instance-1"),
    )
    monkeypatch.setattr(
        "services.tools.execute_code.send_with_unity_instance",
        fake_send,
    )


# --- execute action ---


def test_execute_forwards_code_to_unity(mock_unity):
    result = asyncio.run(execute_code(SimpleNamespace(), action="execute", code="return 42;"))
    assert result["success"] is True
    assert mock_unity["tool_name"] == "execute_code"
    assert mock_unity["params"]["code"] == "return 42;"
    assert mock_unity["params"]["action"] == "execute"


def test_execute_sends_safety_checks_true_by_default(mock_unity):
    asyncio.run(execute_code(SimpleNamespace(), action="execute", code="return 1;"))
    assert mock_unity["params"]["safety_checks"] is True


def test_execute_sends_safety_checks_false(mock_unity):
    asyncio.run(execute_code(SimpleNamespace(), action="execute", code="x();", safety_checks=False))
    assert mock_unity["params"]["safety_checks"] is False


def test_execute_returns_data(mock_unity):
    result = asyncio.run(execute_code(SimpleNamespace(), action="execute", code="return 42;"))
    assert result["data"]["result"] == 42


def test_execute_requires_code():
    result = asyncio.run(execute_code(SimpleNamespace(), action="execute", code=None))
    assert result["success"] is False
    assert "code" in result["message"].lower()


# --- get_history action ---


def test_get_history_forwards_to_unity(mock_unity):
    asyncio.run(execute_code(SimpleNamespace(), action="get_history", limit=5))
    assert mock_unity["params"]["action"] == "get_history"
    assert mock_unity["params"]["limit"] == 5


def test_get_history_default_limit(mock_unity):
    asyncio.run(execute_code(SimpleNamespace(), action="get_history"))
    assert mock_unity["params"]["limit"] == 10


def test_get_history_clamps_limit(mock_unity):
    asyncio.run(execute_code(SimpleNamespace(), action="get_history", limit=999))
    assert mock_unity["params"]["limit"] == 50


def test_get_history_clamps_negative_limit(mock_unity):
    asyncio.run(execute_code(SimpleNamespace(), action="get_history", limit=-5))
    assert mock_unity["params"]["limit"] == 1


# --- replay action ---


def test_replay_forwards_index(mock_unity):
    asyncio.run(execute_code(SimpleNamespace(), action="replay", index=3))
    assert mock_unity["params"]["action"] == "replay"
    assert mock_unity["params"]["index"] == 3


def test_replay_requires_index():
    result = asyncio.run(execute_code(SimpleNamespace(), action="replay", index=None))
    assert result["success"] is False
    assert "index" in result["message"].lower()


# --- clear_history action ---


def test_clear_history_forwards(mock_unity):
    asyncio.run(execute_code(SimpleNamespace(), action="clear_history"))
    assert mock_unity["params"]["action"] == "clear_history"


# --- error handling ---


def test_error_response_normalized(mock_unity_error):
    result = asyncio.run(execute_code(SimpleNamespace(), action="execute", code="bad"))
    assert result["success"] is False
    assert "Compilation failed" in result["message"]


def test_non_dict_response_handled(monkeypatch):
    async def fake_send(send_fn, unity_instance, tool_name, params):
        return "unexpected"

    monkeypatch.setattr(
        "services.tools.execute_code.get_unity_instance_from_context",
        AsyncMock(return_value="unity-instance-1"),
    )
    monkeypatch.setattr(
        "services.tools.execute_code.send_with_unity_instance",
        fake_send,
    )
    result = asyncio.run(execute_code(SimpleNamespace(), action="execute", code="return 1;"))
    assert result["success"] is False


@pytest.mark.parametrize(
    ("action", "params"),
    [
        ("execute", {"code": "return 1;"}),
        ("get_history", {}),
        ("replay", {"index": 0}),
        ("clear_history", {}),
    ],
)
@pytest.mark.parametrize(
    "response",
    [
        {
            "success": False,
            "message": None,
            "error": "Select a Unity instance",
            "hint": "select_instance",
            "data": {"available_instances": ["unity-instance-1", "unity-instance-2"]},
        },
        {
            "success": False,
            "message": "API key required",
            "error": "auth_required",
            "data": None,
        },
        {
            "success": False,
            "message": None,
            "error": "TimeoutError",
            "hint": "retry",
            "data": None,
        },
    ],
)
def test_failure_preserves_transport_recovery_details(monkeypatch, action, params, response):
    original_response = response.copy()
    send = AsyncMock(return_value=response)
    monkeypatch.setattr("services.tools.execute_code.send_with_unity_instance", send)
    monkeypatch.setattr(
        "services.tools.execute_code.get_unity_instance_from_context",
        AsyncMock(return_value="unity-instance-1"),
    )

    result = asyncio.run(execute_code(SimpleNamespace(), action=action, **params))

    assert result["success"] is False
    assert result["message"] == (response["message"] or response["error"])
    assert result.get("error") == response["error"]
    assert result.get("hint") == response.get("hint")
    assert result["data"] == response["data"]
    assert response == original_response


@pytest.mark.parametrize("action", ["execute", "replay"])
def test_missing_required_input_skips_context_lookup_and_dispatch(monkeypatch, action):
    lookup = AsyncMock()
    send = AsyncMock()
    monkeypatch.setattr("services.tools.execute_code.get_unity_instance_from_context", lookup)
    monkeypatch.setattr("services.tools.execute_code.send_with_unity_instance", send)

    result = asyncio.run(execute_code(SimpleNamespace(), action=action))

    assert result["success"] is False
    lookup.assert_not_awaited()
    send.assert_not_awaited()


def test_sdk_rejects_scalar_coercion_before_unity_dispatch():
    code = textwrap.dedent("""
        import asyncio, importlib
        from fastmcp import FastMCP, Client
        from services.tools import register_all_tools

        module = importlib.import_module("services.tools.execute_code")
        sent, errors = [], []
        async def send(fn, instance, command, params):
            sent.append(params)
            return {"success": True, "message": "OK", "data": {"result": 42}}
        module.send_with_unity_instance = send
        server = FastMCP("execute-code-contract")
        register_all_tools(server)

        async def main():
            for mode in ("2026-07-28", "legacy"):
                async with Client(server, mode=mode) as client:
                    tool = next(t for t in await client.list_tools() if t.name == "execute_code")
                    assert tool.input_schema["properties"]["safety_checks"]["type"] == "boolean"
                    assert {"type": "integer"} in tool.input_schema["properties"]["index"]["anyOf"]
                    properties = tool.input_schema["properties"]
                    assert "replay" in properties["safety_checks"].get("description", "")
                    assert "replay" in properties["index"].get("description", "")
                    assert "1-50" in properties["limit"].get("description", "")
                    invalid = [
                        {"action": "replay", "index": True},
                        {"action": "replay", "index": 0.5},
                        {"action": "replay", "index": 0.0},
                        {"action": "execute", "code": "return 1;", "safety_checks": 0},
                        {"action": "get_history", "limit": True},
                    ]
                    for payload in invalid:
                        before = len(sent)
                        result = await client.call_tool("execute_code", payload, raise_on_error=False)
                        if not result.is_error or len(sent) != before:
                            error = f"{mode}: {payload!r} dispatched as {sent[-1] if len(sent) > before else None!r}"
                            errors.append(error)
                            print(error)
                    for payload in (
                        {"action": "replay", "index": 0},
                        {"action": "execute", "code": "return 1;", "safety_checks": False},
                        {"action": "get_history", "limit": 5},
                    ):
                        result = await client.call_tool("execute_code", payload)
                        assert result.structured_content["success"] is True
                        assert sent[-1].items() >= payload.items()
            assert not errors, errors
            print("real SDK execute_code scalar contracts passed in both protocol modes")
        asyncio.run(main())
    """)
    result = subprocess.run(
        [sys.executable, "-B", "-c", code],
        capture_output=True,
        text=True,
        timeout=60,
        env={**os.environ, "UNITY_MCP_DISABLE_TELEMETRY": "true", "UNITY_MCP_TRANSPORT": "stdio"},
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "scalar contracts passed" in result.stdout


# --- param isolation ---


def test_execute_omits_irrelevant_params(mock_unity):
    asyncio.run(execute_code(SimpleNamespace(), action="execute", code="return 1;"))
    assert "index" not in mock_unity["params"]
    assert "limit" not in mock_unity["params"]


def test_history_omits_irrelevant_params(mock_unity):
    asyncio.run(execute_code(SimpleNamespace(), action="get_history"))
    assert "code" not in mock_unity["params"]
    assert "index" not in mock_unity["params"]
    assert "safety_checks" not in mock_unity["params"]


def test_replay_omits_irrelevant_params(mock_unity):
    asyncio.run(execute_code(SimpleNamespace(), action="replay", index=0))
    assert "code" not in mock_unity["params"]
    assert "limit" not in mock_unity["params"]
    assert "safety_checks" not in mock_unity["params"]
