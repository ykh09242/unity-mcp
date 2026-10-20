"""Durable receipt replay across two actual owned retained-stdio SDK processes."""

from contextlib import asynccontextmanager
import itertools
import json
from pathlib import Path
import subprocess
import sys

import anyio
from anyio.streams.buffered import BufferedByteReceiveStream
import pytest

TOOLS = Path(__file__).resolve().parents[2] / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

from bench_transport_process import cleanup_process, native_python

META = "io.github.ykh09242.unity-mcp/execution"
PROJECT = "0123456789abcdef"


@asynccontextmanager
async def owned_stdio(tmp_path: Path, store: Path, protocol: str):
    fixture = Path(__file__).with_name("execution_receipt_stdio_fixture.py")
    source = fixture.parent.parent / "src"
    command = native_python([str(fixture), str(store)], (source, TOOLS))
    command.insert(1, "-B")  # -I ignores PYTHON* and a parent's -B is not inherited.
    with (tmp_path / "stderr.log").open("wb") as stderr:
        process = await anyio.open_process(
            command, cwd=tmp_path, stdin=subprocess.PIPE, stderr=stderr
        )
        try:
            (tmp_path / "owned-process.json").write_text(
                json.dumps({"pid": process.pid, "command": command}) + "\n", encoding="utf-8"
            )
            assert process.stdout is not None and process.stdin is not None
            output = BufferedByteReceiveStream(process.stdout)
            request_ids = itertools.count(1)

            async def exchange(method, params, *, notification=False):
                if protocol == "2026-07-28" and method == "tools/call":
                    params = {
                        **params,
                        "_meta": {
                            "io.modelcontextprotocol/protocolVersion": protocol,
                            "io.modelcontextprotocol/clientCapabilities": {},
                            "io.modelcontextprotocol/clientInfo": {
                                "name": "receipt-stdio",
                                "version": "1",
                            },
                            **params.get("_meta", {}),
                        },
                    }
                message = {"jsonrpc": "2.0", "method": method, "params": params}
                # Each request needs a fresh ID even after its response is received:
                # the peer may still be settling the previous stdout flush.
                identifier = None if notification else next(request_ids)
                if identifier is not None:
                    message["id"] = identifier
                await process.stdin.send(json.dumps(message).encode() + b"\n")
                if identifier is None:
                    return None
                reply = json.loads(await output.receive_until(b"\n", 128 * 1024))
                assert reply.get("id") == identifier, reply
                assert "error" not in reply, reply
                return reply

            if protocol == "2025-11-25":
                reply = await exchange(
                    "initialize",
                    {
                        "protocolVersion": protocol,
                        "capabilities": {},
                        "clientInfo": {"name": "receipt-stdio", "version": "1"},
                    },
                )
                assert reply["result"]["protocolVersion"] == protocol
                await exchange("notifications/initialized", {}, notification=True)
            yield exchange
            await process.stdin.aclose()
        finally:
            await cleanup_process(process, grace_seconds=2)


@pytest.mark.asyncio
@pytest.mark.parametrize("protocol", ["2025-11-25", "2026-07-28"])
@pytest.mark.parametrize("as_error", [False, True])
async def test_actual_stdio_restart_rehydrates_receipt_without_second_handler(
    tmp_path, protocol, as_error
):
    # Given one owned native SDK process and its actual private receipt store.
    first_dir, second_dir = tmp_path / "first", tmp_path / "second"
    first_dir.mkdir()
    second_dir.mkdir()
    store = tmp_path / "receipts"
    with anyio.fail_after(25):
        async with owned_stdio(first_dir, store, protocol) as call:
            described = await call("tools/call", {"name": "get_execution_receipt", "arguments": {}})
            store_id = described["result"]["structuredContent"]["store_id"]
            params = {
                "name": "receipt_exercise",
                "arguments": {"checked": 4, "as_error": as_error},
                "_meta": {
                    META: {
                        "version": 1,
                        "store_id": store_id,
                        "project_hash": PROJECT,
                        "idempotency_key": "stdio-restart-key-0001",
                        "retain_result": True,
                    }
                },
            }
            initial = await call("tools/call", params)
        # When a second owned process reopens the same journal and receives that key.
        async with owned_stdio(second_dir, store, protocol) as call:
            replay = await call("tools/call", params)
            count = await call("tools/call", {"name": "body_count", "arguments": {}})
        # Then actual public SDK wire fields survive and the restarted body never runs.
        assert initial["result"]["content"] == replay["result"]["content"]
        assert initial["result"]["structuredContent"] == replay["result"]["structuredContent"]
        assert initial["result"]["isError"] is replay["result"]["isError"] is as_error
        assert initial["result"]["_meta"]["application"] == replay["result"]["_meta"]["application"]
        assert replay["result"]["_meta"][META]["replayed"] is True
        assert count["result"]["structuredContent"]["body"] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("protocol", ["2025-11-25", "2026-07-28"])
async def test_owned_stdio_client_never_reuses_request_ids(tmp_path, monkeypatch, protocol):
    frames = []
    open_process = anyio.open_process

    async def capture_process(*args, **kwargs):
        process = await open_process(*args, **kwargs)
        send = process.stdin.send

        async def capture_request(data):
            frames.append(json.loads(data))
            await send(data)

        monkeypatch.setattr(process.stdin, "send", capture_request)
        return process

    monkeypatch.setattr(anyio, "open_process", capture_process)
    with anyio.fail_after(25):
        async with owned_stdio(tmp_path, tmp_path / "receipts", protocol) as call:
            first = await call("tools/call", {"name": "body_count", "arguments": {}})
            second = await call("tools/call", {"name": "body_count", "arguments": {}})
    identifiers = [frame["id"] for frame in frames if "id" in frame]
    assert len(identifiers) >= 2
    assert len(identifiers) == len(set(identifiers)), frames
    assert all(
        "id" not in frame for frame in frames if frame["method"].startswith("notifications/")
    )
    assert (
        first["result"]["structuredContent"] == second["result"]["structuredContent"] == {"body": 0}
    )
