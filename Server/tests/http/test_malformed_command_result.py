"""A malformed reply cannot keep an owned dispatch pending or prove success."""

import asyncio
import gzip
import json
import pytest
from fastmcp.tools.base import ToolResult

from models.response_limits import ResponseOwner, response_owner
from services.execution_receipt_identity import ReceiptOptions
from tests.http.test_local_project_context import A_HASH, environment, hub_environment  # noqa: F401
from tests.test_execution_receipt_integration import options, request
from transport.execution_receipts import ExecutionReceipts, META_KEY
from tests.test_plugin_json_decoder import COMMAND_ID, owned_hub  # noqa: F401
from transport.large_result_assembler import CHUNK_PAYLOAD_BYTES, COMPRESSION_CAPABILITY, MAGIC
from tests.http.test_local_project_context import Peer


@pytest.mark.asyncio
@pytest.mark.parametrize("negotiation", ["arbitration", "resume"])
@pytest.mark.parametrize("valid", [False, True])
async def test_only_valid_negotiated_proofs_are_observed(environment, tmp_path, negotiation, valid):
    hub, _, a, b, _ = environment
    peer = Peer(hub, a.root, "c" * 16)
    await peer.websocket.accept()
    resume = negotiation == "resume"
    await peer.endpoint.on_receive(
        peer.websocket,
        {
            "type": "register",
            "project_hash": peer.project_hash,
            "project_name": "Proof",
            "project_path": peer.root,
            "capabilities": ["native_arbitration_v1", *(["command_resume_v1"] if resume else [])],
            "arbitration_generation": "a" * 32,
            **({"resume_generation": "e" * 32} if resume else {}),
        },
    )
    runtime = ExecutionReceipts(tmp_path / "receipt-proof", private_root=True)
    await runtime.start()
    owner = ResponseOwner()
    token = response_owner.set(owner)
    prepared = None
    params = {"action": "save"}
    try:
        prepared = await runtime.prepare(
            options=ReceiptOptions.parse(options(runtime, project=peer.project_hash)),
            surface="mcp-tool",
            name="manage_scene",
            arguments=params,
            semantic_context={},
            policy_params=params,
            request=request(),
        )

        async def reply():
            wire = peer.executes[-1]
            assert len(peer.executes) == 1
            proof = {**wire["arbitration"], "disposition": "started", "retryable": False}
            result = {
                "native_arbitration": proof if valid or resume else None,
                "payload": {"success": True},
            }
            if resume:
                binding = wire["resume"]
                result = {
                    "command_resume": {
                        "version": 1,
                        "invocation_id": binding["invocation_id"],
                        "editor_session_id": binding["editor_session_id"],
                        # queued is inconsistent with the nested started arbitration.
                        "state": "completed" if valid else "queued",
                    },
                    "payload": result,
                }
            peer.reply = result

        peer.before_reply = reply

        async def handler():
            result = await hub.send_command(
                peer.websocket.state.plugin_session_id, "manage_scene", params
            )
            if valid:
                assert result["success"] is True
            else:
                assert result["data"]["reason"] == "outcome_unknown"
            return ToolResult(structured_content=result)

        result = await prepared.execute(handler)
        meta = result.meta[META_KEY]
        durable = runtime.store.get(
            prepared.authority.scope,
            store_id=meta["store_id"],
            receipt_id=meta["receipt_id"],
            grants=prepared.authority.grants,
        )
        assert durable.status == ("completed" if valid else "unknown")
        assert len(durable.attempts) == 1 and durable.attempts[0].dispatched
        assert durable.attempts[0].observed is valid
        assert len(peer.executes) == 1 and a.executes == b.executes == [] and hub._pending == {}
    finally:
        if prepared is not None:
            prepared.close()
        response_owner.reset(token)
        owner.release()
        runtime.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid_result", [None, []])
