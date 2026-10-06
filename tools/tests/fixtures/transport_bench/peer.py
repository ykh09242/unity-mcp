"""Owned synthetic Unity wire peers; never discover or contact an Editor."""
from __future__ import annotations

import json
import hashlib
import hmac
import secrets
import socket
import socketserver
import struct
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

import anyio
import websockets
from pydantic import JsonValue
from websockets.asyncio.client import ClientConnection
from websockets.exceptions import ConnectionClosed

from tools.tests.fixtures.transport_bench.workload import PROJECT_HASH, PeerRequest, PeerTiming, make_result


@dataclass(slots=True)
class PeerState:
    """Mutable counters and connection leases belong to this fixture run."""
    timing_path: Path
    large_bytes: int
    work_ms: float
    auth_token: str
    server_generation: str = field(default_factory=lambda: secrets.token_hex(16))
    commands: dict[str, int] = field(default_factory=dict)
    command_correlations: dict[str, str] = field(default_factory=dict)
    registrations: int = 0
    websocket: ClientConnection | None = None
    registered: anyio.Event = field(default_factory=anyio.Event)
    write_lock: threading.Lock = field(default_factory=threading.Lock)

    def mark_active(self, request: PeerRequest) -> None:
        with self.write_lock:
            with self.timing_path.with_name("active.jsonl").open("a", encoding="utf-8") as stream:
                stream.write(json.dumps({"correlation": request.correlation, "command": request.name}) + "\n")

    def serialize(self, request: PeerRequest, queue_ms: float, work_ms: float) -> bytes:
        started = time.perf_counter()
        result: dict[str, JsonValue] = ({"message": "pong"} if request.name == "ping"
                                        else make_result(request.workload, self.large_bytes))
        envelope: dict[str, JsonValue] = {"status": "success", "result": result}
        if request.id:
            envelope = {"type": "command_result", "id": request.id, "result": envelope}
        payload = json.dumps(envelope, separators=(",", ":")).encode()
        timing = PeerTiming(correlation=request.correlation, queue_ms=queue_ms,
                            synthetic_unity_work_ms=work_ms,
                            peer_serialization_ms=(time.perf_counter() - started) * 1000,
                            response_bytes=len(payload))
        with self.write_lock:
            self.commands[request.name] = self.commands.get(request.name, 0) + 1
            with self.timing_path.open("a", encoding="utf-8") as stream:
                stream.write(timing.model_dump_json() + "\n")
        return payload


def read_exact(connection: socket.socket, size: int) -> bytes:
    chunks = bytearray()
    while len(chunks) < size:
        piece = connection.recv(size - len(chunks))
        if not piece:
            raise EOFError("Owned TCP peer closed")
        chunks.extend(piece)
    return bytes(chunks)


class TcpPeer(socketserver.ThreadingTCPServer):
    """Ephemeral listener serving the production legacy frame format."""
    daemon_threads = True

    def __init__(self, state: PeerState) -> None:
        self.state = state
        super().__init__(("127.0.0.1", 0), TcpHandler)


