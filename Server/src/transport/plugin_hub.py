"""WebSocket hub for Unity plugin communication."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import time
import uuid
import weakref
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, ClassVar, Literal

import anyio
from starlette.endpoints import WebSocketEndpoint
from starlette.websockets import WebSocket, WebSocketState

from core.config import config
from core.constants import API_KEY_HEADER
from models.models import MCPResponse
from models.response_limits import (
    MAX_RESPONSE_BYTES,
    MAX_RESPONSE_DEPTH,
    MAX_RESPONSE_NODES,
    MAX_RESPONSE_RETAINED_BYTES,
    bounded_json_text,
    response_limit_error,
    response_size,
    ResponseOwner,
    response_owner,
)
from transport.plugin_registry import PluginRegistry
from transport.charge_ledger import ChargeLedger
from transport.json_decoder import decode_json
from transport.editor_state_store import EditorStateStore
from transport.large_result_assembler import (
    CAPABILITY as LARGE_RESULT_CAPABILITY,
    MAGIC,
    MAX_FRAME_BYTES,
    COMPRESSION_CAPABILITY,
    LargeResultAssembler,
    LargeResultProtocolError,
)
from transport.blender_timeout import blender_command_timeout, SERVER_RESPONSE_GRACE
from services.api_key_service import ApiKeyService
from services.tools.shared_tool_reads import SharedReadCapacityError, SharedToolReads

if TYPE_CHECKING:
    from fastmcp import FastMCP
from transport.models import (
    WelcomeMessage,
    RegisteredMessage,
    ExecuteCommandMessage,
    CancelCommandMessage,
    PingMessage,
    RegisterMessage,
    RegisterToolsMessage,
    PongMessage,
    CommandResultMessage,
    ResultStartMessage,
    SessionList,
    SessionDetails,
)

logger = logging.getLogger(__name__)


def _read_bounded_wait_env(name: str, default_s: float, max_s: float) -> float:
    """Read a wait-seconds env override, clamped to [0, max_s].

    The ceiling exists to keep a typo from stalling every command, but it must sit
    well above the default so explicit overrides actually take effect (#1207).
    """
    raw = os.environ.get(name)
    if raw is None:
        return max(0.0, min(default_s, max_s))
    try:
        value = float(raw)
    except ValueError as e:
        logger.warning("Invalid %s=%r, using default %s: %s", name, raw, default_s, e)
        value = default_s
    return max(0.0, min(value, max_s))


# ---------- MCP session tracking ----------
# Track stable SDK connections, rather than SDK v2's per-request sessions.
_active_mcp_sessions: weakref.WeakSet = weakref.WeakSet()


def _install_session_tracking(mcp: "FastMCP") -> None:
    """Observe client connections through FastMCP's middleware lifecycle."""
    from transport.session_tracking import SessionTrackingMiddleware

    if not any(isinstance(item, SessionTrackingMiddleware) for item in mcp.middleware):
        mcp.add_middleware(SessionTrackingMiddleware(_active_mcp_sessions))


class PluginDisconnectedError(RuntimeError):
    """Raised when a plugin WebSocket disconnects while commands are in flight."""


class NoUnitySessionError(RuntimeError):
    """Raised when no Unity plugins are available."""


@dataclass(frozen=True, slots=True)
class _ConnectionReadIdentity:
    """Pin a read to one registered principal, socket and admission epoch."""

    session_id: str
    user_id: str | None
    socket_id: int
    generation: str
    command_epoch: int
    state_epoch: int


class InstanceSelectionRequiredError(RuntimeError):
    """Raised when the caller must explicitly select a Unity instance."""

    _SELECTION_REQUIRED = (
        "Unity instance selection is required. "
        "Call set_active_instance with Name@hash from mcpforunity://instances."
    )
    _MULTIPLE_INSTANCES = (
        "Multiple Unity instances are connected. "
        "Call set_active_instance with Name@hash from mcpforunity://instances."
    )

    def __init__(self, message: str | None = None, available_instances: list[str] | None = None):
        # Carried structurally so the transport layer can surface the ids without
        # parsing the message; also appended to the text for parity with the stdio
        # guard, which lists the ids inline.
        self.available_instances = available_instances or []
        text = message or self._SELECTION_REQUIRED
        if self.available_instances:
            text = f"{text} Available instances: {self.available_instances}."
        super().__init__(text)


class PluginHub(WebSocketEndpoint):
    """Manages persistent WebSocket connections to Unity plugins."""

    # configure() creates the lock and entry points guard its lifecycle. Pylint
    # only infers the class attribute's initial None value in async-with blocks.
    # pylint: disable=not-async-context-manager

    encoding = None  # Application bounds must precede JSON decoding.
    KEEP_ALIVE_INTERVAL = 15
    SERVER_TIMEOUT = 30
    COMMAND_TIMEOUT = 30
    # Server-side ping interval (seconds) - how often to send pings to Unity
    PING_INTERVAL = 10
    # Max time (seconds) to wait for pong before considering connection dead
    PING_TIMEOUT = 20
    # Timeout (seconds) for fast-fail commands like ping/read_console/get_editor_state.
    # Keep short so MCP clients aren't blocked during Unity compilation/reload/unfocused throttling.
    FAST_FAIL_TIMEOUT = 2.0
    # Fast-path commands should never block the client for long; return a retry hint instead.
    # This helps avoid the Cursor-side ~30s tool-call timeout when Unity is compiling/reloading
    # or is throttled while unfocused.
    _FAST_FAIL_COMMANDS: set[str] = {"read_console", "get_editor_state", "ping"}

    _registry: PluginRegistry | None = None
    _mcp: FastMCP | None = None
    # Index into mcp._transforms where Unity's server-level overrides start.
    # Transforms before this index are startup defaults; at and after are Unity syncs.
    _unity_transform_start: int | None = None
    _connections: dict[str, WebSocket] = {}
    # command_id -> {"future": Future, "session_id": str}
    _pending: dict[str, dict[str, Any]] = {}
    _lock: asyncio.Lock | None = None
    _loop: asyncio.AbstractEventLoop | None = None
    # session_id -> last pong timestamp (monotonic)
    _last_pong: ClassVar[dict[str, float]] = {}
    # session_id -> ping task
    _ping_tasks: ClassVar[dict[str, asyncio.Task]] = {}
    CLOSE_TIMEOUT = 5.0
    CANCEL_TIMEOUT = 1.0
    # Bound both retained tasks and their request/model/JSON working set. These
    # ceilings are independent of execution deadlines for legitimate long jobs.
    MAX_PENDING_COMMANDS = 256
    MAX_PENDING_PER_USER = 32
    MAX_PENDING_PER_SESSION = 16
    MAX_PENDING_PAYLOAD_BYTES = 32 * 1024 * 1024
    MAX_PENDING_PAYLOAD_BYTES_PER_USER = 8 * 1024 * 1024
    MAX_PENDING_PAYLOAD_BYTES_PER_SESSION = 4 * 1024 * 1024
    MAX_COMMAND_PAYLOAD_BYTES = 4 * 1024 * 1024
    MAX_COMMAND_PAYLOAD_NODES = 65_536
    MAX_COMMAND_PAYLOAD_DEPTH = 64
    MAX_RAW_MESSAGE_BYTES = MAX_RESPONSE_BYTES
    MAX_RESULT_BYTES = MAX_RESPONSE_BYTES
    MAX_RESULT_DEPTH = MAX_RESPONSE_DEPTH
    MAX_RESULT_NODES = MAX_RESPONSE_NODES
    MAX_RESULT_RETAINED_BYTES = MAX_RESPONSE_RETAINED_BYTES
    MAX_RETAINED_RESULT_BYTES_PER_SESSION = 256 * 1024 * 1024
    MAX_RETAINED_RESULT_BYTES_PER_USER = 512 * 1024 * 1024
    MAX_RETAINED_RESULT_BYTES = 1024 * 1024 * 1024
    REGISTRATION_TIMEOUT = 10.0
    _admitted: ClassVar[dict[int, tuple[WebSocket, str | None]]] = {}
    # Independent of routing/pending maps: disconnect must not release a result
    # still owned by the command coroutine during cancellation-sensitive cleanup.
    _retained_results: ClassVar[ChargeLedger] = ChargeLedger()
    CANCELLATION_CAPABILITY = "command_cancel_v1"
    CAPABILITIES = frozenset(
        {
            "editor_state_v1",
            LARGE_RESULT_CAPABILITY,
            COMPRESSION_CAPABILITY,
            CANCELLATION_CAPABILITY,
        }
    )
    _editor_states: ClassVar[EditorStateStore] = EditorStateStore()
    # No completed-result TTL: only callers overlapping an actual RPC share it.
    _readiness_reads: ClassVar[SharedToolReads[dict[str, Any]]] = SharedToolReads()
    _ordinary_state_reads: ClassVar[SharedToolReads[dict[str, Any]]] = SharedToolReads()
    _NON_MUTATING_COMMANDS = frozenset({"ping", "get_editor_state", "get_test_job"})
    _large_results: ClassVar[LargeResultAssembler | None] = None
    _raw_results: ClassVar[ChargeLedger] = ChargeLedger()

    @classmethod
    def _supports(cls, websocket: WebSocket, capability: str) -> bool:
        features = getattr(websocket.state, "plugin_features", ())
        return isinstance(features, (set, frozenset)) and capability in features

    @classmethod
    def _transfer_pending(cls, generation: str, command_id: str) -> bool:
        entry = cls._pending.get(command_id)
        if entry is None or entry["future"].done():
            return False
        websocket = cls._connections.get(entry["session_id"])
        return (
            websocket is not None
            and getattr(websocket.state, "plugin_generation", None) == generation
        )

    @classmethod
    def _has_result_capacity(cls, entry: Mapping[str, Any], charge: int) -> bool:
        user_id, session_id = entry.get("user_id"), entry["session_id"]
        total = cls._retained_results.total_bytes + cls._raw_results.total_bytes
        user_total = cls._retained_results.user_bytes(user_id) + cls._raw_results.user_bytes(
            user_id
        )
        session_total = cls._retained_results.session_bytes(
            session_id
        ) + cls._raw_results.session_bytes(session_id)
        return (
            total + charge <= cls.MAX_RETAINED_RESULT_BYTES
            and user_total + charge <= cls.MAX_RETAINED_RESULT_BYTES_PER_USER
            and session_total + charge <= cls.MAX_RETAINED_RESULT_BYTES_PER_SESSION
        )

    @classmethod
    def _reserve_transfer(cls, generation: str, command_id: str, size: int) -> bool:
        return cls._reserve_compressed_transfer(generation, command_id, size, 0)

    @classmethod
    def _reserve_compressed_transfer(
        cls,
        generation: str,
        command_id: str,
        size: int,
        working_bytes: int,
        *,
        ledger: ChargeLedger | None = None,
    ) -> bool:
        ledger = cls._raw_results if ledger is None else ledger
        if ledger is not cls._raw_results:
            return False
        entry = cls._pending.get(command_id)
        # The bytearray and its decoded Unicode text coexist during validation.
        # Inflation reserves its bounded native/output working set before allocation.
        charge = 5 * size + 4096 + working_bytes
        if entry is None or not cls._has_result_capacity(entry, charge):
            return False
        ledger[generation, command_id] = {
            "bytes": charge,
            "user_id": entry.get("user_id"),
            "session_id": entry["session_id"],
        }
        return True

    @classmethod
    def _release_transfer(cls, generation: str, command_id: str) -> None:
        cls._raw_results.pop((generation, command_id), None)

    @classmethod
    def _discard_socket_state(cls, websocket: WebSocket) -> None:
        session_id = getattr(websocket.state, "plugin_session_id", None)
        if isinstance(session_id, str):
            cls._editor_states.remove_session(session_id)
        generation = getattr(websocket.state, "plugin_generation", None)
        if cls._large_results is not None and isinstance(generation, str):
            cls._large_results.discard_owner(generation)

    @classmethod
    def _expire_large_results(cls) -> None:
        if cls._large_results is None:
            return
        for generation, command_id in cls._large_results.expire():
            if cls._transfer_pending(generation, command_id):
                cls._pending[command_id]["future"].set_result(
                    response_limit_error("result_transfer_timeout")
                )

    async def dispatch(self) -> None:
        """Own admission through connect failures, registration and disconnect."""
        websocket = WebSocket(self.scope, receive=self.receive, send=self.send)
        close_code = 1000
        try:
            # Include welcome write in the registration deadline.
            deadline = asyncio.get_running_loop().time() + self.REGISTRATION_TIMEOUT
            await asyncio.wait_for(self.on_connect(websocket), self.REGISTRATION_TIMEOUT)
            while websocket.application_state == WebSocketState.CONNECTED:
                registered = getattr(websocket.state, "plugin_registered", False)
                message = data = None
                try:
                    if registered:
                        message = await websocket.receive()
                    else:
                        remaining = max(0.0, deadline - asyncio.get_running_loop().time())
                        message = await asyncio.wait_for(websocket.receive(), remaining)
                    if message["type"] == "websocket.disconnect":
                        close_code = int(message.get("code") or 1000)
                        break
                    if message["type"] == "websocket.receive":
                        data = await self.decode(websocket, message)
                        if websocket.application_state == WebSocketState.CONNECTED:
                            if registered:
                                await self.on_receive(websocket, data)
                            else:
                                remaining = max(0.0, deadline - asyncio.get_running_loop().time())
                                await asyncio.wait_for(self.on_receive(websocket, data), remaining)
                finally:
                    # Delivery owns accepted results; idle dispatch must not
                    # retain an uncharged raw frame or its decoded graph.
                    message = data = None
        except asyncio.TimeoutError:
            close_code = 4408
            try:
                await asyncio.wait_for(
                    websocket.close(code=close_code, reason="Plugin registration timeout"),
                    self.CLOSE_TIMEOUT,
                )
            except (asyncio.TimeoutError, RuntimeError):
                pass
        finally:
            # Level cancellation may interrupt every await: release admission
            # synchronously, then shield the bounded routing cleanup.
            type(self)._admitted.pop(id(websocket), None)
            with anyio.CancelScope(shield=True):
                await self.on_disconnect(websocket, close_code)

    async def decode(self, websocket: WebSocket, message: dict[str, Any]) -> Any:
        raw = message.get("text")
        if raw is None:
            raw = message.get("bytes")
        if not isinstance(raw, (str, bytes)):
            await websocket.close(code=1003, reason="Malformed plugin message")
            return None
        if isinstance(raw, bytes) and raw.startswith(MAGIC):
            if not self._supports(websocket, LARGE_RESULT_CAPABILITY):
                await websocket.close(code=4400, reason="Large results were not negotiated")
                return None
            if len(raw) > MAX_FRAME_BYTES:
                await websocket.close(
                    code=1009, reason="Large result frame exceeds supported limits"
                )
                return None
            return raw
        text = bounded_json_text(
            raw,
            max_bytes=self.MAX_RAW_MESSAGE_BYTES,
            max_depth=self.MAX_RESULT_DEPTH,
            max_nodes=self.MAX_RESULT_NODES,
        )
        if text is None:
            await websocket.close(code=1009, reason="Plugin message exceeds supported limits")
            return None
        try:
            # Subclass decode hooks are part of the existing bounded-text path.
            return (
                decode_json(raw, fallback_text=text)
                if type(raw) in (str, bytes)
                else json.loads(text)
            )
        except (ValueError, RecursionError):
            await websocket.close(code=1003, reason="Malformed plugin message")
            return None

    @classmethod
    def _command_payload_size(cls, command_type: str, params: dict[str, Any]) -> int | None:
        """Conservatively charge JSON inputs without allocating a serialized copy.

        Count retained Python objects, model/dump copies and worst-case JSON
        string escapes. Stop traversing at the budget, node or depth ceiling;
        even a cyclic or very wide input cannot cause unbounded work here.
        """
        size = 1024  # Per-command future/task/message overhead.
        nodes = 0

        def visit(value: Any, depth: int) -> bool:
            nonlocal size, nodes
            nodes += 1
            if depth > cls.MAX_COMMAND_PAYLOAD_DEPTH or nodes > cls.MAX_COMMAND_PAYLOAD_NODES:
                return False
            size += 128 + 2 * sys.getsizeof(value)
            if isinstance(value, str):
                # Supplementary code points can escape as two six-byte surrogates.
                size += 12 * len(value)
            if size > cls.MAX_COMMAND_PAYLOAD_BYTES:
                return False
            if isinstance(value, dict):
                return all(
                    visit(key, depth + 1) and visit(item, depth + 1) for key, item in value.items()
                )
            if isinstance(value, (list, tuple)):
                return all(visit(item, depth + 1) for item in value)
            return value is None or isinstance(value, (str, bool, int, float))

        return size if visit(command_type, 0) and visit(params, 0) else None

    @staticmethod
    def _command_capacity_response() -> dict[str, Any]:
        return MCPResponse(
            success=False,
            error="Unity command capacity reached; please retry",
            hint="retry",
            data={"reason": "command_capacity", "retry_after_ms": 250},
        ).model_dump()

    @classmethod
    def configure(
        cls,
        registry: PluginRegistry,
        loop: asyncio.AbstractEventLoop | None = None,
        mcp: FastMCP | None = None,
    ) -> None:
        previous_registry = cls._registry
        cls._registry = registry
        if previous_registry is not None and previous_registry is not registry:
            previous_registry.notify_changed()
        cls._mcp = mcp
        cls._loop = loop or asyncio.get_running_loop()
        # Ensure coordination primitives are bound to the configured loop
        cls._lock = asyncio.Lock()
        cls._editor_states = EditorStateStore()
        cls._readiness_reads = SharedToolReads()
        cls._ordinary_state_reads = SharedToolReads()
        cls._raw_results = ChargeLedger()
        raw_ledger = cls._raw_results

        def reserve(generation: str, command_id: str, size: int) -> bool:
            return cls._reserve_compressed_transfer(
                generation, command_id, size, 0, ledger=raw_ledger
            )

        def reserve_compressed(
            generation: str, command_id: str, size: int, working_bytes: int
        ) -> bool:
            return cls._reserve_compressed_transfer(
                generation, command_id, size, working_bytes, ledger=raw_ledger
            )

        def release(generation: str, command_id: str) -> None:
            raw_ledger.pop((generation, command_id), None)

        cls._large_results = LargeResultAssembler(
            cls._transfer_pending, reserve, release, reserve_compressed=reserve_compressed
        )
        # Start tracking MCP client sessions for tool-change notifications
        if mcp is not None:
            _install_session_tracking(mcp)

    @classmethod
    def is_configured(cls) -> bool:
        return cls._registry is not None and cls._lock is not None

    @classmethod
    async def _close_websocket(cls, websocket: WebSocket) -> None:

        try:
            # Closure is owned cleanup, including inside a cancelled ASGI scope.
            with anyio.CancelScope(shield=True):
                await asyncio.wait_for(websocket.close(code=1001), timeout=cls.CLOSE_TIMEOUT)
        except Exception:
            logger.debug("Failed to close plugin WebSocket", exc_info=True)

    @classmethod
    async def shutdown(cls) -> None:
        """Release the plugin resources owned by the current server lifespan."""
        lock = cls._lock
        if lock is None:
            return
        async with lock:
            registry = cls._registry
            sockets = {id(ws): ws for ws in cls._connections.values()}
            sockets.update({key: entry[0] for key, entry in cls._admitted.items()})
            connections = list(sockets.values())
            ping_tasks = list(cls._ping_tasks.values())
            pending = list(cls._pending.values())
            for websocket in connections:
                cls._discard_socket_state(websocket)
            cls._editor_states.reset()
            cls._large_results = None
            cls._raw_results.clear()
            cls._connections.clear()
            cls._ping_tasks.clear()
            cls._last_pong.clear()
            cls._pending.clear()
            cls._admitted.clear()
            cls._registry = None
            if registry is not None:
                registry.notify_changed()
            cls._mcp = None
            cls._loop = None
            cls._lock = None
            cls._unity_transform_start = None

        for entry in pending:
            future = entry.get("future")
            if future is not None and not future.done():
                future.set_exception(PluginDisconnectedError("Unity plugin server shut down"))
        for task in ping_tasks:
            task.cancel()
        await asyncio.gather(*ping_tasks, return_exceptions=True)
        if registry is not None:
            await registry.clear()
        await asyncio.gather(*(cls._close_websocket(ws) for ws in connections))

    async def on_connect(self, websocket: WebSocket) -> None:
        cls = type(self)
        lock = cls._lock
        registry = cls._registry
        if lock is None or registry is None:
            await websocket.close(code=1013, reason="Plugin server unavailable")
            return
        async with lock:
            # Include sockets admitted before registration and compatibility
            # connections inserted directly by an embedding application.
            sockets = set(cls._admitted) | {id(ws) for ws in cls._connections.values()}
            accepted = len(sockets) < registry.MAX_SESSIONS
            if accepted:
                cls._admitted[id(websocket)] = (websocket, None)
        if not accepted:
            await websocket.close(code=4429, reason="Plugin connection limit reached")
            return
        try:
            await self._connect_authenticated(websocket)
        except BaseException:
            # Connect sits outside Starlette's normal disconnect finally.
            cls._admitted.pop(id(websocket), None)
            raise
        if websocket.application_state != WebSocketState.CONNECTED:
            cls._admitted.pop(id(websocket), None)

    async def _connect_authenticated(self, websocket: WebSocket) -> None:
        # Validate API key in remote-hosted mode (fail closed)
        if config.http_remote_hosted:
            if not ApiKeyService.is_initialized():
                logger.debug("WebSocket connection rejected: auth service not initialized")
                await websocket.close(code=1013, reason="Try again later")
                return

            api_key = websocket.headers.get(API_KEY_HEADER)

            if not api_key:
                logger.debug("WebSocket connection rejected: API key required")
                await websocket.close(code=4401, reason="API key required")
                return

            service = ApiKeyService.get_instance()
            source_id = websocket.client.host if websocket.client is not None else "unknown"
            result = await service.validate(api_key, source_id=source_id)

            if result.overloaded:
                await websocket.close(code=1013, reason="Authentication temporarily busy")
                return

            if not result.valid:
                # Transient auth failures are retryable (1013)
                if result.error and any(
                    indicator in result.error.lower()
                    for indicator in ("unavailable", "timeout", "service error")
                ):
                    logger.debug("WebSocket connection rejected: auth service unavailable")
                    await websocket.close(code=1013, reason="Try again later")
                    return

                logger.debug("WebSocket connection rejected: invalid API key")
                await websocket.close(code=4403, reason="Invalid API key")
                return

            # Both valid and user_id must be present to accept
            if not result.user_id:
                logger.debug("WebSocket connection rejected: validated key missing user_id")
                await websocket.close(code=4403, reason="Invalid API key")
                return

            # Store user_id in websocket state for later use during registration
            websocket.state.user_id = result.user_id
            websocket.state.api_key_metadata = result.metadata

        cls = type(self)
        user_id = getattr(websocket.state, "user_id", None)
        lock = cls._lock
        registry = cls._registry
        if lock is None or registry is None:
            await websocket.close(code=1013, reason="Plugin server unavailable")
            return
        async with lock:
            # Authentication never changes; only its validated principal owns
            # per-user capacity. Local sockets share the local principal.
            user_count = sum(
                owner == user_id for ws, owner in cls._admitted.values() if ws is not websocket
            )
            accepted = user_count < registry.MAX_SESSIONS_PER_USER
            if accepted:
                cls._admitted[id(websocket)] = (websocket, user_id)
        if not accepted:
            await websocket.close(code=4429, reason="Plugin user connection limit reached")
            return
        await websocket.accept()
        msg = WelcomeMessage(
            serverTimeout=self.SERVER_TIMEOUT,
            keepAliveInterval=self.KEEP_ALIVE_INTERVAL,
            capabilities=sorted(self.CAPABILITIES),
        )
        await websocket.send_json(msg.model_dump())

    async def on_receive(self, websocket: WebSocket, data: Any) -> None:
        if isinstance(data, bytes):
            await self._handle_large_result(websocket, data)
            return
        if not isinstance(data, dict):
            logger.warning("Received non-object payload from plugin")
            await websocket.close(code=4400, reason="Plugin registration required")
            return

        message_type = data.get("type")
        if not getattr(websocket.state, "plugin_registered", False) and message_type != "register":
            await websocket.close(code=4400, reason="Plugin registration must be first message")
            return
        try:
            if message_type == "register":
                await self._handle_register(websocket, RegisterMessage(**data))
            elif message_type == "register_tools":
                await self._handle_register_tools(websocket, RegisterToolsMessage(**data))
            elif message_type == "pong":
                await self._handle_pong(websocket, PongMessage(**data))
            elif message_type == "command_result":
                await self._handle_command_result(websocket, CommandResultMessage(**data))
            elif message_type == "result_start":
                await self._handle_large_result(websocket, data)
            elif message_type == "editor_state":
                await self._handle_editor_state(websocket, data)
            else:
                logger.debug("Ignoring unrecognized plugin message")
        except Exception as e:
            logger.error("Error handling plugin message (%s)", type(e).__name__)
            if not getattr(websocket.state, "plugin_registered", False):
                await websocket.close(code=4400, reason="Invalid plugin registration")

    async def on_disconnect(self, websocket: WebSocket, close_code: int) -> None:
        cls = type(self)
        cls._admitted.pop(id(websocket), None)
        lock = cls._lock
        if lock is None:
            return
        async with lock:
            cls._discard_socket_state(websocket)
            session_ids = [sid for sid, ws in cls._connections.items() if ws is websocket]
            for session_id in session_ids:
                cls._connections.pop(session_id, None)
                # Stop the ping loop for this session
                ping_task = cls._ping_tasks.pop(session_id, None)
                if ping_task and not ping_task.done():
                    ping_task.cancel()
                # Clean up last pong tracking
                cls._last_pong.pop(session_id, None)
                # Fail-fast any in-flight commands for this session to avoid waiting for COMMAND_TIMEOUT.
                pending_ids = [
                    command_id
                    for command_id, entry in cls._pending.items()
                    if entry.get("session_id") == session_id
                ]
                if pending_ids:
                    logger.debug(
                        f"Cancelling {len(pending_ids)} pending commands for disconnected session"
                    )
                for command_id in pending_ids:
                    entry = cls._pending.pop(command_id, None)
                    future = entry.get("future") if isinstance(entry, dict) else None
                    if future and not future.done():
                        future.set_exception(
                            PluginDisconnectedError(
                                f"Unity plugin session {session_id} disconnected while awaiting command_result"
                            )
                        )
                if cls._registry:
                    await cls._registry.unregister(session_id)
                logger.info(f"Plugin session {session_id} disconnected ({close_code})")

        if session_ids and not config.http_remote_hosted:
            await cls._refresh_server_tool_visibility()
            await cls._notify_mcp_tool_list_changed()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    @classmethod
    async def get_cached_editor_state(
        cls,
        unity_instance: str | None,
        user_id: str | None = None,
    ) -> dict[str, Any] | None:
        """Read only a fresh snapshot owned by the currently routed connection."""
        if cls._registry is None or cls._lock is None:
            return None
        if config.http_remote_hosted and not user_id:
            return None
        try:
            session_id = await cls._resolve_session_id(
                unity_instance, user_id=user_id, retry_on_reload=False
            )
        except (NoUnitySessionError, InstanceSelectionRequiredError):
            return None
        lock = cls._lock
        if lock is None:
            return None
        async with lock:
            websocket = cls._connections.get(session_id)
            if websocket is None or not cls._supports(websocket, "editor_state_v1"):
                return None
            if (
                websocket.application_state != WebSocketState.CONNECTED
                or websocket.client_state != WebSocketState.CONNECTED
            ):
                return None
            return cls._editor_states.get(session_id, user_id)

    @classmethod
    async def _read_identity(
        cls, session_id: str, *, authoritative: bool = False
    ) -> _ConnectionReadIdentity | None:
        """Capture only live registered identity; epochs stay bounded by sockets."""
        lock, registry = cls._lock, cls._registry
        if lock is None or registry is None:
            return None
        async with lock:
            if cls._registry is not registry:
                return None
            session = await registry.get_session(session_id)
            websocket = cls._connections.get(session_id)
            if session is None or websocket is None:
                return None
            state = websocket.state
            generation = getattr(state, "plugin_generation", None)
            command_epoch = getattr(state, "plugin_command_epoch", 0)
            state_epoch = getattr(state, "plugin_state_read_epoch", 0)
            if (
                not isinstance(generation, str)
                or not generation
                or not getattr(state, "plugin_registered", False)
                or getattr(state, "plugin_session_id", None) != session_id
                or getattr(state, "user_id", None) != session.user_id
                or type(command_epoch) is not int
                or type(state_epoch) is not int
                or websocket.client_state != WebSocketState.CONNECTED
                or websocket.application_state != WebSocketState.CONNECTED
            ):
                return None
            if authoritative:
                state_epoch += 1
                state.plugin_state_read_epoch = state_epoch
            return _ConnectionReadIdentity(
                session_id, session.user_id, id(websocket), generation, command_epoch, state_epoch
            )

    @staticmethod
    def _is_non_mutating(command_type: str, params: dict[str, Any]) -> bool:
        """Keep console get read-only; clear and ambiguous actions invalidate."""
        if command_type == "read_console":
            action = params.get("action")
            return action is None or isinstance(action, str) and action.lower() == "get"
        return command_type in PluginHub._NON_MUTATING_COMMANDS

    @staticmethod
    def _matches_read_identity(
        websocket: WebSocket, user_id: str | None, identity: _ConnectionReadIdentity
    ) -> bool:
        """Check the captured generation atomically at actual command admission."""
        state = websocket.state
        return (
            id(websocket) == identity.socket_id
            and user_id == identity.user_id
            and getattr(state, "user_id", None) == identity.user_id
            and getattr(state, "plugin_session_id", None) == identity.session_id
            and getattr(state, "plugin_generation", None) == identity.generation
            and getattr(state, "plugin_command_epoch", 0) == identity.command_epoch
            # State epochs separate joining, not admission: two independent
            # fresh reads can both finish after invalidating older flights.
            and websocket.client_state == WebSocketState.CONNECTED
            and websocket.application_state == WebSocketState.CONNECTED
        )

    @classmethod
    async def _readiness_probe(
        cls, session_id: str, identity: _ConnectionReadIdentity | None
    ) -> Literal["ready", "retry", "command_capacity", "result_capacity", "read_invalidated"]:
        """Share a pending ping, reducing its charged copy before returning."""
        owner = ResponseOwner()
        token = response_owner.set(owner)
        try:
            if identity is None:
                probe = await cls.send_command(session_id, "ping", {})
            else:
                async with cls._readiness_reads.session(identity) as read:
                    probe = await read.fetch(
                        lambda: cls.send_command(session_id, "ping", {}, _expected_read=identity)
                    )
            data = probe.get("data")
            if isinstance(data, dict):
                reason = data.get("reason")
                if isinstance(reason, str) and reason in {
                    "command_capacity",
                    "result_capacity",
                    "read_invalidated",
                }:
                    return reason
            result = probe.get("result")
            if (
                probe.get("status") == "success"
                and isinstance(result, dict)
                and result.get("message") == "pong"
            ):
                return "ready"
            return "retry"
        except SharedReadCapacityError:
            return "result_capacity"
        finally:
            response_owner.reset(token)
            owner.release()

    @classmethod
    async def send_command(
        cls,
        session_id: str,
        command_type: str,
        params: dict[str, Any],
        *,
        _expected_read: _ConnectionReadIdentity | None = None,
    ) -> dict[str, Any]:
        try:
            websocket = await cls._get_connection(session_id)
        except RuntimeError:
            if _expected_read is not None:
                return cls._unavailable_retry_response("read_invalidated")
            raise
        registry = cls._registry
        session = await registry.get_session(session_id) if registry is not None else None
        # Admission follows the registered principal, never caller parameters.
        user_id = session.user_id if session is not None else None
        # Compute a per-command timeout:
        # - fast-path commands: short timeout (encourage retry)
        # - long-running commands: allow caller to request a longer timeout via params
        unity_timeout_s = float(cls.COMMAND_TIMEOUT)
        server_wait_s = float(cls.COMMAND_TIMEOUT)
        if command_type == "blender_bridge":
            # The socket's default is 180s even when the caller omits the field.
            # Importing/placing its result in Unity happens after that socket call.
            unity_timeout_s = blender_command_timeout(params)
            server_wait_s = unity_timeout_s + SERVER_RESPONSE_GRACE
        elif command_type in cls._FAST_FAIL_COMMANDS:
            fast_timeout = float(cls.FAST_FAIL_TIMEOUT)
            unity_timeout_s = fast_timeout
            server_wait_s = fast_timeout
        else:
            # Common tools pass a requested timeout in seconds (e.g., timeout_seconds=900).
            requested = None
            try:
                if isinstance(params, dict):
                    requested = params.get("timeout_seconds", None)
                    if requested is None:
                        requested = params.get("timeoutSeconds", None)
            except Exception:
                requested = None

            if requested is not None:
                try:
                    requested_s = float(requested)
                    # Clamp to a sane upper bound to avoid accidental infinite hangs.
                    requested_s = max(1.0, min(requested_s, 60.0 * 60.0))
                    unity_timeout_s = max(unity_timeout_s, requested_s)
                    # Give the server a small cushion beyond the Unity-side timeout to account for transport overhead.
                    server_wait_s = max(server_wait_s, requested_s + 5.0)
                except Exception:
                    pass

        lock = cls._lock
        if lock is None:
            raise RuntimeError("PluginHub not configured")

        async with lock:
            # Disconnect can run between the initial lookup and this lock.
            if cls._connections.get(session_id) is not websocket:
                if _expected_read is not None:
                    return cls._unavailable_retry_response("read_invalidated")
                raise RuntimeError(f"Plugin session {session_id} not connected")
            if _expected_read is not None and not cls._matches_read_identity(
                websocket, user_id, _expected_read
            ):
                return cls._unavailable_retry_response("read_invalidated")
            # _pending is the single source of accounting: every existing pop on
            # result/cancel/timeout/disconnect/eviction/shutdown returns capacity.
            user_count = session_count = total_bytes = user_bytes = session_bytes = 0
            for entry in cls._pending.values():
                entry_bytes = entry.get("payload_bytes", 0)
                total_bytes += entry_bytes
                if entry.get("user_id") == user_id:
                    user_count += 1
                    user_bytes += entry_bytes
                if entry["session_id"] == session_id:
                    session_count += 1
                    session_bytes += entry_bytes
            if (
                len(cls._pending) >= cls.MAX_PENDING_COMMANDS
                or user_count >= cls.MAX_PENDING_PER_USER
                or session_count >= cls.MAX_PENDING_PER_SESSION
            ):
                return cls._command_capacity_response()
            payload_bytes = cls._command_payload_size(command_type, params)
            if payload_bytes is None:
                return MCPResponse(
                    success=False,
                    error="Unity command payload exceeds supported size or structure",
                    data={"reason": "command_payload_limit"},
                ).model_dump()
            if (
                total_bytes + payload_bytes > cls.MAX_PENDING_PAYLOAD_BYTES
                or user_bytes + payload_bytes > cls.MAX_PENDING_PAYLOAD_BYTES_PER_USER
                or session_bytes + payload_bytes > cls.MAX_PENDING_PAYLOAD_BYTES_PER_SESSION
            ):
                return cls._command_capacity_response()
            # Unknown/custom/batch commands may mutate. Advance before socket
            # I/O so later readers cannot join an earlier observation.
            if not cls._is_non_mutating(command_type, params):
                epoch = getattr(websocket.state, "plugin_command_epoch", 0)
                websocket.state.plugin_command_epoch = epoch + 1 if type(epoch) is int else 1
            if command_type == "get_editor_state" and _expected_read is None:
                epoch = getattr(websocket.state, "plugin_state_read_epoch", 0)
                websocket.state.plugin_state_read_epoch = epoch + 1 if type(epoch) is int else 1
            command_id = str(uuid.uuid4())
            if command_id in cls._pending:
                raise RuntimeError(f"Duplicate command id generated: {command_id}")
            future: asyncio.Future = asyncio.get_running_loop().create_future()
            result_ledger = cls._retained_results
            cls._pending[command_id] = {
                "future": future,
                "session_id": session_id,
                "user_id": user_id,
                "payload_bytes": payload_bytes,
                "response_owner": response_owner.get(),
                "result_ledger": result_ledger,
            }
            generation = getattr(websocket.state, "plugin_generation", None)

        send_task: asyncio.Task | None = None
        canceled = False
        try:
            msg = ExecuteCommandMessage(
                id=command_id,
                name=command_type,
                params=params,
                timeout=unity_timeout_s,
            )
            deadline = asyncio.get_running_loop().time() + server_wait_s
            try:
                send_task = asyncio.create_task(websocket.send_json(msg.model_dump()))
                done, _ = await asyncio.wait(
                    {send_task, future},
                    timeout=server_wait_s,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                # A disconnect must also interrupt a blocked socket write.
                if future in done:
                    return future.result()
                if send_task not in done:
                    raise asyncio.TimeoutError
                send_task.result()
                remaining = max(0.0, deadline - asyncio.get_running_loop().time())
                result = await asyncio.wait_for(future, timeout=remaining)
                return result
            except PluginDisconnectedError as exc:
                return MCPResponse(success=False, error=str(exc), hint="retry").model_dump()
            except asyncio.TimeoutError:
                canceled = True
                if command_type in cls._FAST_FAIL_COMMANDS:
                    return MCPResponse(
                        success=False,
                        error=f"Unity did not respond to '{command_type}' within {server_wait_s:.1f}s; please retry",
                        hint="retry",
                    ).model_dump()
                raise
        except asyncio.CancelledError:
            canceled = True
            raise
        finally:
            # Release synchronously on the owner loop before cancellation-sensitive
            # I/O cleanup. AnyIO level cancellation can interrupt every await here.
            cls._pending.pop(command_id, None)
            if cls._large_results is not None:
                if isinstance(generation, str):
                    cls._large_results.discard(generation, command_id)
            if not future.done():
                future.cancel()
            elif not future.cancelled():
                # A disconnect may finish the future while the socket write fails.
                future.exception()
            try:
                with anyio.move_on_after(cls.CANCEL_TIMEOUT, shield=True):
                    if send_task is not None:
                        if not send_task.done():
                            send_task.cancel()
                        await asyncio.gather(send_task, return_exceptions=True)
                        if canceled:
                            await cls._cancel_command_on_owner(
                                websocket, session_id, generation, command_id
                            )
            finally:
                # A disconnected or completed future still retains its result
                # during send-task cleanup. Only its owner returns this capacity.
                if response_owner.get() is None:
                    result_ledger.pop(command_id, None)

    @classmethod
    async def _cancel_command_on_owner(
        cls, websocket: WebSocket, session_id: str, generation: str | None, command_id: str
    ) -> None:
        # Always address the captured socket: a reconnect must never receive an
        # old command's cancellation, even if it reused the routing session ID.
        if (
            not isinstance(generation, str)
            or cls._connections.get(session_id) is not websocket
            or getattr(websocket.state, "plugin_generation", None) != generation
            or not cls._supports(websocket, cls.CANCELLATION_CAPABILITY)
        ):
            return
        try:
            await websocket.send_json(CancelCommandMessage(id=command_id).model_dump())
        except Exception:
            # Cancellation is best-effort and never replaces the caller's original
            # timeout/cancellation. The surrounding shield has a bounded deadline.
            logger.debug("Unable to deliver command cancellation to its owning plugin")

    @classmethod
    async def get_sessions(cls, user_id: str | None = None) -> SessionList:
        """Get all active plugin sessions.

        Args:
            user_id: If provided (remote-hosted mode), only return sessions for this user.
        """
        if cls._registry is None:
            return SessionList(sessions={})
        sessions = await cls._registry.list_sessions(user_id=user_id)
        return SessionList(
            sessions={
                session_id: SessionDetails(
                    project=session.project_name,
                    hash=session.project_hash,
                    unity_version=session.unity_version,
                    connected_at=session.connected_at.isoformat(),
                )
                for session_id, session in sessions.items()
            }
        )

    @classmethod
    async def get_tools_for_project(
        cls,
        project_hash: str,
        user_id: str | None = None,
    ) -> list[Any]:
        """Retrieve tools registered for an active project hash."""
        if cls._registry is None:
            return []

        session_id = await cls._registry.get_session_id_by_hash(project_hash, user_id=user_id)
        if not session_id:
            return []

        session = await cls._registry.get_session(session_id)
        if not session:
            return []

        return list(session.tools.values())

    @classmethod
    async def has_tool_for_identity(
        cls,
        identity: _ConnectionReadIdentity,
        project_hash: str,
        user_id: str,
        names: tuple[str, ...],
    ) -> bool:
        """Authorize selected membership against the captured live connection generation."""
        lock, registry = cls._lock, cls._registry
        if lock is None or registry is None or identity.user_id != user_id:
            return False
        async with lock:
            if cls._registry is not registry:
                return False
            websocket = cls._connections.get(identity.session_id)
            if websocket is None:
                return False
            state = websocket.state
            if (
                id(websocket) != identity.socket_id
                or getattr(state, "plugin_generation", None) != identity.generation
                or getattr(state, "plugin_session_id", None) != identity.session_id
                or not getattr(state, "plugin_registered", False)
                or getattr(state, "user_id", None) != user_id
                or websocket.client_state != WebSocketState.CONNECTED
                or websocket.application_state != WebSocketState.CONNECTED
            ):
                return False
            # Command/read epochs concern observation sharing, not tool permission.
            allowed = await registry.has_tool_for_session(
                identity.session_id, project_hash, user_id, names
            )
            # configure is synchronous and may replace owners while the registry
            # lock acquisition awaited, even though this old Hub lock is held.
            return (
                allowed
                and cls._registry is registry
                and cls._lock is lock
                and cls._connections.get(identity.session_id) is websocket
                and getattr(state, "plugin_generation", None) == identity.generation
                and getattr(state, "user_id", None) == user_id
                and getattr(state, "plugin_registered", False)
                and getattr(state, "plugin_session_id", None) == identity.session_id
                and websocket.client_state == WebSocketState.CONNECTED
                and websocket.application_state == WebSocketState.CONNECTED
            )

    @classmethod
    async def capture_tool_identity(
        cls, session_id: str
    ) -> _ConnectionReadIdentity | Literal[False] | None:
        """None permits never-live shims; a lost required live capture denies access."""
        websocket = cls._connections.get(session_id)
        if websocket is None:
            return None
        registry, lock = cls._registry, cls._lock
        identity = await cls._read_identity(session_id)
        if (
            identity is None
            or identity.socket_id != id(websocket)
            or cls._registry is not registry
            or cls._lock is not lock
            or cls._connections.get(session_id) is not websocket
        ):
            return False
        return identity

    @classmethod
    async def get_tool_definition(
        cls,
        project_hash: str,
        tool_name: str,
        user_id: str | None = None,
    ) -> Any | None:
        """Retrieve a specific tool definition for an active project hash."""
        if cls._registry is None:
            return None

        session_id = await cls._registry.get_session_id_by_hash(project_hash, user_id=user_id)
        if not session_id:
            return None

        session = await cls._registry.get_session(session_id)
        if not session:
            return None

        return session.tools.get(tool_name)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    async def _handle_register(self, websocket: WebSocket, payload: RegisterMessage) -> None:
        cls = type(self)
        registry = cls._registry
        lock = cls._lock
        if registry is None or lock is None:
            await websocket.close(code=1011)
            raise RuntimeError("PluginHub not configured")

        async with lock:
            registered = getattr(websocket.state, "plugin_registered", False) or getattr(
                websocket.state, "plugin_registering", False
            )
            websocket.state.plugin_registering = True
        if registered:
            await websocket.close(code=4409, reason="Plugin already registered")
            return

        project_name = payload.project_name
        project_hash = payload.project_hash
        unity_version = payload.unity_version
        project_path = payload.project_path

        if not project_hash:
            await websocket.close(code=4400)
            raise ValueError("Plugin registration missing project_hash")

        # Get user_id from websocket state (set during API key validation)
        user_id = getattr(websocket.state, "user_id", None)

        session_id = str(uuid.uuid4())
        evicted_ws = None
        async with lock:
            # The registry and routing insertion form one transaction. There is
            # no cancellation point after register mutates its maps and before
            # this socket becomes discoverable by disconnect cleanup.
            if cls._registry is not registry:
                await websocket.close(code=1013, reason="Plugin server unavailable")
                return
            try:
                session, evicted_session_id = await registry.register(
                    session_id,
                    project_name,
                    project_hash,
                    unity_version,
                    project_path,
                    user_id=user_id,
                )
            except ValueError:
                await websocket.close(code=4429, reason="Plugin session limit reached")
                return
            # Clean up the evicted session's connection, ping loop, and pending commands
            # so they don't linger as orphans after a domain-reload reconnection race.
            if evicted_session_id:
                evicted_ws = cls._connections.pop(evicted_session_id, None)
                if evicted_ws is not None:
                    cls._discard_socket_state(evicted_ws)
                old_ping = cls._ping_tasks.pop(evicted_session_id, None)
                if old_ping and not old_ping.done():
                    old_ping.cancel()
                cls._last_pong.pop(evicted_session_id, None)
                cancelled_commands = []
                for command_id, entry in list(cls._pending.items()):
                    if entry.get("session_id") == evicted_session_id:
                        future = entry.get("future")
                        if future and not future.done():
                            future.set_exception(
                                PluginDisconnectedError(
                                    f"Unity plugin session {evicted_session_id} superseded by {session_id}"
                                )
                            )
                            cancelled_commands.append(command_id)
                        cls._pending.pop(command_id, None)
                if cancelled_commands:
                    logger.info(
                        "Evicted session %s: cancelled pending commands %s",
                        evicted_session_id,
                        cancelled_commands,
                    )
                logger.info(f"Evicted previous session {evicted_session_id} for same instance")

            cls._connections[session.session_id] = websocket
            websocket.state.plugin_registered = True
            websocket.state.plugin_session_id = session_id
            websocket.state.plugin_generation = str(uuid.uuid4())
            websocket.state.plugin_command_epoch = 0
            websocket.state.plugin_state_read_epoch = 0
            accepted = cls.CAPABILITIES.intersection(payload.capabilities)
            if LARGE_RESULT_CAPABILITY not in accepted:
                accepted = accepted - {COMPRESSION_CAPABILITY}
            if "editor_state_v1" in accepted:
                if not cls._editor_states.register_session(session_id, user_id):
                    accepted = accepted - {"editor_state_v1"}
            websocket.state.plugin_features = frozenset(accepted)
            # Initialize last pong time and start ping loop for this session
            cls._last_pong[session_id] = time.monotonic()
            # Cancel any existing ping task for this session (shouldn't happen, but be safe)
            old_task = cls._ping_tasks.pop(session_id, None)
            if old_task and not old_task.done():
                old_task.cancel()
            # Start the server-side ping loop
            ping_task = asyncio.create_task(cls._ping_loop(session_id, websocket))
            cls._ping_tasks[session_id] = ping_task

        try:
            response = RegisteredMessage(session_id=session_id, capabilities=sorted(accepted))
            await websocket.send_json(response.model_dump())
        finally:
            # Eviction owns closure even if the replacement's ACK fails or is cancelled.
            if evicted_ws is not None:
                await cls._close_websocket(evicted_ws)

        if evicted_session_id and not config.http_remote_hosted:
            await cls._refresh_server_tool_visibility()
            await cls._notify_mcp_tool_list_changed()

        if user_id:
            logger.info(
                "Plugin registered: %r (%r) for user %r", project_name, project_hash, user_id
            )
        else:
            logger.info("Plugin registered: %r (%r)", project_name, project_hash)

    async def _handle_register_tools(
        self, websocket: WebSocket, payload: RegisterToolsMessage
    ) -> None:
        cls = type(self)
        registry = cls._registry
        lock = cls._lock
        if registry is None or lock is None:
            return

        if len(payload.model_dump_json().encode("utf-8")) > 512 * 1024:
            await websocket.close(code=4400, reason="Tool registration exceeds size limit")
            return

        # Find session_id for this websocket
        async with lock:
            session_id = next(
                (sid for sid, ws in cls._connections.items() if ws is websocket), None
            )

        if not session_id:
            logger.warning("Received register_tools from unknown connection")
            return

        await registry.register_tools_for_session(session_id, payload.tools)
        logger.info(f"Registered {len(payload.tools)} tools for session {session_id}")

        # Hosted catalogs are read from the authenticated plugin session through
        # custom_tools / execute_custom_tool, never installed process-wide.
        if config.http_remote_hosted:
            return

        # Sync server-level FastMCP visibility so new MCP client sessions
        # (e.g. new Claude Code conversations) see the correct tool set.
        await cls._refresh_server_tool_visibility()

        try:
            from services.custom_tool_service import CustomToolService

            service = CustomToolService.get_instance()
            service.register_global_tools(payload.tools)
        except RuntimeError as exc:
            logger.debug(
                "Skipping global custom tool registration: CustomToolService not initialized yet (%s)",
                type(exc).__name__,
            )
        except Exception as exc:
            logger.warning(
                "Unexpected error during global custom tool registration; "
                "custom tools may not be available globally (%s)",
                type(exc).__name__,
            )

        # Publish custom tools before clients can re-fetch the changed catalog.
        await cls._notify_mcp_tool_list_changed()

    @classmethod
    async def _refresh_server_tool_visibility(cls) -> None:
        """Keep local server inventory large enough for every registered project."""
        registry = cls._registry
        if config.http_remote_hosted or registry is None:
            return
        sessions = await registry.list_sessions()
        if not sessions:
            mcp = cls._mcp
            if mcp is not None and cls._unity_transform_start is not None:
                mcp._transforms = mcp._transforms[: cls._unity_transform_start]
                cls._unity_transform_start = None
            return
        cls._sync_server_tool_visibility(
            [tool for session in sessions.values() for tool in session.tools.values()]
        )

    @classmethod
    def _sync_server_tool_visibility(cls, registered_tools: list) -> None:
        """Sync FastMCP server-level tool group visibility to match Unity's state.

        When Unity sends ``register_tools``, some groups may have been toggled
        on/off via the Unity Editor GUI.  We mirror that state at the FastMCP
        server level so that **new** MCP client sessions (e.g. a fresh Claude
        Code conversation) see the correct tool set without requiring
        ``manage_tools`` activation.

        The startup ``register_all_tools()`` disables non-default groups via
        ``mcp.disable(tags=...)``.  Here we append ``mcp.enable(tags=...)``
        transforms for groups that Unity has enabled, effectively overriding
        the startup defaults.  FastMCP processes transforms in order so later
        ``enable`` calls override earlier ``disable`` calls.
        """
        if config.http_remote_hosted:
            return
        mcp = cls._mcp
        if mcp is None:
            return

        try:
            from services.registry import get_group_tool_names, TOOL_GROUPS

            registered_names: set[str] = set()
            for tool in registered_tools:
                name = (
                    getattr(tool, "name", None) if not isinstance(tool, dict) else tool.get("name")
                )
                if isinstance(name, str) and name:
                    registered_names.add(name)

            group_tools = get_group_tool_names()

            # Reset Unity overrides: trim transforms back to where Unity started,
            # then re-apply based on current registered tools.
            if cls._unity_transform_start is not None:
                mcp._transforms = mcp._transforms[: cls._unity_transform_start]
            else:
                # First time: record where startup transforms end.
                cls._unity_transform_start = len(mcp._transforms)

            enabled_groups: list[str] = []
            disabled_groups: list[str] = []

            for group_name in sorted(TOOL_GROUPS.keys()):
                tool_names = group_tools.get(group_name, [])
                has_any_registered = any(n in registered_names for n in tool_names)

                if has_any_registered:
                    # Override the startup disable with an enable.
                    tag = f"group:{group_name}"
                    mcp.enable(tags={tag}, components={"tool"})
                    enabled_groups.append(group_name)
                else:
                    # Group not present in Unity's registered tools — disable it.
                    tag = f"group:{group_name}"
                    mcp.disable(tags={tag}, components={"tool"})
                    disabled_groups.append(group_name)

            if enabled_groups or disabled_groups:
                logger.info(
                    "Server-level tool visibility synced from Unity: "
                    "enabled=[%s], disabled=[%s], total_transforms=%d, unity_start=%d",
                    ", ".join(enabled_groups),
                    ", ".join(disabled_groups),
                    len(mcp._transforms),
                    cls._unity_transform_start or 0,
                )
        except Exception:
            logger.debug(
                "Failed to sync server-level tool visibility",
                exc_info=True,
            )

    @classmethod
    async def _notify_mcp_tool_list_changed(cls) -> None:
        """Send ``tools/list_changed`` to every connected MCP client session.

        After server-level tool visibility is updated (e.g. when Unity reports
        its registered tools), existing MCP clients (especially stdio-based
        ones like Claude Code) must be told to re-fetch the tool list.
        FastMCP's ``mcp.enable()``/``mcp.disable()`` update the server-level
        transforms but do **not** push notifications to already-connected
        sessions — we do that here.
        """
        sessions = list(_active_mcp_sessions)
        if not sessions:
            return
        for session in sessions:
            try:
                await session.send_tool_list_changed()
            except Exception:
                logger.debug(
                    "Failed to notify MCP session of tool list change",
                    exc_info=True,
                )
        logger.info(
            "Sent tools/list_changed notification to %d MCP session(s)",
            len(sessions),
        )

    async def _handle_editor_state(self, websocket: WebSocket, data: dict[str, Any]) -> None:
        cls = type(self)
        lock = cls._lock
        if lock is None or not cls._supports(websocket, "editor_state_v1"):
            return
        session_id = getattr(websocket.state, "plugin_session_id", None)
        async with lock:
            if cls._connections.get(session_id) is websocket:
                cls._editor_states.accept(session_id, data)

    async def _handle_large_result(
        self, websocket: WebSocket, data: bytes | dict[str, Any]
    ) -> None:
        cls = type(self)
        if not cls._supports(websocket, LARGE_RESULT_CAPABILITY):
            await websocket.close(code=4400, reason="Large results were not negotiated")
            return
        lock, assembler = cls._lock, cls._large_results
        generation = getattr(websocket.state, "plugin_generation", None)
        if lock is None or assembler is None or not isinstance(generation, str):
            return
        completed = None
        command_id = None
        try:
            async with lock:
                cls._expire_large_results()
                if isinstance(data, dict):
                    start = ResultStartMessage(**data)
                    if start.encoding == "gzip" and not cls._supports(
                        websocket, COMPRESSION_CAPABILITY
                    ):
                        raise LargeResultProtocolError("compression_not_negotiated", start.id)
                    assembler.begin(
                        generation,
                        start.id,
                        start.total_bytes,
                        start.chunk_count,
                        encoding=start.encoding,
                        decoded_bytes=start.decoded_bytes,
                    )
                    return
                completed = assembler.feed(generation, data)
                if completed is None:
                    return
                command_id = completed.command_id
                # Decode directly from bytearray: avoid a second full bytes copy.
                text = bounded_json_text(
                    completed.payload,
                    max_bytes=cls.MAX_RAW_MESSAGE_BYTES,
                    max_depth=cls.MAX_RESULT_DEPTH,
                    max_nodes=cls.MAX_RESULT_NODES,
                )
                if text is None:
                    raise LargeResultProtocolError("result_payload_limit", command_id)
                message = decode_json(completed.payload, fallback_text=text)
                if (
                    not isinstance(message, dict)
                    or message.get("type") != "command_result"
                    or message.get("id") != command_id
                ):
                    raise LargeResultProtocolError("invalid_result_envelope", command_id)
                payload = CommandResultMessage(**message)
                completed = text = message = None
            await self._handle_command_result(websocket, payload)
        except LargeResultProtocolError as exc:
            if exc.reason in {
                "result_capacity",
                "result_transfer_timeout",
            }:
                if cls._transfer_pending(generation, exc.command_id):
                    cls._pending[exc.command_id]["future"].set_result(
                        response_limit_error(exc.reason)
                    )
                return
            await websocket.close(code=4400, reason="Invalid large result transfer")
        except (ValueError, RecursionError):
            await websocket.close(code=4400, reason="Invalid large result transfer")
        finally:
            if command_id is not None:
                assembler.discard(generation, command_id)

    async def _handle_command_result(
        self, websocket: WebSocket, payload: CommandResultMessage
    ) -> None:
        cls = type(self)
        lock = cls._lock
        if lock is None:
            return
        command_id = payload.id
        result = payload.result

        if not command_id:
            logger.warning("Command result missing id")
            return

        close_offender = False
        async with lock:
            entry = cls._pending.get(command_id)
            if entry is None or cls._connections.get(entry["session_id"]) is not websocket:
                return
            future = entry.get("future")
            if future and not future.done():
                charge = response_size(
                    result,
                    max_bytes=cls.MAX_RESULT_BYTES,
                    max_depth=cls.MAX_RESULT_DEPTH,
                    max_nodes=cls.MAX_RESULT_NODES,
                    max_retained=cls.MAX_RESULT_RETAINED_BYTES,
                )
                if charge is None:
                    future.set_result(response_limit_error("result_payload_limit"))
                    count = getattr(websocket.state, "result_limit_violations", 0)
                    count = count if isinstance(count, int) else 0
                    websocket.state.result_limit_violations = count + 1
                    close_offender = count >= 1
                else:
                    user_id = entry.get("user_id")
                    if not cls._has_result_capacity(entry, charge):
                        future.set_result(response_limit_error("result_capacity"))
                        return
                    result_ledger = entry.get("result_ledger", cls._retained_results)
                    if result_ledger is not cls._retained_results:
                        future.set_result(response_limit_error("result_capacity"))
                        return
                    result_ledger[command_id] = {
                        "bytes": charge,
                        "user_id": user_id,
                        "session_id": entry["session_id"],
                    }
                    owner = entry.get("response_owner")
                    if owner is not None:
                        if owner.released:
                            result_ledger.pop(command_id, None)
                            future.set_result(response_limit_error("result_capacity"))
                            return
                        owner.entries.append((result_ledger, command_id))

                        def reserve_copy(
                            copy_owner,
                            source_id=command_id,
                            reservation=result_ledger[command_id],
                            ledger=result_ledger,
                        ):
                            # Synchronous on the same owner loop: admission and
                            # insertion cannot interleave with another producer.
                            if (
                                ledger is not cls._retained_results
                                or ledger.get(source_id) is not reservation
                                or not cls._has_result_capacity(reservation, reservation["bytes"])
                            ):
                                return False
                            copy_id = "copy:" + str(uuid.uuid4())
                            ledger[copy_id] = reservation
                            copy_owner.entries.append((ledger, copy_id))
                            copy_owner.copy_reservations.append(reserve_copy)
                            return True

                        owner.copy_reservations.append(reserve_copy)
                    future.set_result(result)
        if close_offender:
            await asyncio.wait_for(
                websocket.close(code=1009, reason="Repeated plugin result limit violation"),
                cls.CLOSE_TIMEOUT,
            )

    async def _handle_pong(self, websocket: WebSocket, payload: PongMessage) -> None:
        cls = type(self)
        registry = cls._registry
        lock = cls._lock
        if registry is None or lock is None:
            return
        session_id = payload.session_id
        if session_id:
            async with lock:
                if cls._connections.get(session_id) is not websocket:
                    return
                cls._last_pong[session_id] = time.monotonic()
                await registry.touch(session_id)

    @classmethod
    async def _ping_loop(cls, session_id: str, websocket: WebSocket) -> None:
        """Server-initiated ping loop to detect dead connections.

        Sends periodic pings to the Unity client. If no pong is received within
        PING_TIMEOUT seconds, the connection is considered dead and closed.
        This helps detect connections that die silently (e.g., Windows OSError 64).
        """
        logger.debug(f"[Ping] Starting ping loop for session {session_id}")
        try:
            while True:
                await asyncio.sleep(cls.PING_INTERVAL)

                # Check if we're still supposed to be running and get last pong time (under lock)
                lock = cls._lock
                if lock is None:
                    break
                async with lock:
                    cls._expire_large_results()
                    if session_id not in cls._connections:
                        logger.debug(
                            f"[Ping] Session {session_id} no longer in connections, stopping ping loop"
                        )
                        break
                    # Read last pong time under lock for consistency
                    last_pong = cls._last_pong.get(session_id, 0)

                # Check staleness: has it been too long since we got a pong?
                elapsed = time.monotonic() - last_pong
                if elapsed > cls.PING_TIMEOUT:
                    logger.warning(
                        f"[Ping] Session {session_id} stale: no pong for {elapsed:.1f}s "
                        f"(timeout={cls.PING_TIMEOUT}s). Closing connection."
                    )
                    await cls._evict_connection(session_id, "heartbeat_timeout")
                    break

                # Send a ping to the client
                try:
                    ping_msg = PingMessage()
                    await asyncio.wait_for(
                        websocket.send_json(ping_msg.model_dump()),
                        timeout=cls.PING_TIMEOUT,
                    )
                    logger.debug(f"[Ping] Sent ping to session {session_id}")
                except Exception as send_ex:
                    # Send failed - connection is dead
                    logger.warning(
                        f"[Ping] Failed to send ping to session {session_id}: {send_ex}. "
                        "Connection likely dead."
                    )
                    await cls._evict_connection(session_id, "heartbeat_send_failed")
                    break

        except asyncio.CancelledError:
            logger.debug(f"[Ping] Ping loop cancelled for session {session_id}")
        except Exception as ex:
            logger.warning(f"[Ping] Ping loop error for session {session_id}: {ex}")
        finally:
            logger.debug(f"[Ping] Ping loop ended for session {session_id}")

    @classmethod
    async def _get_connection(cls, session_id: str) -> WebSocket:
        lock = cls._lock
        if lock is None:
            raise RuntimeError("PluginHub not configured")
        async with lock:
            websocket = cls._connections.get(session_id)
        if websocket is None:
            raise RuntimeError(f"Plugin session {session_id} not connected")
        return websocket

    @classmethod
    async def _evict_connection(cls, session_id: str, reason: str) -> None:
        """Drop a stale session from in-memory maps and registry."""
        lock = cls._lock
        if lock is None:
            return

        websocket: WebSocket | None = None
        ping_task: asyncio.Task | None = None
        pending_futures: list[asyncio.Future] = []
        async with lock:
            registry = cls._registry
            websocket = cls._connections.pop(session_id, None)
            if websocket is not None:
                cls._discard_socket_state(websocket)
            ping_task = cls._ping_tasks.pop(session_id, None)
            cls._last_pong.pop(session_id, None)
            keys_to_remove: list[object] = []
            for key, entry in list(cls._pending.items()):
                if entry.get("session_id") == session_id:
                    future = entry.get("future")
                    if future and not future.done():
                        pending_futures.append(future)
                    keys_to_remove.append(key)
            for key in keys_to_remove:
                cls._pending.pop(key, None)

        if (
            ping_task is not None
            and ping_task is not asyncio.current_task()
            and not ping_task.done()
        ):
            ping_task.cancel()

        for future in pending_futures:
            if not future.done():
                future.set_exception(
                    PluginDisconnectedError(
                        f"Unity plugin session {session_id} disconnected while awaiting command_result"
                    )
                )

        # Remove routing state before close I/O can block or be cancelled.
        if registry is not None:
            try:
                await registry.unregister(session_id)
            except Exception:
                logger.debug(
                    "Failed to unregister evicted plugin session %s",
                    session_id,
                    exc_info=True,
                )

        if websocket is not None:
            await cls._close_websocket(websocket)

        if registry is not None and not config.http_remote_hosted:
            await cls._refresh_server_tool_visibility()
            await cls._notify_mcp_tool_list_changed()

        logger.debug("Evicted plugin session %s (%s)", session_id, reason)

    @classmethod
    async def _ensure_live_connection(cls, session_id: str) -> bool:
        """Best-effort pre-send liveness check for a plugin WebSocket."""
        try:
            websocket = await cls._get_connection(session_id)
        except RuntimeError:
            await cls._evict_connection(session_id, "missing_websocket")
            return False

        if (
            websocket.client_state == WebSocketState.CONNECTED
            and websocket.application_state == WebSocketState.CONNECTED
        ):
            return True

        logger.debug(
            "Detected stale plugin connection before send: session=%s app_state=%s client_state=%s",
            session_id,
            websocket.application_state,
            websocket.client_state,
        )
        await cls._evict_connection(session_id, "stale_websocket_state")
        return False

    @staticmethod
    def _unavailable_retry_response(reason: str = "no_unity_session") -> dict[str, Any]:
        return MCPResponse(
            success=False,
            error="Unity session not available; please retry",
            hint="retry",
            data={"reason": reason, "retry_after_ms": 250},
        ).model_dump()

    # ------------------------------------------------------------------
    # Session resolution helpers
    # ------------------------------------------------------------------
    @classmethod
    async def _resolve_session_id(
        cls,
        unity_instance: str | None,
        user_id: str | None = None,
        retry_on_reload: bool = True,
    ) -> str:
        """Resolve a project hash (Unity instance id) to an active plugin session.

        During Unity domain reloads the plugin's WebSocket session is torn down
        and reconnected shortly afterwards. Instead of failing immediately when
        no sessions are available, we wait for a bounded period for a plugin
        to reconnect so in-flight MCP calls can succeed transparently.

        Args:
            unity_instance: Target instance (Name@hash or hash)
            user_id: User ID from API key validation (for remote-hosted mode session isolation)
            retry_on_reload: If False, do not wait for reconnects when no session is present.
        """
        if cls._registry is None:
            raise RuntimeError("Plugin registry not configured")

        registry = cls._registry

        # Bound waiting for Unity sessions. Default to 20s to handle domain reloads
        # (which can take 10-20s after test runs or script changes).
        #
        # NOTE: This wait can impact agentic workflows where domain reloads happen
        # frequently (e.g., after test runs, script compilation). The 20s default
        # balances handling slow reloads vs. avoiding unnecessary delays.
        #
        # TODO: Make this more deterministic by detecting Unity's actual reload state
        # (e.g., via status file, heartbeat, or explicit "reloading" signal from Unity)
        # rather than blindly waiting up to 20s. See Issue #657.
        #
        # Configurable via: UNITY_MCP_SESSION_RESOLVE_MAX_WAIT_S (default: 20.0, max: 120.0).
        # The ceiling used to equal the default, which silently neutered the override for
        # projects whose reloads/test boundaries legitimately exceed 20s (#1207).
        max_wait_s = _read_bounded_wait_env(
            "UNITY_MCP_SESSION_RESOLVE_MAX_WAIT_S", default_s=20.0, max_s=120.0
        )
        if not retry_on_reload:
            max_wait_s = 0.0

        # Allow callers to provide either just the hash or Name@hash
        target_hash: str | None = None
        if unity_instance:
            if "@" in unity_instance:
                _, _, suffix = unity_instance.rpartition("@")
                if not suffix.strip():
                    raise NoUnitySessionError("Unity instance selector is missing its project hash")
                target_hash = suffix
            else:
                target_hash = unity_instance

        async def _try_once() -> tuple[str | None, int, bool]:
            if cls._registry is not registry:
                raise NoUnitySessionError("Unity plugin server stopped or replaced")
            explicit_required = config.http_remote_hosted
            # Prefer a specific Unity instance if one was requested
            if target_hash:
                # In remote-hosted mode with user_id, use user-scoped lookup
                if config.http_remote_hosted and user_id:
                    session_id = await registry.get_session_id_by_hash(target_hash, user_id)
                else:
                    session_id = await registry.get_session_id_by_hash(target_hash)
                if cls._registry is not registry:
                    raise NoUnitySessionError("Unity plugin server stopped or replaced")
                # Explicit selection never uses the count. Keep the remote
                # principal guard and miss paths in list_sessions below.
                if session_id is not None and (not config.http_remote_hosted or user_id):
                    return session_id, 0, explicit_required
                sessions = await registry.list_sessions(user_id=user_id)
                if cls._registry is not registry:
                    raise NoUnitySessionError("Unity plugin server stopped or replaced")
                return session_id, len(sessions), explicit_required

            # No target provided: determine if we can auto-select
            # In remote-hosted mode, filter sessions by user_id
            sessions = await registry.list_sessions(user_id=user_id)
            if cls._registry is not registry:
                raise NoUnitySessionError("Unity plugin server stopped or replaced")
            count = len(sessions)
            if count == 0:
                return None, count, explicit_required
            if explicit_required:
                return None, count, explicit_required
            if count == 1:
                return next(iter(sessions.keys())), count, explicit_required
            # Multiple sessions but no explicit target is ambiguous
            return None, count, explicit_required

        async def _available_instance_ids() -> list[str]:
            # Error path only; one extra registry read keeps the refusal actionable.
            try:
                sessions = await registry.list_sessions(user_id=user_id)
                return sorted(f"{s.project_name}@{s.project_hash}" for s in sessions.values())
            except Exception:
                return []

        deadline = time.monotonic() + max_wait_s
        changed = registry.change_event
        session_id, session_count, explicit_required = await _try_once()
        if session_id is None and explicit_required and not target_hash and session_count > 0:
            raise InstanceSelectionRequiredError(
                available_instances=await _available_instance_ids()
            )
        wait_started = None

        # If there is no active plugin yet (e.g., Unity starting up or reloading),
        # wait politely for a session to appear before surfacing an error.
        while session_id is None and time.monotonic() < deadline:
            if not target_hash and session_count > 1:
                raise InstanceSelectionRequiredError(
                    InstanceSelectionRequiredError._MULTIPLE_INSTANCES,
                    available_instances=await _available_instance_ids(),
                )
            if session_id is None and explicit_required and not target_hash and session_count > 0:
                raise InstanceSelectionRequiredError(
                    available_instances=await _available_instance_ids()
                )
            if wait_started is None:
                wait_started = time.monotonic()
                logger.debug(
                    "No plugin session available (instance=%s); waiting up to %.2fs",
                    unity_instance or "default",
                    max_wait_s,
                )
            if not await registry.wait_for_change(changed, max(0.0, deadline - time.monotonic())):
                break
            if time.monotonic() >= deadline:
                break
            changed = registry.change_event
            session_id, session_count, explicit_required = await _try_once()

        if session_id is not None and wait_started is not None:
            logger.debug(
                "Plugin session restored after %.3fs (instance=%s)",
                time.monotonic() - wait_started,
                unity_instance or "default",
            )
        if session_id is None and not target_hash and session_count > 1:
            raise InstanceSelectionRequiredError(InstanceSelectionRequiredError._MULTIPLE_INSTANCES)

        if session_id is None and explicit_required and not target_hash and session_count > 0:
            raise InstanceSelectionRequiredError()

        if session_id is None:
            logger.warning(
                "No Unity plugin reconnected within %.2fs (instance=%s)",
                max_wait_s,
                unity_instance or "default",
            )
            # At this point we've given the plugin ample time to reconnect; surface
            # a clear error so the client can prompt the user to open Unity.
            raise NoUnitySessionError("No Unity plugins are currently connected")

        return session_id

    @classmethod
    async def send_command_for_instance(
        cls,
        unity_instance: str | None,
        command_type: str,
        params: dict[str, Any],
        user_id: str | None = None,
        retry_on_reload: bool = True,
        *,
        editor_state_read_mode: Literal["ordinary", "authoritative"] | None = None,
    ) -> dict[str, Any]:
        """Send a command to a Unity instance.

        Args:
            unity_instance: Target instance (Name@hash or hash)
            command_type: Command type to execute
            params: Command parameters
            user_id: User ID for session isolation in remote-hosted mode
            retry_on_reload: If False, do not wait for session reconnect on reload.
        """
        try:
            session_id = await cls._resolve_session_id(
                unity_instance,
                user_id=user_id,
                retry_on_reload=retry_on_reload,
            )
        except NoUnitySessionError:
            logger.debug(
                "Unity session unavailable; returning retry: command=%s instance=%s",
                command_type,
                unity_instance or "default",
            )
            return cls._unavailable_retry_response("no_unity_session")

        if not await cls._ensure_live_connection(session_id):
            if not retry_on_reload:
                return cls._unavailable_retry_response("stale_connection")
            try:
                session_id = await cls._resolve_session_id(
                    unity_instance,
                    user_id=user_id,
                    retry_on_reload=True,
                )
            except NoUnitySessionError:
                return cls._unavailable_retry_response("no_unity_session")
            if not await cls._ensure_live_connection(session_id):
                return cls._unavailable_retry_response("stale_connection")

        share_state = (
            command_type == "get_editor_state"
            and not params
            and editor_state_read_mode == "ordinary"
        )
        identity = await cls._read_identity(
            session_id, authoritative=command_type == "get_editor_state" and not share_state
        )

        # During domain reload / immediate reconnect windows, the plugin may be connected but not yet
        # ready to process execute commands on the Unity main thread (which can be further delayed when
        # the Unity Editor is unfocused). For fast-path commands, we do a bounded readiness probe using
        # a main-thread ping command (handled by TransportCommandDispatcher) rather than waiting on
        # register_tools (which can be delayed by EditorApplication.delayCall).
        if retry_on_reload and command_type in cls._FAST_FAIL_COMMANDS and command_type != "ping":
            max_wait_s = _read_bounded_wait_env(
                "UNITY_MCP_SESSION_READY_WAIT_SECONDS", default_s=6.0, max_s=120.0
            )
            if max_wait_s > 0:
                deadline = time.monotonic() + max_wait_s
                while time.monotonic() < deadline:
                    try:
                        outcome = await cls._readiness_probe(session_id, identity)
                    except Exception:
                        outcome = "retry"

                    # Capacity refusal must stay cheap for readiness-gated tools
                    # too; repeated probes cannot make space for the caller.
                    if outcome == "command_capacity":
                        return cls._command_capacity_response()
                    if outcome == "result_capacity":
                        return response_limit_error("result_capacity")
                    if outcome == "read_invalidated":
                        return cls._unavailable_retry_response("read_invalidated")
                    if outcome == "ready":
                        break
                    await asyncio.sleep(0.1)
                else:
                    # Not ready within the bounded window: return retry hint without sending.
                    return MCPResponse(
                        success=False,
                        error=f"Unity session not ready for '{command_type}' (ping not answered); please retry",
                        hint="retry",
                    ).model_dump()

        if share_state and identity is not None:
            try:
                async with cls._ordinary_state_reads.session(identity) as read:
                    return await read.fetch(
                        lambda: cls.send_command(
                            session_id, command_type, params, _expected_read=identity
                        )
                    )
            except SharedReadCapacityError:
                return response_limit_error("result_capacity")
        if identity is not None and command_type in cls._FAST_FAIL_COMMANDS:
            return await cls.send_command(session_id, command_type, params, _expected_read=identity)
        return await cls.send_command(session_id, command_type, params)

    # ------------------------------------------------------------------
    # Blocking helpers for synchronous tool code
    # ------------------------------------------------------------------
    @classmethod
    def _run_coroutine_sync(cls, coro: "asyncio.Future[Any]") -> Any:
        if cls._loop is None:
            raise RuntimeError("PluginHub event loop not configured")
        loop = cls._loop
        if loop.is_running():
            try:
                running_loop = asyncio.get_running_loop()
            except RuntimeError:
                running_loop = None
            else:
                if running_loop is loop:
                    raise RuntimeError(
                        "Cannot wait synchronously for PluginHub coroutine from within the event loop"
                    )
        future = asyncio.run_coroutine_threadsafe(coro, loop)
        return future.result()

    @classmethod
    def send_command_blocking(
        cls,
        unity_instance: str | None,
        command_type: str,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        return cls._run_coroutine_sync(
            cls.send_command_for_instance(unity_instance, command_type, params)
        )

    @classmethod
    def list_sessions_sync(cls) -> SessionList:
        return cls._run_coroutine_sync(cls.get_sessions())


def send_command_to_plugin(
    *,
    unity_instance: str | None,
    command_type: str,
    params: dict[str, Any],
) -> dict[str, Any]:
    return PluginHub.send_command_blocking(unity_instance, command_type, params)
