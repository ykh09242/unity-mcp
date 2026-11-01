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
            frames = []

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
                wire = json.dumps(message).encode() + b"\n"
                frames.append(json.loads(wire))
                await process.stdin.send(wire)
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
            yield exchange, frames
            await process.stdin.aclose()
        finally:
            await cleanup_process(process, grace_seconds=2)


@pytest.fixture(scope="module")
def receipt_scenarios(tmp_path_factory):
    """Share incidental SDK startup; each protocol still owns a real restart."""
    scenarios = {}

    async def scenario(protocol):
        if protocol in scenarios:
            return scenarios[protocol]
        root = tmp_path_factory.mktemp("receipt-" + protocol)
        first_dir, second_dir = root / "first", root / "second"
        first_dir.mkdir()
        second_dir.mkdir()
        store = root / "receipts"
        initial, replay, params = {}, {}, {}
        with anyio.fail_after(25):
            async with owned_stdio(first_dir, store, protocol) as (call, first_frames):
                described = await call(
                    "tools/call", {"name": "get_execution_receipt", "arguments": {}}
                )
                store_id = described["result"]["structuredContent"]["store_id"]
                for as_error in (False, True):
                    params[as_error] = {
                        "name": "receipt_exercise",
                        "arguments": {"checked": 4, "as_error": as_error},
                        "_meta": {
                            META: {
                                "version": 1,
                                "store_id": store_id,
                                "project_hash": PROJECT,
                                "idempotency_key": "stdio-restart-key-" + str(as_error),
                                "retain_result": True,
                            }
                        },
                    }
                    initial[as_error] = await call("tools/call", params[as_error])
                executed = await call("tools/call", {"name": "body_count", "arguments": {}})
                assert executed["result"]["structuredContent"]["body"] == 2
            # A new native process reopens both independent journal records.
            async with owned_stdio(second_dir, store, protocol) as (call, second_frames):
                for as_error in (False, True):
                    replay[as_error] = await call("tools/call", params[as_error])
                first_count = await call("tools/call", {"name": "body_count", "arguments": {}})
                second_count = await call("tools/call", {"name": "body_count", "arguments": {}})
            assert (
                first_count["result"]["structuredContent"]
                == second_count["result"]["structuredContent"]
                == {"body": 0}
            )
        scenarios[protocol] = initial, replay, (first_frames, second_frames)
        return scenarios[protocol]

    return scenario


@pytest.mark.asyncio
@pytest.mark.parametrize("protocol", ["2025-11-25", "2026-07-28"])
@pytest.mark.parametrize("as_error", [False, True])
async def test_actual_stdio_restart_rehydrates_receipt_without_second_handler(
    receipt_scenarios, protocol, as_error
):
    initial_results, replay_results, _frames = await receipt_scenarios(protocol)
    initial, replay = initial_results[as_error], replay_results[as_error]
    assert initial["result"]["content"] == replay["result"]["content"]
    assert initial["result"]["structuredContent"] == replay["result"]["structuredContent"]
    assert initial["result"]["isError"] is replay["result"]["isError"] is as_error
    assert initial["result"]["_meta"]["application"] == replay["result"]["_meta"]["application"]
    assert replay["result"]["_meta"][META]["replayed"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("protocol", ["2025-11-25", "2026-07-28"])
async def test_owned_stdio_client_never_reuses_request_ids(receipt_scenarios, protocol):
    _initial, _replay, processes = await receipt_scenarios(protocol)
    for frames in processes:
        identifiers = [frame["id"] for frame in frames if "id" in frame]
        assert len(identifiers) >= 4
        assert len(identifiers) == len(set(identifiers)), frames
        assert all(
            "id" not in frame for frame in frames if frame["method"].startswith("notifications/")
        )
