# /// script
# requires-python = ">=3.11"
# dependencies = ["fastmcp>=4,<5", "mcp>=2,<3", "websockets", "uvicorn"]
# ///
# ─── How to run ───
# Launched only by tools/bench_transport.py using the existing Server interpreter.
"""Owned FastMCP subset exercising actual public tools and product transports."""
from __future__ import annotations

import argparse
import importlib
import json
import os
import socket
import threading
import time
from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar
from functools import partial
from pathlib import Path
from typing import AsyncIterator, Iterator

import anyio
import uvicorn
from fastmcp import Context, FastMCP
from pydantic import JsonValue
from starlette.routing import WebSocketRoute
from starlette.websockets import WebSocket

from tools.tests.fixtures.transport_bench.peer import PeerState, TcpPeer, serve_websocket
from tools.tests.fixtures.transport_bench.workload import INSTANCE
from core.config import config
from transport.legacy.unity_connection import UnityConnection
from transport.local_auth_middleware import LocalControlAuthMiddleware
from transport.plugin_hub import PluginHub
from transport.plugin_registry import PluginRegistry
from transport.unity_transport import send_with_unity_instance
import transport.legacy.unity_connection as legacy

CORRELATION: ContextVar[str] = ContextVar("bench_correlation", default="")


class MeasuredConnection(UnityConnection):
    """Fixture-only timing of the existing product connection admission lock."""
    queue_path: Path

    @contextmanager
    def _command_lock(self, deadline: float | None) -> Iterator[None]:
        started = time.perf_counter()
        with super()._command_lock(deadline):
            elapsed = (time.perf_counter() - started) * 1000
            with self.queue_path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps({"correlation": CORRELATION.get(), "queue_ms": elapsed}) + "\n")
            yield


