# /// script
# requires-python = ">=3.11"
# dependencies = ["fastmcp>=4,<5", "mcp>=2,<3", "websockets", "uvicorn"]
# ///
# ─── How to run ───
# Launched only by tools/bench_transport.py using the existing Server interpreter.
"""Owned UnityMCP subset exercising actual public tools and product transports."""
from __future__ import annotations

import argparse
import importlib
import importlib.metadata
import json
import os
import socket
import struct
import sys
import threading
import time
from contextlib import ExitStack, asynccontextmanager, contextmanager
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
from tools.tests.fixtures.transport_bench.accounting import Accounting
from tools.tests.fixtures.transport_bench.resource import ResourceContract, ResourceHold
from main import UnityMCP
from core.config import config
from transport.legacy.unity_connection import UnityConnection
from transport.plugin_hub import PluginHub
from transport.plugin_registry import PluginRegistry
from transport.unity_transport import send_with_unity_instance
import transport.legacy.unity_connection as legacy
from transport.response_limit_middleware import ResponseLimitMiddleware
from tools.bench_transport_diagnostic import Trace, patch, require_sdk, traced_tool, write_sidecar

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


async def serve(args: argparse.Namespace, diagnostic: Trace | None) -> None:
    console = importlib.import_module("services.tools.read_console")
    jobs = importlib.import_module("services.tools.run_tests")
    state = PeerState(Path(args.directory) / "peer.jsonl", args.large_bytes, args.work_ms, os.environ["BENCH_AUTH"])
    state.cohort_gate, state.concurrency, state.samples = args.cohort_gate, args.concurrency, args.samples
    accounting = Accounting(args.transport)
    expected_src = (Path(args.product_root) / "Server/src").resolve()
    def product_imports() -> dict[str, str]:
        loaded = {}
        for name, module in tuple(sys.modules.items()):
            if name.split(".")[0] in {"main", "core", "models", "services", "transport", "utils"}:
                source = getattr(module, "__file__", None)
                if source:
                    path = Path(source).resolve()
                    if not path.is_relative_to(expected_src):
                        raise RuntimeError(f"Product import escaped selected source root: {name}")
                    loaded[name] = path.relative_to(expected_src).as_posix()
        return loaded

    product_imports()
    # Production send_command performs status-file preflight even on an explicit
    # socket. Substitute owned state before any calls so it cannot read user files.
    def owned_status(_target_hash: str | None = None) -> dict[str, JsonValue]:
        return {"reloading": False, "reason": "owned_benchmark"}

    legacy.read_status_file = owned_status
    original_large = PluginHub._handle_large_result
    original_result = PluginHub._handle_command_result

    async def observe_large(hub, websocket, data) -> None:
        await original_large(hub, websocket, data)
        snapshot = accounting.snapshot()
        if isinstance(data, bytes) and snapshot.get("assembler_buffer_bytes", 0) > 0:
            state.partial_admitted.set()
        # A send-side completion is not proof that the hub consumed late frames.
        # Correlate the final identity chunk after the actual product handler.
        if (isinstance(data, bytes) and len(data) >= 44 and data[:4] == b"ULR1"
                and data[4:40].decode("ascii") == state.partial_command_id
                and struct.unpack(">I", data[40:44])[0] + len(data) - 44 == state.partial_total_bytes):
            state.partial_received.set()

    async def observe_result(hub, websocket, payload) -> None:
        await original_result(hub, websocket, payload)
        accounting.snapshot()

    PluginHub._handle_large_result = observe_large
    PluginHub._handle_command_result = observe_result
    original_connect = PluginHub._connect_authenticated

    async def instrument_connect(hub: PluginHub, websocket: WebSocket) -> None:
        await original_connect(hub, websocket)
        original_send = websocket.send_json

        async def send_json(data: dict[str, JsonValue], mode: str = "text") -> None:
            if data.get("type") == "execute" and data.get("name") == "ping":
                state.command_correlations[str(data["id"])] = CORRELATION.get() + ":readiness"
            if diagnostic is not None and data.get('type') == 'execute':
                diagnostic.alias(str(data['id']), CORRELATION.get())
            await original_send(data, mode=mode)

        websocket.send_json = send_json

    # Timing is out of band: the actual WebSocket messages remain byte-for-byte
    # the product envelopes. This monkeypatch exists only in the owned process.
    PluginHub._connect_authenticated = instrument_connect
    config.transport_mode = args.transport
    config.http_remote_hosted = False
    config.connection_timeout = 3
    config.local_auth_token = os.environ["BENCH_AUTH"]
    config.command_total_timeout = 5
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
        (Path(args.directory) / "ready.json").write_text(json.dumps({"endpoint": endpoint, "pid": os.getpid()}), encoding="utf-8")

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
                    if args.cohort_gate and hasattr(PluginHub, "_readiness_reads"):
                        state.subscription_gate = True
                        original_session = PluginHub._readiness_reads.session

                        @asynccontextmanager
                        async def observed_session(key):
                            async with original_session(key) as read:
                                state.enter_call(CORRELATION.get(), subscriber=True)
                                yield read

                        PluginHub._readiness_reads.session = observed_session
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

    mcp = UnityMCP("OwnedTransportBench", lifespan=lifespan)
    mcp.add_middleware(ResponseLimitMiddleware())
    resources = ResourceContract(state, accounting, Path(args.directory)) if args.resource_contract and args.transport == "http" else None
    if resources is not None:
        resources.install(mcp)

    @mcp.tool(name="read_console")
    @traced_tool(diagnostic, 'filter_text')
    async def read_console(ctx: Context, filter_text: str) -> dict[str, JsonValue]:
        await ctx.set_state("unity_instance", INSTANCE)
        token = CORRELATION.set(filter_text)
        state.enter_call(filter_text)
        try:
            return await console.read_console(ctx, filter_text=filter_text, count=1, format="json")
        finally:
            CORRELATION.reset(token)

    @mcp.tool(name="get_test_job")
    @traced_tool(diagnostic, 'job_id')
    async def get_test_job(ctx: Context, job_id: str) -> dict[str, JsonValue]:
        await ctx.set_state("unity_instance", INSTANCE)
        token = CORRELATION.set(job_id)
        state.enter_call(job_id)
        try:
            result = await jobs.get_test_job(ctx, job_id)
            return result.model_dump()
        finally:
            CORRELATION.reset(token)

    @mcp.tool(name="get_editor_state")
    @traced_tool(diagnostic, 'correlation')
    async def get_editor_state(correlation: str) -> object:
        token = CORRELATION.set(correlation)
        state.enter_call(correlation)
        try:
            return await send_with_unity_instance(send, INSTANCE, "get_editor_state", {"benchCorrelation": correlation})
        finally:
            CORRELATION.reset(token)

    @mcp.tool(name="bench_status")
    async def status() -> dict[str, JsonValue]:
        return {"commands": dict(state.commands), "registrations": state.registrations,
                "pending": len(PluginHub._pending) if args.transport == "http" else None,
                "retained": len(PluginHub._retained_results) if args.transport == "http" else None,
                "accounting": accounting.snapshot()}

    @mcp.tool(name="bench_wait_active")
    async def wait_active(correlation: str) -> dict[str, bool]:
        await state.wait_event("active", correlation)
        return {"signalled": True}

    @mcp.tool(name="bench_wait_complete")
    async def wait_complete(correlation: str) -> dict[str, bool]:
        await state.wait_event("complete", correlation)
        if correlation == "large:partial_cancel":
            with anyio.fail_after(8):
                await state.partial_received.wait()
            return {"signalled": True, "receiver_final_chunk_processed": True}
        return {"signalled": True}

    @mcp.tool(name="bench_wait_drained")
    async def wait_drained() -> dict[str, JsonValue]:
        await accounting.wait_drained()
        return await status()

    @mcp.tool(name="bench_partial_status")
    async def partial_status() -> dict[str, JsonValue]:
        with anyio.fail_after(8):
            await state.partial_admitted.wait()
        return accounting.snapshot()

    @mcp.tool(name="bench_arm_partial")
    async def arm_partial() -> dict[str, bool]:
        state.partial_admitted, state.partial_release = anyio.Event(), anyio.Event()
        state.partial_received = anyio.Event()
        state.partial_command_id, state.partial_total_bytes = "", 0
        return {"armed": True}

    @mcp.tool(name="bench_release_partial")
    async def release_partial() -> dict[str, bool]:
        state.partial_release.set()
        return {"released": True}

    @mcp.tool(name="bench_metadata")
    async def metadata() -> dict[str, JsonValue]:
        if diagnostic is not None:
            write_sidecar(Path(args.directory) / 'diagnostic-child.json',
                          {**diagnostic.export(), 'snapshot_scope': 'pre_metadata_response'})
        return {"product_imports": product_imports(), "accounting_sampled_high_water": accounting.peaks,
                "readiness_strategy": "inflight_shared" if hasattr(PluginHub, "_readiness_reads") else "per_call",
                "ordinary_resource_strategy": "inflight_shared" if hasattr(PluginHub, "_ordinary_state_reads") else "per_call",
                "readiness_private_workloads": ["state"] if hasattr(PluginHub, "_ordinary_state_reads") else [],
                "runner": "UnityMCP", "retention_middleware": True, "pid": os.getpid(),
                "child_packages": {name: importlib.metadata.version(name) for name in
                                   ("fastmcp", "mcp", "anyio", "uvicorn", "httpx2", "websockets", "pydantic", "pydantic-core")}}

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

    if diagnostic is not None:
        batches: dict[str, tuple[int, int]] = {}

        @mcp.tool(name='bench_diagnostic_batch')
        async def diagnostic_batch(workload: str, begin: bool) -> dict[str, JsonValue]:
            if workload not in {'small', 'state', 'large', 'job'}:
                raise ValueError('Unknown diagnostic workload')
            if begin:
                if workload in batches:
                    raise ValueError('Diagnostic batch already started')
                batches[workload] = time.process_time_ns(), time.perf_counter_ns()
                diagnostic.batch = workload
                return {'started': True}
            cpu_started, wall_started = batches.pop(workload)
            # Sender completion alone is insufficient for byte ownership: both
            # peer execution and actual product delivery accounting must drain.
            for index in range(args.samples):
                await state.wait_event('complete', f'{workload}:warm:{index}')
            await accounting.wait_drained()
            diagnostic.batch = ''
            return {'process_cpu_ms': (time.process_time_ns() - cpu_started) / 1e6,
                    'wall_ms': (time.perf_counter_ns() - wall_started) / 1e6,
                    'scope': 'whole_child_process_including_owned_peers_all_threads_and_controls',
                    'peer_completed_and_accounting_drained': True}

    match args.transport:
        case "stdio":
            await mcp.run_async(transport="stdio", show_banner=False)
        case "http":
            app = mcp.http_app(path="/mcp", json_response=True)
            app.routes.append(WebSocketRoute("/hub", PluginHub))
            if resources is not None:
                app.add_middleware(ResourceHold, contract=resources)
            server = uvicorn.Server(uvicorn.Config(app, log_level="error", access_log=False))
            try:
                await server.serve(sockets=[listener])
            finally:
                if listener is not None:
                    listener.close()


