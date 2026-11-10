"""Framed reply validation through the real async adapter and durable receipt wrapper."""

import json
import sys

import pytest
from fastmcp.tools.base import ToolResult

from core.config import config
from models.models import MCPResponse
from models.response_limits import ResponseOwner, response_owner
from services.execution_receipt_identity import ReceiptOptions
from tests import test_stdio_json_codec
from tests.test_execution_receipt_integration import PROJECT, options, request
from transport.execution_receipts import META_KEY, ExecutionReceipts
from transport.legacy import unity_connection as uc
from transport.unity_transport import send_with_unity_instance

reply_connection = test_stdio_json_codec.reply_connection


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "wire,expected",
    [
        pytest.param(
            b'{"status":"success","result":{"integer":' + b"9" * 5000 + b"}}",
            None,
            id="integer-limit",
        ),
        pytest.param(b"{}", None, id="empty-object"),
        pytest.param(b'{"result":{"success":true}}', None, id="missing-status"),
        pytest.param(b'{"status":"pending","result":{}}', None, id="invalid-status"),
        pytest.param(b'{"status":[]}', None, id="container-status"),
        pytest.param(b'{"status":true}', None, id="boolean-status"),
        pytest.param(
            b'{"status":"success","result":{"success":true,"data":{"id":7}}}',
            {"success": True, "data": {"id": 7}},
            id="valid-success",
        ),
        pytest.param(b'{"status":"success"}', {}, id="valid-default-result"),
        pytest.param(
            b'{"status":"error","error":"Native refusal"}',
            {"success": False, "error": "Native refusal"},
            id="valid-error",
        ),
        pytest.param(
            b'{"status":"error","message":"Native message"}',
            {"success": False, "error": "Native message"},
            id="valid-error-message",
        ),
    ],
)
async def test_reply_validity_controls_receipt_observation_without_redispatch(
    tmp_path, monkeypatch, reply_connection, wire, expected
):
    # A rejected decoder/envelope must not become an ordinary completed receipt.
    conn, sock = reply_connection(wire)
    conn.instance_id = "Owned@" + PROJECT
    conn.session_generation = "controlled-stdio-generation"
    monkeypatch.setattr(config, "transport_mode", "stdio")
    runtime = ExecutionReceipts(tmp_path / "receipts", private_root=True)
    await runtime.start()
    delivery = ResponseOwner()
    token = response_owner.set(delivery)
    preparation = {
        "options": ReceiptOptions.parse(options(runtime)),
        "surface": "mcp-tool",
        "name": "manage_gameobject",
        "arguments": {"action": "modify"},
        "semantic_context": {},
        "policy_params": {"action": "modify"},
        "request": request(),
    }
    prepared = await runtime.prepare(**preparation)
    native_results = []

    async def body():
        response = await send_with_unity_instance(
            uc._async_send_command_attempt,
            conn.instance_id,
            "manage_gameobject",
            {"action": "modify"},
            max_retries=2,
        )
        native_results.append(
            response.model_dump() if isinstance(response, MCPResponse) else response
        )
        # Handlers may consume the native response and return their own public body.
        return ToolResult(structured_content={"success": True, "wrapper": "handled"})

    try:
        result = await prepared.execute(body)
        invalid = expected is None
        if invalid:
            assert native_results[0]["success"] is False
            assert native_results[0]["data"] == {
                "reason": "outcome_unknown",
                "command": "manage_gameobject",
            }
        elif "error" in expected:
            assert {key: native_results[0][key] for key in expected} == expected
        else:
            assert native_results[0] == expected
        metadata = result.meta[META_KEY]
        assert metadata["status"] == ("unknown" if invalid else "completed")
        assert metadata["result_available"] is (not invalid)
        stored = await runtime.io(
            lambda: runtime.store.lookup(
                prepared.authority.scope,
                preparation["options"],
                prepared.identity,
                grants=prepared.authority.grants,
            )
        )
        assert stored.status == metadata["status"]
        assert stored.result_available is (not invalid)
        if invalid:
            assert stored.result_bytes == 0
        assert len(stored.attempts) == 1
        assert stored.attempts[0].dispatched is True
        assert stored.attempts[0].observed is (not invalid)
        duplicate = await runtime.prepare(**preparation)
        assert isinstance(duplicate, ToolResult)
        assert duplicate.meta[META_KEY]["receipt_id"] == metadata["receipt_id"]
        assert duplicate.meta[META_KEY]["result_available"] is (not invalid)
        assert duplicate.is_error is invalid
        assert len(native_results) == 1
        assert len(sock.sent) == 2
        assert json.loads(sock.sent[1])["type"] == "manage_gameobject"
        assert sock.closed is invalid
        assert conn.sock is (None if invalid else sock)
        assert sys.get_int_max_str_digits() == sys.int_info.default_max_str_digits
    finally:
        prepared.close()
        response_owner.reset(token)
        delivery.release()
        runtime.close()