class TcpHandler(socketserver.BaseRequestHandler):
    server: TcpPeer

    def handle(self) -> None:
        self.request.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        state = self.server.state
        challenge = secrets.token_hex(32)
        banner = (f"WELCOME UNITY-MCP 2 FRAMING=1 AUTH=HMAC-SHA256 SERVER={state.server_generation} "
                  f"CHALLENGE={challenge}\n").encode("ascii")
        self.request.sendall(banner)
        auth_size = struct.unpack(">Q", read_exact(self.request, 8))[0]
        if auth_size > 1024:
            raise ValueError("Owned authentication frame too large")
        auth = json.loads(read_exact(self.request, auth_size))
        base = f"{state.server_generation}\n{challenge}\n{auth['client_nonce']}"
        proof = hmac.new(state.auth_token.encode(), ("unity-mcp-stdio-v2\nclient\n" + base).encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(proof, auth["proof"]):
            raise ValueError("Owned authentication proof mismatch")
        session_id = secrets.token_hex(16)
        server_proof = hmac.new(state.auth_token.encode(), ("unity-mcp-stdio-v2\nserver\n" + base + "\n" + session_id).encode(), hashlib.sha256).hexdigest()
        ack = json.dumps({"type": "authenticated", "version": 2, "session_id": session_id, "proof": server_proof}).encode()
        self.request.sendall(struct.pack(">Q", len(ack)) + ack)
        self.server.state.registrations += 1
        try:
            while True:
                size = struct.unpack(">Q", read_exact(self.request, 8))[0]
                raw = json.loads(read_exact(self.request, size))
                request = PeerRequest(name=raw["type"], params=raw["params"])
                self.server.state.mark_active(request)
                started = time.perf_counter()
                delay = 0.5 if ":cancel" in request.correlation else self.server.state.work_ms / 1000
                time.sleep(delay)
                payload = self.server.state.serialize(request, 0.0, (time.perf_counter() - started) * 1000)
                self.request.sendall(struct.pack(">Q", len(payload)) + payload)
        except (EOFError, ConnectionError, OSError):
            return


async def serve_websocket(state: PeerState, endpoint: str, token: str) -> None:
    """Register a real PluginHub peer and negotiate its chunked result protocol."""
    from core.local_auth import LOCAL_AUTH_HEADER
    from transport.large_result_assembler import CHUNK_PAYLOAD_BYTES, MAGIC, THRESHOLD_BYTES

    async def execute(request: PeerRequest) -> None:
        queued = time.perf_counter()
        async with gate:
            started = time.perf_counter()
            queue_ms = (started - queued) * 1000
            state.mark_active(request)
            delay = 0.5 if request.name != "ping" and ":cancel" in request.correlation else state.work_ms / 1000
            await anyio.sleep(delay)
            payload = state.serialize(request, queue_ms, (time.perf_counter() - started) * 1000)
            if len(payload) < THRESHOLD_BYTES:
                await websocket.send(payload.decode())
            else:
                chunks = (len(payload) + CHUNK_PAYLOAD_BYTES - 1) // CHUNK_PAYLOAD_BYTES
                await websocket.send(json.dumps({"type": "result_start", "id": request.id,
                                                 "total_bytes": len(payload), "chunk_count": chunks}))
                for index in range(chunks):
                    start = index * CHUNK_PAYLOAD_BYTES
                    await websocket.send(MAGIC + request.id.encode("ascii") + struct.pack(">I", start)
                                         + payload[start:start + CHUNK_PAYLOAD_BYTES])

    while True:
        try:
            async with websockets.connect(endpoint, additional_headers={LOCAL_AUTH_HEADER: token},
                                          proxy=None, compression=None, max_size=2**20) as websocket:
                json.loads(await websocket.recv())  # welcome precedes registration
                await websocket.send(json.dumps({"type": "register", "project_name": "OwnedBench",
                                                 "project_hash": PROJECT_HASH, "unity_version": "synthetic",
                                                 "capabilities": ["large_result_v1"]}))
                registered = json.loads(await websocket.recv())
                if registered["type"] != "registered":
                    raise ValueError("Owned peer registration rejected")
                state.registrations += 1
                state.websocket = websocket
                state.registered.set()
                gate = anyio.Lock()
                async with anyio.create_task_group() as group:
                    try:
                        async for message in websocket:
                            raw = json.loads(message)
                            match raw["type"]:
                                case "execute":
                                    request = PeerRequest.model_validate(raw)
                                    correlation = state.command_correlations.pop(request.id, "")
                                    if correlation:
                                        request = request.model_copy(update={"params": {**request.params, "benchCorrelation": correlation}})
                                    group.start_soon(execute, request)
                                case "ping":
                                    await websocket.send(json.dumps({"type": "pong", "session_id": registered["session_id"]}))
                                case "registered" | "tools_registered":
                                    pass
                                case other:
                                    raise ValueError(f"Unexpected owned peer message: {other}")
                    except ConnectionClosed:
                        group.cancel_scope.cancel()
        except (ConnectionClosed, OSError):
            await anyio.sleep(0.02)