async def test_owned_invalid_reply_preserves_receipt_uncertainty(
    environment, tmp_path, invalid_result
):
    hub, _, a, b, client = environment
    runtime = ExecutionReceipts(tmp_path / "receipt-ledger", private_root=True)
    await runtime.start()
    owner = ResponseOwner()
    token = response_owner.set(owner)
    prepared = None
    try:
        prepared = await runtime.prepare(
            options=ReceiptOptions.parse(options(runtime, project=A_HASH)),
            surface="mcp-tool",
            name="ping",
            arguments={},
            semantic_context={},
            policy_params={},
            request=request(),
        )

        async def receive():
            command_id = a.executes[-1]["id"]
            entry = hub._pending[command_id]
            frame = {"type": "command_result", "id": command_id, "result": invalid_result}
            await b.endpoint.on_receive(b.websocket, frame)
            await a.endpoint.on_receive(a.websocket, {**frame, "id": ["foreign"]})
            generation = a.websocket.state.plugin_generation
            a.websocket.state.plugin_generation = "replacement-generation"
            try:
                await a.endpoint.on_receive(a.websocket, frame)
            finally:
                a.websocket.state.plugin_generation = generation
            assert not entry["future"].done()
            await a.endpoint.on_receive(a.websocket, frame)
            assert entry["future"].done(), "Owned malformed reply left the native command pending"
            assert "result_work" not in entry

        a.before_reply = receive

        async def handler():
            result = await hub.send_command(a.websocket.state.plugin_session_id, "ping", {})
            assert result["data"]["reason"] == "outcome_unknown"
            assert result["data"]["command_id"] == a.executes[0]["id"]
            assert "invalid_command_result" in result["error"]
            return ToolResult(structured_content=result)

        result = await prepared.execute(handler)
        assert result.meta[META_KEY]["status"] == "unknown"
        assert result.meta[META_KEY]["result_available"] is False
        attempts = result.meta[META_KEY]["attempts"]
        assert len(attempts) == 1 and attempts[0]["dispatched"] and not attempts[0]["observed"]
        assert len(a.executes) == 1 and b.executes == [] and hub._pending == {}
    finally:
        if prepared is not None:
            prepared.close()
        response_owner.reset(token)
        owner.release()
        runtime.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("compressed", [False, True])
@pytest.mark.parametrize("replacement", ["none", "entry", "future"])
async def test_negotiated_invalid_result_requires_exact_pending_owner(
    owned_hub, compressed, replacement
):
    hub, socket = owned_hub
    socket.state.plugin_registered = True
    socket.state.plugin_features.add(COMPRESSION_CAPABILITY)
    pending = type(hub)._pending
    original, other = [asyncio.get_running_loop().create_future() for _ in range(2)]
    pending[COMMAND_ID] = {"future": original, "session_id": "owned", "user_id": None}
    raw = json.dumps(
        {"type": "command_result", "id": COMMAND_ID, "result": None, "pad": "x" * 1048576}
    ).encode()
    wire = gzip.compress(raw) if compressed else raw
    try:
        await hub.on_receive(
            socket,
            {
                "type": "result_start",
                "id": COMMAND_ID,
                "total_bytes": len(wire),
                "chunk_count": (len(wire) + CHUNK_PAYLOAD_BYTES - 1) // CHUNK_PAYLOAD_BYTES,
                **({"encoding": "gzip", "decoded_bytes": len(raw)} if compressed else {}),
            },
        )
        if replacement == "entry":
            pending[COMMAND_ID] = dict(pending[COMMAND_ID])
        elif replacement == "future":
            pending[COMMAND_ID]["future"] = other
        for offset in range(0, len(wire), CHUNK_PAYLOAD_BYTES):
            await hub.on_receive(
                socket,
                MAGIC
                + COMMAND_ID.encode()
                + offset.to_bytes(4, "big")
                + wire[offset : offset + CHUNK_PAYLOAD_BYTES],
            )
        if replacement == "none":
            assert original.done() and str(original.exception()) == "invalid_command_result"
        else:
            assert not original.done()
        assert not other.done()
        socket.close.assert_not_awaited()
        assert type(hub)._retained_results.total_bytes == type(hub)._raw_results.total_bytes == 0
    finally:
        original.cancel()
        other.cancel()