async def main(args: argparse.Namespace) -> None:
    console = importlib.import_module("services.tools.read_console")
    jobs = importlib.import_module("services.tools.run_tests")
    state = PeerState(Path(args.directory) / "peer.jsonl", args.large_bytes, args.work_ms, os.environ["BENCH_AUTH"])
    # Production send_command performs status-file preflight even on an explicit
    # socket. Substitute owned state before any calls so it cannot read user files.
    def owned_status(_target_hash: str | None = None) -> dict[str, JsonValue]:
        return {"reloading": False, "reason": "owned_benchmark"}

    legacy.read_status_file = owned_status
    original_connect = PluginHub._connect_authenticated

    async def instrument_connect(hub: PluginHub, websocket: WebSocket) -> None:
        await original_connect(hub, websocket)
        original_send = websocket.send_json

        async def send_json(data: dict[str, JsonValue], mode: str = "text") -> None:
            if data.get("type") == "execute" and data.get("name") == "ping":
                state.command_correlations[str(data["id"])] = CORRELATION.get() + ":readiness"
            await original_send(data, mode=mode)

        websocket.send_json = send_json

    # Timing is out of band: the actual WebSocket messages remain byte-for-byte
    # the product envelopes. This monkeypatch exists only in the owned process.
    PluginHub._connect_authenticated = instrument_connect
    config.transport_mode = args.transport
    config.http_remote_hosted = False
    config.connection_timeout = 3
    config.command_total_timeout = 5
    config.local_auth_token = os.environ["BENCH_AUTH"]
    connection: MeasuredConnection | None = None
    listener: socket.socket | None = None
    endpoint = ""
    if args.transport == "http":
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        endpoint = f"http://127.0.0.1:{listener.getsockname()[1]}"

    async def send(command: str, params: dict[str, JsonValue], **_kwargs: object) -> object:
        if connection is None:
            raise RuntimeError("Owned stdio connection is unavailable")
        return await anyio.to_thread.run_sync(partial(connection.send_command, command, params,
                                                       max_attempts=0, deadline=time.monotonic() + 5),
                                              abandon_on_cancel=False)

    async def ready() -> None:
        await state.registered.wait()
        (Path(args.directory) / "ready.json").write_text(json.dumps({"endpoint": endpoint}), encoding="utf-8")

    @asynccontextmanager
    async def lifespan(_server: FastMCP) -> AsyncIterator[None]:
        nonlocal connection
        async with anyio.create_task_group() as group:
            tcp: TcpPeer | None = None
            match args.transport:
                case "stdio":
                    tcp = TcpPeer(state)
                    thread = threading.Thread(target=tcp.serve_forever, daemon=True)
                    thread.start()
                    connection = MeasuredConnection(port=tcp.server_address[1], host="127.0.0.1", instance_id=INSTANCE,
                                                    auth_token_provider=lambda _generation: os.environ["BENCH_AUTH"],
                                                    allow_legacy_auth=False)
                    connection.queue_path = Path(args.directory) / "queue.jsonl"
                    console.async_send_command_with_retry = send
                    jobs.async_send_command_with_retry = send
                    state.registered.set()
                case "http":
                    PluginHub.configure(PluginRegistry())
                    group.start_soon(serve_websocket, state, endpoint.replace("http://", "ws://") + "/hub",
                                     os.environ["BENCH_AUTH"])
                case other:
                    raise ValueError(f"Unknown transport: {other}")
            group.start_soon(ready)
            try:
                yield
            finally:
                group.cancel_scope.cancel()
                if connection is not None:
                    connection.disconnect()
                if tcp is not None:
                    tcp.shutdown()
                    tcp.server_close()
                    thread.join(timeout=2)
                if args.transport == "http":
                    with anyio.CancelScope(shield=True):
                        await PluginHub.shutdown()

    mcp = FastMCP("OwnedTransportBench", lifespan=lifespan)

    @mcp.tool(name="read_console")
    async def read_console(ctx: Context, filter_text: str) -> dict[str, JsonValue]:
        await ctx.set_state("unity_instance", INSTANCE)
        token = CORRELATION.set(filter_text)
        try:
            return await console.read_console(ctx, filter_text=filter_text, count=1, format="json")
        finally:
            CORRELATION.reset(token)

    @mcp.tool(name="get_test_job")
    async def get_test_job(ctx: Context, job_id: str) -> dict[str, JsonValue]:
        await ctx.set_state("unity_instance", INSTANCE)
        token = CORRELATION.set(job_id)
        try:
            result = await jobs.get_test_job(ctx, job_id)
            return result.model_dump()
        finally:
            CORRELATION.reset(token)

    @mcp.tool(name="get_editor_state")
    async def get_editor_state(correlation: str) -> object:
        token = CORRELATION.set(correlation)
        try:
            return await send_with_unity_instance(send, INSTANCE, "get_editor_state", {"benchCorrelation": correlation})
        finally:
            CORRELATION.reset(token)

    @mcp.tool(name="bench_status")
    async def status() -> dict[str, JsonValue]:
        return {"commands": dict(state.commands), "registrations": state.registrations,
                "pending": len(PluginHub._pending), "retained": len(PluginHub._retained_results)}

    @mcp.tool(name="bench_reconnect")
    async def reconnect() -> dict[str, JsonValue]:
        match args.transport:
            case "stdio":
                if connection is None:
                    raise RuntimeError("Owned connection absent")
                connection.disconnect()
            case "http":
                websocket = state.websocket
                if websocket is None:
                    raise RuntimeError("Owned plugin absent")
                state.registered = anyio.Event()
                await websocket.close()
                with anyio.fail_after(5):
                    await state.registered.wait()
        return {"success": True}

    match args.transport:
        case "stdio":
            await mcp.run_async(transport="stdio", show_banner=False)
        case "http":
            app = mcp.http_app(path="/mcp", json_response=True)
            app.routes.append(WebSocketRoute("/hub", PluginHub))
            app.add_middleware(LocalControlAuthMiddleware, token=os.environ["BENCH_AUTH"])
            server = uvicorn.Server(uvicorn.Config(app, log_level="error", access_log=False))
            try:
                await server.serve(sockets=[listener])
            finally:
                if listener is not None:
                    listener.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--transport", choices=("stdio", "http"), required=True)
    parser.add_argument("--directory", required=True)
    parser.add_argument("--large-bytes", type=int, required=True)
    parser.add_argument("--work-ms", type=float, required=True)
    args = parser.parse_args()
    anyio.run(main, args)