async def main(args: argparse.Namespace) -> None:
    """OFF installs no diagnostic patches; ON restores every seam even on failure."""
    if not args.diagnostic:
        await serve(args, None)
        return
    require_sdk()
    diagnostic = Trace()
    import transport.response_limit_middleware as limits
    import transport.stdio_response_delivery as delivery

    def command_label(identity: str) -> str:
        return diagnostic.aliases.get(identity, 'unity:' + identity)

    try:
        with ExitStack() as patches:
            if args.transport == 'http':
                patches.enter_context(patch(PluginHub, '_handle_command_result', ('self', 'websocket', 'payload'),
                    diagnostic, 'hub_command_result', correlation=lambda _hub, _ws, payload: command_label(payload.id)))
                def large_label(_hub, _ws, data) -> str:
                    identity = data[4:40].decode('ascii', errors='replace') if isinstance(data, bytes) else str(data.get('id', ''))
                    return command_label(identity)
                patches.enter_context(patch(PluginHub, '_handle_large_result', ('self', 'websocket', 'data'),
                    diagnostic, 'hub_large_result', correlation=large_label))
            patches.enter_context(patch(limits, 'response_size',
                ('value', 'max_bytes', 'max_depth', 'max_nodes', 'max_retained'),
                diagnostic, 'mcp_envelope_validation', synchronous=True))
            if args.transport == 'stdio':
                def rpc_label(_stream, message) -> str:
                    identity = getattr(message.message, 'id', None)
                    return f'rpc:{type(identity).__name__}:{identity}'
                patches.enter_context(patch(delivery.DeliverySendStream, 'send', ('self', 'message'),
                    diagnostic, 'stdio_handoff', correlation=rpc_label))
                patches.enter_context(patch(delivery._BinaryWriteOperation, 'run', ('self',),
                    diagnostic, 'stdio_write_flush', synchronous=True))
            await serve(args, diagnostic)
    finally:
        # A hard OS kill can bypass Python finally; metadata provides the last
        # successful snapshot. Graceful/error exits also preserve the latest one.
        write_sidecar(Path(args.directory) / 'diagnostic-child.json',
                      {**diagnostic.export(), 'snapshot_scope': 'server_finally_after_patch_restore'})


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--transport", choices=("stdio", "http"), required=True)
    parser.add_argument("--directory", required=True)
    parser.add_argument("--large-bytes", type=int, required=True)
    parser.add_argument("--work-ms", type=float, required=True)
    parser.add_argument("--product-root", type=Path, required=True)
    parser.add_argument("--concurrency", type=int, required=True)
    parser.add_argument("--samples", type=int, required=True)
    parser.add_argument("--cohort-gate", action="store_true")
    parser.add_argument("--resource-contract", action="store_true")
    parser.add_argument('--diagnostic', action='store_true')
    args = parser.parse_args()
    anyio.run(main, args)
