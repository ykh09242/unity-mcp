"""WebSocket hub for Unity plugin communication."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid
import weakref
from hashlib import sha256
from collections.abc import Coroutine, Mapping
from dataclasses import dataclass
from contextlib import AsyncExitStack, asynccontextmanager, nullcontext
from typing import TYPE_CHECKING, Any, ClassVar, Literal
from collections.abc import Callable

import anyio
from pydantic import ValidationError
from starlette.endpoints import WebSocketEndpoint
from starlette.websockets import WebSocket, WebSocketState

from services.execution_receipt_context import execution_owner, observe_native_result, own_future
from services.command_progress import (
    CAPABILITY as PROGRESS_CAPABILITY,
    MAX_PROGRESS_BYTES,
    MAX_PROGRESS_FRAMES,
    CommandProgressUpdate,
    ProgressSink,
    ProgressSource,
)
from core import phase_diagnostics
from core.config import config
from core.constants import API_KEY_HEADER
from models.models import MCPResponse
from models.capabilities import CapabilityConnection
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
from transport.plugin_registry import PluginRegistry, ToolSettingsConflict
from transport.local_project_context import (
    local_project_context,
    full_hash,
    normalized_root,
    ProjectSelection,
    LocalProjectContext,
)
from transport.tool_list_notifier import ToolListNotifier
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
from transport.command_timeout import (
    requested_command_timeout,
    configured_wait_timeout,
    MAX_COMMAND_TIMEOUT,
)

# Compatibility facade: all callers share the same ContextVar and evidence types.
from transport.command_budget import (
    _CommandBudget as _CommandBudget,
    _HttpRequestBudget as _HttpRequestBudget,
    CommandEvidenceSnapshot as CommandEvidenceSnapshot,
    HttpBudgetSnapshot as HttpBudgetSnapshot,
    http_request_budget as http_request_budget,
    command_outcome_unknown as command_outcome_unknown,
    capture_http_request_budget as capture_http_request_budget,
    detached_http_request_budget as detached_http_request_budget,
    extend_http_request_deadline as extend_http_request_deadline,
)
from services.api_key_service import ApiKeyService
from services.operation_permissions import (
    OperationPermissionError,
    authorize_operation,
    required_permissions,
)
from services.credential_lease import (
    CredentialLease,
    CredentialLeaseError,
    require_credential_lease,
)
from transport.remote_auth_middleware import AUTHENTICATED_LEASE_STATE
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
    ToolSettingsCorrelation,
    ToolSettingsResult,
    TOOL_SETTINGS_CAPABILITY,
    PongMessage,
    CommandResultMessage,
    CommandProgressMessage,
    CommandProgressRequest,
    ResultStartMessage,
    SessionList,
    SessionDetails,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class _EditorStatePublication:
    """Registry-derived routing captured during the authenticated registration."""

    user_id: str | None
    project_hash: str
    session_id: str
    generation: str


def _read_bounded_wait_env(name: str, default_s: float, max_s: float) -> float:
    """Read a finite observation window without accepting NaN or infinity."""
    return configured_wait_timeout(name, default_s, max_s)


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


class CommandNotDispatchedError(RuntimeError):
    """The command was refused before any attempt to write to its plugin."""


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
    COMMAND_TIMEOUT = 300
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
    _unity_transforms: tuple = ()
    _unity_visibility_signature: tuple | None = None
    _connections: dict[str, WebSocket] = {}
    # command_id -> {"future": Future, "session_id": str}
    _pending: dict[str, dict[str, Any]] = {}
    _lock: asyncio.Lock | None = None
    _loop: asyncio.AbstractEventLoop | None = None
    # session_id -> last pong timestamp (monotonic)
    _last_pong: ClassVar[dict[str, float]] = {}
    # session_id -> ping task
    _ping_tasks: ClassVar[dict[str, asyncio.Task]] = {}
    # Recovery callers may expire before eviction finishes. Keep one owned task
    # per registry/session until completion, and drain it during shutdown.
    _eviction_tasks: ClassVar[dict[tuple[int, str], asyncio.Task]] = {}
    CLOSE_TIMEOUT = 5.0
    CANCEL_TIMEOUT = 1.0
    # Bound both retained tasks and their request/model/JSON working set. These
    # ceilings are independent of execution deadlines for legitimate long jobs.
    MAX_PENDING_COMMANDS = 256
    MAX_PENDING_PER_USER = 32
    MAX_PENDING_PER_SESSION = 16
    MAX_PENDING_PAYLOAD_BYTES = 1024 * 1024 * 1024
    MAX_PENDING_PAYLOAD_BYTES_PER_USER = 512 * 1024 * 1024
    MAX_PENDING_PAYLOAD_BYTES_PER_SESSION = 256 * 1024 * 1024
    MAX_COMMAND_PAYLOAD_BYTES = 32 * 1024 * 1024
    MAX_COMMAND_PAYLOAD_NODES = 1_000_000
    MAX_COMMAND_RETAINED_BYTES = 256 * 1024 * 1024
    MAX_COMMAND_PAYLOAD_DEPTH = 64
    MAX_RAW_MESSAGE_BYTES = MAX_RESPONSE_BYTES
    MAX_RESULT_BYTES = MAX_RESPONSE_BYTES
    MAX_RESULT_DEPTH = MAX_RESPONSE_DEPTH
    MAX_RESULT_NODES = MAX_RESPONSE_NODES
    MAX_RESULT_RETAINED_BYTES = MAX_RESPONSE_RETAINED_BYTES
    MAX_RETAINED_RESULT_BYTES_PER_SESSION = 1024 * 1024 * 1024
    MAX_RETAINED_RESULT_BYTES_PER_USER = 1536 * 1024 * 1024
    MAX_RETAINED_RESULT_BYTES = 2048 * 1024 * 1024
    REGISTRATION_TIMEOUT = 10.0
    _admitted: ClassVar[dict[int, tuple[WebSocket, str | None]]] = {}
    _shutdown_requested: ClassVar[bool] = False
    _catalog_notifications: ClassVar[ToolListNotifier] = ToolListNotifier()
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
            TOOL_SETTINGS_CAPABILITY,
            PROGRESS_CAPABILITY,
            phase_diagnostics.CAPABILITY,
        }
    )
    _editor_states: ClassVar[EditorStateStore] = EditorStateStore()
    # No completed-result TTL: only callers overlapping an actual RPC share it.
    _readiness_reads: ClassVar[SharedToolReads[dict[str, Any]]] = SharedToolReads()
    _ordinary_state_reads: ClassVar[SharedToolReads[dict[str, Any]]] = SharedToolReads()
    _ordinary_state_flights: ClassVar[weakref.WeakKeyDictionary[asyncio.Task, _CommandBudget]] = (
        weakref.WeakKeyDictionary()
    )
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
            revision = cls._editor_states.change_token(session_id)
            cls._editor_states.remove_session(session_id)
            cls._publish_editor_state(websocket, (revision or 0) + 1)
            websocket.state.plugin_editor_state_publication = None
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

    @staticmethod
    def _socket_lease(websocket: WebSocket) -> CredentialLease | None:
        if not config.http_remote_hosted:
            return None
        lease = getattr(websocket.state, AUTHENTICATED_LEASE_STATE, None)
        if (
            not isinstance(lease, CredentialLease)
            or getattr(websocket.state, "plugin_auth_retired", False)
            or lease.user_id != getattr(websocket.state, "user_id", None)
        ):
            raise CredentialLeaseError()
        return lease

    @classmethod
    @asynccontextmanager
    async def _authorized_socket_lock(cls, websocket, lock, requester=None):
        """Renew outside the Hub lock; check both exact generations without yielding inside."""
        plugin = cls._socket_lease(websocket)
        leases = tuple(lease for lease in (requester, plugin) if lease is not None)
        while True:
            for lease in leases:
                await lease.check()
            async with lock:
                try:
                    for lease in leases:
                        lease.check_now()
                except CredentialLeaseError:
                    if any(lease.retired for lease in leases):
                        raise
                    continue
                yield
                return

    @classmethod
    def _fence_plugin_owners(cls) -> None:
        """Retire old socket ownership before reconfiguration/cleanup can suspend."""
        sockets = {id(ws): ws for ws in cls._connections.values()}
        sockets.update({key: value[0] for key, value in cls._admitted.items()})
        for websocket in sockets.values():
            websocket.state.plugin_auth_retired = True
            task = getattr(websocket.state, "plugin_auth_task", None)
            if task is not None:
                task.cancel()

    async def dispatch(self) -> None:
        """Own admission through connect failures, registration and disconnect."""
        websocket = None

        async def guarded_send(message):
            lease = (
                getattr(websocket.state, AUTHENTICATED_LEASE_STATE, None)
                if websocket is not None
                else None
            )
            if isinstance(lease, CredentialLease):
                await lease.check()
                lease.check_now()
            await self.send(message)
            if isinstance(lease, CredentialLease):
                await lease.check()

        websocket = WebSocket(self.scope, receive=self.receive, send=guarded_send)
        close_code = 1000
        # Owner exit follows every close path and the actual captured disconnect cleanup.
        async with AsyncExitStack() as lifetime:
            self._credential_lifetime = lifetime
            lifetime.callback(setattr, self, "_credential_lifetime", None)
            websocket.state.plugin_auth_task = asyncio.current_task()
            try:
                # Include welcome write in the registration deadline.
                deadline = asyncio.get_running_loop().time() + self.REGISTRATION_TIMEOUT
                # Connect and cleanup must belong to the same actual owner task and
                # Context. A wait_for child cannot transfer its ContextVar token here.
                async with asyncio.timeout_at(deadline):
                    await self.on_connect(websocket)
                if websocket.application_state != WebSocketState.CONNECTED:
                    return
                lease = type(self)._socket_lease(websocket)
                while websocket.application_state == WebSocketState.CONNECTED:
                    registered = getattr(websocket.state, "plugin_registered", False)
                    message = data = None
                    try:
                        if registered:
                            if lease is not None:
                                await lease.check()
                            message = await websocket.receive()
                            if lease is not None:
                                await lease.check()
                        else:
                            remaining = max(0.0, deadline - asyncio.get_running_loop().time())
                            if lease is not None:
                                await lease.check()
                            message = await asyncio.wait_for(websocket.receive(), remaining)
                            if lease is not None:
                                await lease.check()
                        if message["type"] == "websocket.disconnect":
                            close_code = int(message.get("code") or 1000)
                            break
                        if message["type"] == "websocket.receive":
                            data = await self.decode(websocket, message)
                            if websocket.application_state == WebSocketState.CONNECTED:
                                if registered:
                                    await self.on_receive(websocket, data)
                                else:
                                    remaining = max(
                                        0.0, deadline - asyncio.get_running_loop().time()
                                    )
                                    await asyncio.wait_for(
                                        self.on_receive(websocket, data), remaining
                                    )
                    finally:
                        # Delivery owns accepted results; idle dispatch must not
                        # retain an uncharged raw frame or its decoded graph.
                        message = data = None
            except CredentialLeaseError:
                close_code = 4403
                if websocket.application_state == WebSocketState.CONNECTED:
                    with anyio.move_on_after(self.CLOSE_TIMEOUT, shield=True):
                        await self.send({"type": "websocket.close", "code": close_code})
            except asyncio.CancelledError:
                lease = getattr(websocket.state, AUTHENTICATED_LEASE_STATE, None)
                if not (isinstance(lease, CredentialLease) and lease.retired) and not getattr(
                    websocket.state, "plugin_auth_retired", False
                ):
                    raise
                close_code = 4403
                if websocket.application_state == WebSocketState.CONNECTED:
                    with anyio.move_on_after(self.CLOSE_TIMEOUT, shield=True):
                        await self.send({"type": "websocket.close", "code": close_code})
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
        """Bound wire bytes separately from the copies retained by an admitted command."""
        return response_size(
            {"type": command_type, "params": params},
            max_bytes=cls.MAX_COMMAND_PAYLOAD_BYTES,
            max_nodes=cls.MAX_COMMAND_PAYLOAD_NODES,
            max_depth=cls.MAX_COMMAND_PAYLOAD_DEPTH,
            max_retained=cls.MAX_COMMAND_RETAINED_BYTES,
        )

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
        cls._fence_plugin_owners()
        cls._catalog_notifications.reset()
        previous_registry = cls._registry
        cls._registry = registry
        if previous_registry is not None and previous_registry is not registry:
            previous_registry.notify_changed()
        if cls._mcp is not mcp:
            cls._unity_transform_start = None
            cls._unity_transforms = ()
            cls._unity_visibility_signature = None
        cls._mcp = mcp
        cls._loop = loop or asyncio.get_running_loop()
        # Ensure coordination primitives are bound to the configured loop
        cls._lock = asyncio.Lock()
        cls._shutdown_requested = False
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
        cls._fence_plugin_owners()
        await cls._catalog_notifications.close()
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
            cls._unity_transforms = ()
            cls._unity_visibility_signature = None

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
        await asyncio.gather(*list(cls._eviction_tasks.values()), return_exceptions=True)

    @classmethod
    async def request_idle_shutdown(
        cls, request_exit: Callable[[], bool]
    ) -> Literal["shutdown_requested", "sessions_active", "runner_unavailable"]:
        """Reserve shutdown against all admitted sockets under the admission lock."""
        lock, registry = cls._lock, cls._registry
        if lock is None or registry is None:
            return "runner_unavailable"
        async with lock:
            if cls._lock is not lock or cls._registry is not registry:
                return "runner_unavailable"
            if cls._shutdown_requested:
                return "shutdown_requested"
            if cls._admitted or cls._connections or await registry.list_sessions():
                return "sessions_active"
            cls._shutdown_requested = True
            try:
                if request_exit():
                    return "shutdown_requested"
            except Exception:
                logger.warning("Owned HTTP runner rejected graceful shutdown", exc_info=False)
            cls._shutdown_requested = False
            return "runner_unavailable"

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
            unavailable = (
                cls._shutdown_requested or cls._registry is not registry or cls._lock is not lock
            )
            accepted = not unavailable and len(sockets) < registry.MAX_SESSIONS
            if accepted:
                cls._admitted[id(websocket)] = (websocket, None)
        if not accepted:
            await websocket.close(
                code=1013 if unavailable else 4429,
                reason="Plugin server closing"
                if unavailable
                else "Plugin connection limit reached",
            )
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

            lease = getattr(websocket.state, AUTHENTICATED_LEASE_STATE, None)
            if not isinstance(lease, CredentialLease):
                keys = websocket.headers.getlist(API_KEY_HEADER)
                if len(keys) != 1 or not keys[0]:
                    await websocket.close(code=4401, reason="API key required")
                    return
                service = ApiKeyService.get_instance()
                source_id = websocket.client.host if websocket.client is not None else "unknown"
                result = await service.validate(keys[0], source_id=source_id)
                del keys
                if not result.valid or result.lease is None:
                    await websocket.close(
                        code=1013 if result.overloaded or not result.cacheable else 4403,
                        reason="Try again later"
                        if result.overloaded or not result.cacheable
                        else "Invalid API key",
                    )
                    return
                lease = result.lease
            setattr(websocket.state, AUTHENTICATED_LEASE_STATE, lease)
            websocket.state.user_id = lease.user_id
            lifetime = getattr(self, "_credential_lifetime", None)
            if lifetime is not None:
                # Acquire at the authenticated boundary, before any suspended check
                # or welcome I/O. Dispatch releases only after captured cleanup exits.
                await lifetime.enter_async_context(lease.owner_lifetime())
            await lease.check()

        cls = type(self)
        user_id = getattr(websocket.state, "user_id", None)
        lock = cls._lock
        registry = cls._registry
        if lock is None or registry is None:
            await websocket.close(code=1013, reason="Plugin server unavailable")
            return
        async with lock:
            # The immutable principal of this live credential owns
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
        lease = cls._socket_lease(websocket)
        async with lease.own() if lease is not None else nullcontext():
            await websocket.accept()
            msg = WelcomeMessage(
                serverTimeout=self.SERVER_TIMEOUT,
                keepAliveInterval=self.KEEP_ALIVE_INTERVAL,
                capabilities=sorted(self.CAPABILITIES),
            )
            await websocket.send_json(msg.model_dump())

    async def on_receive(self, websocket: WebSocket, data: Any) -> None:
        lease = type(self)._socket_lease(websocket)
        if lease is not None:
            await lease.check()
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
                await self._handle_register_tools_frame(websocket, data)
            elif message_type == "pong":
                await self._handle_pong(websocket, PongMessage(**data))
            elif message_type == "command_progress":
                await self._handle_command_progress(websocket, data)
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
        catalog_changes = []
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
                    catalog_changes.append(await cls._unregister_catalog(cls._registry, session_id))
                logger.info(f"Plugin session {session_id} disconnected ({close_code})")

        for outcome, scope in catalog_changes:
            await cls._notify_committed_catalog(outcome, scope)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    @classmethod
    async def capture_local_project_context(
        cls, context: LocalProjectContext, target_hash: str, root: str
    ) -> tuple[str, SessionDetails] | None:
        """Capture one local registration while owning the same lock as replacement/admission."""
        lock = cls._lock
        if lock is None:
            context.refuse("project_context_unavailable")
            return None
        async with lock:
            registry = cls._registry
            session_id = await registry.get_session_id_by_hash(target_hash) if registry else None
            session = await registry.get_session(session_id) if registry and session_id else None
            websocket = cls._connections.get(session_id)
            if session is None or websocket is None:
                context.refuse("project_context_target_unavailable")
                return None
            actual_root = normalized_root(session.project_path)
            if actual_root is None:
                context.refuse("project_context_unavailable")
                return None
            if actual_root != root:
                context.refuse("project_context_mismatch")
                return None
            generation = getattr(websocket.state, "plugin_generation", None)
            if not isinstance(generation, str) or not generation:
                context.refuse("project_context_unavailable")
                return None
            context.selection = ProjectSelection(
                registry, session_id, websocket, generation, root, target_hash, session.user_id
            )
            details = SessionDetails(
                project=session.project_name,
                hash=session.project_hash,
                unity_version=session.unity_version,
                connected_at=session.connected_at.isoformat(),
            )
            return session_id, details

    @classmethod
    def _diagnostic_owner_locked(cls, registry, websocket):
        """Caller holds Hub then registry locks; payload identity is never trusted."""
        sid = getattr(websocket.state, "plugin_session_id", None)
        session = registry._sessions.get(sid)
        if session is None:
            return None
        current = (
            registry._user_hash_to_session.get((session.user_id, session.project_hash))
            if session.user_id
            else registry._hash_to_session.get(session.project_hash)
        )
        if (
            cls._registry is not registry
            or cls._connections.get(sid) is not websocket
            or current != sid
            or getattr(websocket.state, "user_id", None) != session.user_id
            or not getattr(websocket.state, "plugin_registered", False)
            or websocket.client_state != WebSocketState.CONNECTED
            or websocket.application_state != WebSocketState.CONNECTED
        ):
            return None
        return session

    @classmethod
    async def _record_tool_diagnostic_failure(
        cls,
        websocket,
        reason,
        names=(),
        since_sequence=None,
        expected_revision=None,
        offending=None,
    ):
        from services.custom_tool_service import CustomToolService

        lock, registry, service = cls._lock, cls._registry, CustomToolService._instance
        if lock is None or registry is None or service is None or service._mcp is not cls._mcp:
            return
        async with lock:
            async with registry._lock:
                session = cls._diagnostic_owner_locked(registry, websocket)
                if session is None or (
                    expected_revision is not None and session.tools_revision != expected_revision
                ):
                    return
                if since_sequence is not None and service.diagnostic_attempt_is_newer(
                    "http",
                    session.project_hash,
                    session.user_id,
                    session.session_id,
                    since_sequence,
                ):
                    return
                service.record_diagnostic_attempt(
                    "http",
                    session.project_hash,
                    session.session_id,
                    session.user_id,
                    names=names,
                    offending=offending,
                    reason=reason,
                    retained=session.tools_known,
                    revision=session.tools_revision,
                )

    @classmethod
    async def get_tool_diagnostic_attempts(cls, instance, user_id=None, name=None):
        """Read only records belonging to the exact current authenticated socket."""
        from services.custom_tool_service import CustomToolService
        from services.tool_diagnostics import _named

        lock, registry, service = cls._lock, cls._registry, CustomToolService._instance
        if lock is None or registry is None or service is None or service._mcp is not cls._mcp:
            return []
        if config.http_remote_hosted and not user_id:
            return []
        try:
            sid = await cls._resolve_session_id(instance, user_id=user_id, retry_on_reload=False)
        except (InstanceSelectionRequiredError, NoUnitySessionError):
            return []
        async with lock:
            async with registry._lock:
                websocket = cls._connections.get(sid)
                session = cls._diagnostic_owner_locked(registry, websocket) if websocket else None
                if session is None or session.user_id != user_id:
                    return []
                result = [
                    _named(
                        service.get_diagnostic_attempts(
                            "http", session.project_hash, user_id, session.session_id
                        ),
                        name,
                    )
                ]
                if not config.http_remote_hosted:
                    alias = (
                        service.get_project_id_for_hash(session.project_hash)
                        or session.project_hash
                    )
                    result.append(_named(service.get_diagnostic_attempts("manual", alias), name))
                return [value for value in result if value is not None]

    @classmethod
    async def get_capability_snapshot(
        cls, unity_instance: str | None, user_id: str | None = None
    ) -> CapabilityConnection:
        """Observe one live tenant-owned generation without dispatching a command."""
        known = cls.CAPABILITIES.intersection(
            {"editor_state_v1", "large_result_v1", "large_result_gzip_v1", "command_cancel_v1"}
        )
        base = {"transport": "http", "server_advertised": sorted(known)}
        if config.http_remote_hosted and not user_id:
            return CapabilityConnection(**base, status="auth_required", next_action="authenticate")
        lock, registry = cls._lock, cls._registry
        if lock is None or registry is None:
            return CapabilityConnection(**base, status="unavailable", next_action="connect_unity")
        try:
            session_id = await cls._resolve_session_id(
                unity_instance, user_id=user_id, retry_on_reload=False
            )
        except InstanceSelectionRequiredError:
            return CapabilityConnection(
                **base, status="selection_required", next_action="select_instance"
            )
        except NoUnitySessionError:
            return CapabilityConnection(**base, status="unavailable", next_action="connect_unity")
        async with lock:
            session = await registry.get_session(session_id)
            websocket = cls._connections.get(session_id)
            if session is not None:
                current_session = await registry.get_session_id_by_hash(
                    session.project_hash, user_id=user_id
                )
            else:
                current_session = None
            if (
                cls._lock is not lock
                or cls._registry is not registry
                or session is None
                or session.user_id != user_id
                or current_session != session_id
                or websocket is None
                or websocket.client_state != WebSocketState.CONNECTED
                or websocket.application_state != WebSocketState.CONNECTED
                or not getattr(websocket.state, "plugin_registered", False)
                or getattr(websocket.state, "plugin_session_id", None) != session_id
                or getattr(websocket.state, "user_id", None) != user_id
            ):
                return CapabilityConnection(**base, status="unavailable", next_action="retry_later")
            generation = getattr(websocket.state, "plugin_generation", None)
            if not isinstance(generation, str) or not generation or len(generation) > 128:
                return CapabilityConnection(**base, status="unknown", next_action="reconnect_unity")
            features = getattr(websocket.state, "plugin_features", None)
            negotiated = (
                sorted(known.intersection(features))
                if isinstance(features, (set, frozenset))
                else None
            )
            user_count = session_count = total_bytes = user_bytes = session_bytes = 0
            for entry in cls._pending.values():
                size = entry.get("payload_bytes", 0)
                total_bytes += size
                if entry.get("user_id") == user_id:
                    user_count += 1
                    user_bytes += size
                if entry.get("session_id") == session_id:
                    session_count += 1
                    session_bytes += size
            available = (
                len(cls._pending) < cls.MAX_PENDING_COMMANDS
                and user_count < cls.MAX_PENDING_PER_USER
                and session_count < cls.MAX_PENDING_PER_SESSION
                and total_bytes < cls.MAX_PENDING_PAYLOAD_BYTES
                and user_bytes < cls.MAX_PENDING_PAYLOAD_BYTES_PER_USER
                and session_bytes < cls.MAX_PENDING_PAYLOAD_BYTES_PER_SESSION
                and cls._has_result_capacity({"user_id": user_id, "session_id": session_id}, 1)
            )
            return CapabilityConnection(
                **base,
                status="connected",
                instance_id=f"{session.project_name}@{session.project_hash}",
                project_hash=session.project_hash,
                generation=sha256(
                    f"{id(websocket)}:{session_id}:{generation}".encode()
                ).hexdigest(),
                negotiated=negotiated,
                admission_available=available,
                pending_commands=session_count,
                tenant_pending_commands=user_count,
                reason=None if available else "command_capacity",
                next_action="none" if available else "retry_later",
            )

    @classmethod
    async def get_cached_editor_state(
        cls,
        unity_instance: str | None,
        user_id: str | None = None,
    ) -> dict[str, Any] | None:
        """Read only a fresh snapshot owned by the currently routed connection."""
        if cls._registry is None or cls._lock is None:
            return None
        requester = await require_credential_lease(user_id) if config.http_remote_hosted else None
        try:
            session_id = await cls._resolve_session_id(
                unity_instance, user_id=user_id, retry_on_reload=False
            )
        except (NoUnitySessionError, InstanceSelectionRequiredError):
            return None
        lock = cls._lock
        if lock is None:
            return None
        try:
            websocket = await cls._get_connection(session_id)
        except RuntimeError:
            return None
        async with cls._authorized_socket_lock(websocket, lock, requester):
            if cls._connections.get(session_id) is not websocket:
                return None
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
    def _command_timeouts(cls, command_type: str, params: dict[str, Any]) -> tuple[float, float]:
        # Compute a per-command timeout:
        # - fast-path commands: short total execution budget
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
            requested_s = requested_command_timeout(params)
            if requested_s is not None:
                unity_timeout_s = max(unity_timeout_s, requested_s)
                server_wait_s = max(server_wait_s, requested_s + SERVER_RESPONSE_GRACE)

        return unity_timeout_s, server_wait_s

    @classmethod
    async def send_command(
        cls,
        session_id: str,
        command_type: str,
        params: dict[str, Any],
        *,
        _budget: _CommandBudget | None = None,
        _expected_read: _ConnectionReadIdentity | None = None,
        _expected_generation: str | None = None,
        _catalog_guard=None,
        _progress: ProgressSink | None = None,
    ) -> dict[str, Any]:
        phase_diagnostics.step(command_type, "admission")
        result = await cls._send_command_response(
            session_id,
            command_type,
            params,
            _budget=_budget,
            _expected_read=_expected_read,
            **(
                {"_expected_generation": _expected_generation}
                if _expected_generation is not None
                else {}
            ),
            _catalog_guard=_catalog_guard,
            **({"_progress": _progress} if _progress is not None else {}),
        )
        phase_diagnostics.step(command_type, "return")
        observe_native_result(result)
        return result

    @classmethod
    async def _send_command_response(
        cls,
        session_id: str,
        command_type: str,
        params: dict[str, Any],
        *,
        _budget: _CommandBudget | None = None,
        _expected_read: _ConnectionReadIdentity | None = None,
        _expected_generation: str | None = None,
        _catalog_guard=None,
        _progress: ProgressSink | None = None,
    ) -> dict[str, Any]:
        budget = _budget or _CommandBudget(
            asyncio.get_running_loop().time() + cls._command_timeouts(command_type, params)[1]
        )
        receipt = execution_owner.get()
        if receipt is not None:
            budget.expires_at = min(budget.expires_at, receipt.expires_at)
        budget.phase = "admission"
        budget.dispatched = False
        budget.command_id = None
        budget.session_id = session_id
        lease = await require_credential_lease() if config.http_remote_hosted else None
        try:
            async with lease.own() if lease is not None else nullcontext():
                async with asyncio.timeout_at(budget.expires_at):
                    return await cls._send_command(
                        session_id,
                        command_type,
                        params,
                        budget,
                        _expected_read=_expected_read,
                        **(
                            {"_expected_generation": _expected_generation}
                            if _expected_generation is not None
                            else {}
                        ),
                        _catalog_guard=_catalog_guard,
                        **({"_progress": _progress} if _progress is not None else {}),
                    )
        except OperationPermissionError:
            if budget.dispatched:
                return command_outcome_unknown(
                    command_type,
                    "Remote operation permission ended",
                    session_id=session_id,
                    command_id=budget.command_id,
                    phase=budget.phase,
                    cause="permission_ended",
                )
            return MCPResponse(success=False, error="permission_denied").model_dump()
        except CredentialLeaseError:
            if budget.dispatched:
                return command_outcome_unknown(
                    command_type,
                    "Remote authorization ended",
                    session_id=session_id,
                    command_id=budget.command_id,
                    phase=budget.phase,
                    cause="authentication_ended",
                )
            return MCPResponse(success=False, error="auth_required").model_dump()
        except TimeoutError:
            return budget.timeout_response(command_type)

    @classmethod
    async def _send_command(
        cls,
        session_id: str,
        command_type: str,
        params: dict[str, Any],
        budget: _CommandBudget,
        *,
        _expected_read: _ConnectionReadIdentity | None = None,
        _expected_generation: str | None = None,
        _catalog_guard=None,
        _progress: ProgressSink | None = None,
    ) -> dict[str, Any]:
        requester = await require_credential_lease() if config.http_remote_hosted else None
        context = local_project_context.get()
        if context is not None and (
            context.selection is None or context.selection.session_id != session_id
        ):
            return context.refuse("project_context_changed", params)
        try:
            websocket = await cls._get_connection(session_id)
        except RuntimeError as exc:
            if context is not None:
                return context.refuse("project_context_changed", params)
            if _expected_read is not None:
                return cls._unavailable_retry_response("read_invalidated")
            raise CommandNotDispatchedError(str(exc)) from exc
        registry = cls._registry
        session = await registry.get_session(session_id) if registry is not None else None
        # Admission follows the registered principal, never caller parameters.
        user_id = session.user_id if session is not None else None
        if requester is not None and requester.user_id != user_id:
            raise CredentialLeaseError()
        unity_timeout_s, _ = cls._command_timeouts(command_type, params)
        lock = cls._lock
        if lock is None:
            if context is not None:
                return context.refuse("project_context_unavailable", params)
            raise CommandNotDispatchedError("PluginHub not configured")

        async with cls._authorized_socket_lock(websocket, lock, requester):
            if context is not None and (
                context.selection is None
                or cls._connections.get(session_id) is not websocket
                or not context.selection.matches(registry, session_id, websocket, session)
            ):
                return context.refuse("project_context_changed", params)
            if _catalog_guard is not None and not _catalog_guard.check_http(session_id, user_id):
                from services.custom_tool_service import custom_catalog_changed

                return custom_catalog_changed().model_dump()
            # Disconnect can run between the initial lookup and this lock.
            if cls._connections.get(session_id) is not websocket:
                if _expected_read is not None:
                    return cls._unavailable_retry_response("read_invalidated")
                raise CommandNotDispatchedError(f"Plugin session {session_id} not connected")
            if (
                _expected_generation is not None
                and getattr(websocket.state, "plugin_generation", None) != _expected_generation
            ):
                return cls._unavailable_retry_response("observation_generation_changed")
            if _expected_read is not None and not cls._matches_read_identity(
                websocket, user_id, _expected_read
            ):
                return cls._unavailable_retry_response("read_invalidated")
            for authority in (requester, cls._socket_lease(websocket)):
                if authority is not None:
                    authorize_operation(
                        authority.permissions,
                        command_type,
                        params,
                        custom=_catalog_guard is not None,
                        native=True,
                    )
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
                "receipt_owner": execution_owner.get(),
                # Keep only the admitted scalar deadline, not its request/dispatch graph.
                "result_deadline": budget.expires_at,
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
            # Admission, serialization and all earlier phases spend this same budget.
            # Omit only the optional envelope field; preserve nested null values.
            payload = msg.model_dump(exclude={"progress"})
            diagnostic = phase_diagnostics.request(
                command_type, cls._supports(websocket, phase_diagnostics.CAPABILITY)
            )
            if diagnostic is not None:
                payload["diagnostics"] = diagnostic
            progress = (
                _progress
                if _progress is not None
                and command_type == "batch_execute"
                and isinstance(generation, str)
                and cls._supports(websocket, PROGRESS_CAPABILITY)
                else None
            )
            if progress is not None:
                entry = cls._pending[command_id]
                plugin = cls._socket_lease(websocket)
                floor = (
                    required_permissions(command_type, payload["params"], native=True)
                    if any(
                        lease is not None and lease.permissions.scoped
                        for lease in (requester, plugin)
                    )
                    else frozenset()
                )

                async def progress_authorized() -> bool:
                    # This task may predate the request ContextVars. Use only the
                    # exact admitted handles, canonical floor, deadline and owner.
                    for lease in (requester, plugin):
                        if lease is not None:
                            await lease.check()
                    async with lock:
                        current = (
                            await registry.get_session(session_id) if registry is not None else None
                        )
                        if (
                            cls._lock is not lock
                            or cls._registry is not registry
                            or cls._pending.get(command_id) is not entry
                            or future.done()
                            or cls._connections.get(session_id) is not websocket
                            or websocket.client_state != WebSocketState.CONNECTED
                            or websocket.application_state != WebSocketState.CONNECTED
                            or not getattr(websocket.state, "plugin_registered", False)
                            or getattr(websocket.state, "plugin_session_id", None) != session_id
                            or getattr(websocket.state, "plugin_generation", None) != generation
                            or getattr(websocket.state, "user_id", None) != user_id
                            or cls._socket_lease(websocket) is not plugin
                            or budget.expires_at <= asyncio.get_running_loop().time()
                        ):
                            return False
                        if current is None or current.user_id != user_id:
                            return False
                        if context is not None and (
                            context.selection is None
                            or not context.selection.matches(
                                registry, session_id, websocket, current
                            )
                        ):
                            return False
                        if _catalog_guard is not None and not _catalog_guard.check_http(
                            session_id, user_id
                        ):
                            return False
                        for lease in (requester, plugin):
                            if lease is not None:
                                lease.check_now()
                                if (
                                    lease.permissions.scoped
                                    and not floor <= lease.permissions.permissions
                                ):
                                    return False
                        return True

                bound = ProgressSource(
                    transport="http",
                    command_id=command_id,
                    session_id=session_id,
                    generation=generation,
                    user_id=user_id,
                    expires_at=budget.expires_at,
                    guard=progress_authorized,
                )
                if progress.bind(bound):
                    entry["progress_owner"] = progress
                    entry["progress_generation"] = generation
                    entry["progress_frames"] = 0
                    payload["progress"] = CommandProgressRequest().model_dump()
                else:
                    progress.close()
            if budget.remaining() <= 0:
                raise TimeoutError
            deadline = budget.expires_at
            phase_diagnostics.step(command_type, "send")
            phase = budget.phase = "send"
            budget.command_id = command_id
            try:

                async def authorized_send():
                    plugin = cls._socket_lease(websocket)
                    for lease in (requester, plugin):
                        if lease is not None:
                            await lease.check()
                    for lease in (requester, plugin):
                        if lease is not None:
                            lease.check_now()
                            # Recheck the serialized canonical operation immediately
                            # before socket I/O, after all yielding authorization checks.
                            authorize_operation(
                                lease.permissions,
                                payload["name"],
                                payload["params"],
                                custom=_catalog_guard is not None,
                                native=True,
                            )
                    receipt = execution_owner.get()
                    if receipt is not None:
                        await receipt.native_async(
                            payload["name"],
                            payload["params"],
                            custom=_catalog_guard is not None,
                            kind="http",
                            attempt_id=command_id,
                            session_id=session_id,
                            generation=generation,
                            project_hash=session.project_hash if session is not None else None,
                        )
                        # Durable journal I/O yields. Repeat the original exact
                        # target/catalog/lease fences before admitting socket I/O.
                        current_session = (
                            await registry.get_session(session_id) if registry is not None else None
                        )
                        async with cls._authorized_socket_lock(websocket, lock, requester):
                            if (
                                cls._connections.get(session_id) is not websocket
                                or getattr(websocket.state, "plugin_generation", None) != generation
                                or current_session is None
                                or current_session.project_hash != session.project_hash
                            ):
                                raise PluginDisconnectedError("Receipt target generation changed")
                            if context is not None and (
                                context.selection is None
                                or not context.selection.matches(
                                    registry, session_id, websocket, current_session
                                )
                            ):
                                raise CommandNotDispatchedError("Receipt project context changed")
                            if _expected_read is not None and not cls._matches_read_identity(
                                websocket, user_id, _expected_read
                            ):
                                raise CommandNotDispatchedError("Receipt read identity changed")
                            if _catalog_guard is not None and not _catalog_guard.check_http(
                                session_id, user_id
                            ):
                                raise CommandNotDispatchedError("Receipt custom catalog changed")
                            for lease in (requester, cls._socket_lease(websocket)):
                                if lease is not None:
                                    lease.check_now()
                                    authorize_operation(
                                        lease.permissions,
                                        payload["name"],
                                        payload["params"],
                                        custom=_catalog_guard is not None,
                                        native=True,
                                    )
                    if receipt is not None:
                        receipt.dispatch_started(
                            kind="http",
                            attempt_id=command_id,
                            session_id=session_id,
                            generation=generation,
                        )
                    budget.dispatched = True
                    await websocket.send_json(payload)
                    for lease in (requester, plugin):
                        if lease is not None:
                            await lease.check()

                receipt = execution_owner.get()
                receipt_pin = receipt.acquire_worker() if receipt is not None else None
                try:
                    send_task = asyncio.create_task(authorized_send())
                except BaseException:
                    if receipt_pin is not None:
                        receipt_pin.close()
                    raise
                if receipt_pin is not None:
                    own_future(send_task, receipt_pin)
                done, _ = await asyncio.wait(
                    {send_task, future},
                    timeout=budget.remaining(),
                    return_when=asyncio.FIRST_COMPLETED,
                )
                # A disconnect must also interrupt a blocked socket write.
                if future in done:
                    for lease in (requester, cls._socket_lease(websocket)):
                        if lease is not None:
                            await lease.check()
                    return future.result()
                if send_task not in done:
                    raise asyncio.TimeoutError
                send_task.result()
                phase_diagnostics.step(command_type, "response")
                phase = budget.phase = "response"
                remaining = max(0.0, deadline - asyncio.get_running_loop().time())
                result = await asyncio.wait_for(future, timeout=remaining)
                for lease in (requester, cls._socket_lease(websocket)):
                    if lease is not None:
                        await lease.check()
                return result
            except OperationPermissionError:
                # Admission may differ after canonical serialization. A refusal
                # before dispatch must not manufacture an unknown outcome or
                # send a native cancellation for work that never started.
                canceled = budget.dispatched
                if not budget.dispatched:
                    return MCPResponse(success=False, error="permission_denied").model_dump()
                return command_outcome_unknown(
                    command_type,
                    "Remote operation permission ended",
                    session_id=session_id,
                    command_id=command_id,
                    phase=phase,
                    cause="permission_ended",
                )
            except CredentialLeaseError:
                canceled = True
                if not budget.dispatched:
                    return MCPResponse(success=False, error="auth_required").model_dump()
                return command_outcome_unknown(
                    command_type,
                    "Remote authorization ended",
                    session_id=session_id,
                    command_id=command_id,
                    phase=phase,
                    cause="authentication_ended",
                )
            except asyncio.CancelledError:
                # Preserve cancellation of the caller. Cancellation of just the
                # socket writer is instead an uncertain transport outcome.
                if asyncio.current_task().cancelling():
                    raise
                canceled = True
                return command_outcome_unknown(
                    command_type,
                    "socket write was cancelled",
                    session_id=session_id,
                    command_id=command_id,
                    phase=phase,
                    cause="send_cancelled",
                )
            except Exception as exc:
                canceled = True
                cause = (
                    "timeout"
                    if isinstance(exc, TimeoutError)
                    else "disconnected"
                    if isinstance(exc, PluginDisconnectedError)
                    else "transport_error"
                )
                return command_outcome_unknown(
                    command_type,
                    str(exc) or type(exc).__name__,
                    session_id=session_id,
                    command_id=command_id,
                    phase=phase,
                    cause=cause,
                )
        except asyncio.CancelledError:
            canceled = True
            raise
        finally:
            # Progress ends at response/cancellation, never actual worker cleanup.
            if _progress is not None:
                _progress.close()
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
                # Resource cleanup may use at most CANCEL_TIMEOUT beyond the
                # execution deadline; it never grants more execution time.
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

        async with cls._authorized_socket_lock(websocket, lock):
            if cls._shutdown_requested or cls._registry is not registry:
                await websocket.close(code=1013, reason="Plugin server closing")
                return
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
        catalog_outcome = None
        catalog_scope = None
        async with cls._authorized_socket_lock(websocket, lock):
            # The registry and routing insertion form one transaction. There is
            # no cancellation point after register mutates its maps and before
            # this socket becomes discoverable by disconnect cleanup.
            if cls._shutdown_requested or cls._registry is not registry:
                await websocket.close(code=1013, reason="Plugin server unavailable")
                return
            try:

                def publish_registration(new_session, prospective):
                    lease = cls._socket_lease(websocket)
                    if lease is not None:
                        lease.check_now()
                    nonlocal catalog_outcome, catalog_scope
                    catalog_outcome = cls._publish_session_catalog(
                        new_session,
                        {},
                        prospective,
                        known=False,
                        generation=session_id,
                    )
                    catalog_scope = dict(
                        user_id=new_session.user_id,
                        project_hash=new_session.project_hash,
                        session_id=session_id,
                        catalog_revision=0,
                    )

                session, evicted_session_id = await registry.register(
                    session_id,
                    project_name,
                    project_hash,
                    unity_version,
                    project_path,
                    user_id=user_id,
                    _publish=publish_registration,
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
            websocket.state.plugin_editor_state_publication = _EditorStatePublication(
                session.user_id,
                session.project_hash,
                session.session_id,
                websocket.state.plugin_generation,
            )
            websocket.state.plugin_command_epoch = 0
            websocket.state.plugin_state_read_epoch = 0
            accepted = cls.CAPABILITIES.intersection(payload.capabilities)
            if LARGE_RESULT_CAPABILITY not in accepted:
                accepted = accepted - {COMPRESSION_CAPABILITY}
            if "editor_state_v1" in accepted:
                if not cls._editor_states.register_session(session_id, user_id):
                    accepted = accepted - {"editor_state_v1"}
            websocket.state.plugin_features = frozenset(accepted)
            cls._publish_editor_state(websocket, 0)
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

        await cls._notify_committed_catalog(catalog_outcome, catalog_scope)

        if user_id:
            logger.info(
                "Plugin registered: %r (%r) for user %r", project_name, project_hash, user_id
            )
        else:
            logger.info("Plugin registered: %r (%r)", project_name, project_hash)

    async def _handle_register_tools_frame(self, websocket: WebSocket, data: dict) -> None:
        correlation = None
        if TOOL_SETTINGS_CAPABILITY in getattr(websocket.state, "plugin_features", ()):
            try:
                correlation = ToolSettingsCorrelation.model_validate(data)
            except ValueError:
                await self._record_tool_diagnostic_failure(websocket, "metadata_invalid")
                await websocket.close(code=4400, reason="Invalid tool settings correlation")
                return
        try:
            payload = RegisterToolsMessage.model_validate(data)
        except ValueError:
            await self._record_tool_diagnostic_failure(websocket, "metadata_invalid")
            if correlation is None:
                raise
            await self._send_tool_settings_result(
                websocket, correlation, "rejected", reason="invalid_catalog"
            )
            return
        await self._handle_register_tools(websocket, payload)

    async def _send_tool_settings_result(
        self, websocket, correlation, status, *, committed=None, reason=None
    ) -> None:
        cls = type(self)
        if cls._lock is None or cls._registry is None:
            return
        async with cls._lock:
            session_id = getattr(websocket.state, "plugin_session_id", None)
            if cls._connections.get(session_id) is not websocket:
                return
            session = await cls._registry.get_session(session_id)
            if session is None:
                return
            revision, count = committed or (session.tools_revision, len(session.tools))
            result = ToolSettingsResult(
                **correlation.model_dump(),
                session_id=session_id,
                status=status,
                catalog_revision=revision,
                enabled_tool_count=count,
                reason=reason,
            )
        try:
            await websocket.send_json(result.model_dump(exclude_none=True))
        except Exception as error:
            # Publication already committed, or this is a refusal. Neither is undone by a lost receipt.
            logger.warning("Tool settings receipt send failed (%s)", type(error).__name__)

    async def _handle_register_tools(
        self, websocket: WebSocket, payload: RegisterToolsMessage
    ) -> None:
        cls = type(self)
        registry = cls._registry
        lock = cls._lock
        if registry is None or lock is None:
            return

        correlation = None
        if TOOL_SETTINGS_CAPABILITY in getattr(websocket.state, "plugin_features", ()):
            correlation = ToolSettingsCorrelation.model_validate(payload.model_dump())
        excluded_receipt_fields = (
            {"settings_epoch", "desired_revision"} if correlation is None else None
        )
        if (
            len(payload.model_dump_json(exclude=excluded_receipt_fields).encode("utf-8"))
            > 512 * 1024
        ):
            await self._record_tool_diagnostic_failure(websocket, "diagnostic_limit")
            if correlation is not None:
                await self._send_tool_settings_result(
                    websocket, correlation, "rejected", reason="invalid_catalog"
                )
                return
            await websocket.close(code=4400, reason="Tool registration exceeds size limit")
            return

        from services.custom_tool_service import CustomToolService

        diagnostic_service = CustomToolService._instance
        before_attempt = diagnostic_service._diagnostic_sequence if diagnostic_service else 0
        outcome = None
        scope = None
        committed = None
        try:
            async with cls._authorized_socket_lock(websocket, lock):
                session_id = next(
                    (sid for sid, ws in cls._connections.items() if ws is websocket), None
                )
                if not session_id:
                    logger.warning("Received register_tools from unknown connection")
                    return

                def publish(session, prepared):
                    lease = cls._socket_lease(websocket)
                    if lease is not None:
                        lease.check_now()
                    nonlocal outcome, scope, committed
                    outcome = cls._publish_session_catalog(
                        session,
                        prepared,
                        registry._sessions,
                        generation=session.session_id,
                    )
                    revision = session.tools_revision + int(
                        not session.tools_known or session.tools != prepared
                    )
                    committed = (revision, len(prepared))
                    scope = dict(
                        user_id=session.user_id,
                        project_hash=session.project_hash,
                        session_id=session.session_id,
                        catalog_revision=revision,
                    )

                await registry.register_tools_for_session(
                    session_id,
                    payload.tools,
                    _publish=publish,
                    **(
                        {"_settings": (correlation.settings_epoch, correlation.desired_revision)}
                        if correlation is not None
                        else {}
                    ),
                )
        except Exception as error:
            names = [tool.name for tool in payload.tools[:256]]
            reason_code = (
                "duplicate_name" if len(set(names)) != len(names) else "publication_failed"
            )
            await self._record_tool_diagnostic_failure(
                websocket, reason_code, names, before_attempt
            )
            if correlation is None:
                raise
            reason = str(error) if isinstance(error, ToolSettingsConflict) else "publication_failed"
            await self._send_tool_settings_result(websocket, correlation, "rejected", reason=reason)
            return
        if correlation is not None:
            await self._send_tool_settings_result(
                websocket, correlation, "applied", committed=committed
            )
        try:
            await cls._notify_committed_catalog(outcome, scope)
        except Exception as error:
            logger.warning("Committed catalog notification failed (%s)", type(error).__name__)

    @classmethod
    async def _refresh_server_tool_visibility(cls) -> None:
        """Keep local server inventory large enough for every registered project."""
        registry = cls._registry
        if config.http_remote_hosted or registry is None:
            return
        sessions = await registry.list_sessions()
        cls._commit_server_tool_visibility(
            cls._prepare_server_tool_visibility(
                [tool for session in sessions.values() for tool in session.tools.values()],
                reset=not sessions,
            )
        )

    @classmethod
    def _prepare_server_tool_visibility(cls, registered_tools, *, reset=False):
        """Build a detached local visibility delta, retaining foreign transforms."""
        if config.http_remote_hosted or cls._mcp is None:
            return None
        from fastmcp.server.transforms.visibility import Visibility
        from services.registry import get_group_tool_names, TOOL_GROUPS

        mcp = cls._mcp
        old = mcp._transforms
        owned = getattr(cls, "_unity_transforms", ())
        owned_ids = {id(transform) for transform in owned}
        base = [transform for transform in old if id(transform) not in owned_ids]
        names = {
            tool.get("name") if isinstance(tool, dict) else tool.name for tool in registered_tools
        }
        groups = get_group_tool_names()
        signature = (
            None
            if reset
            else tuple(
                (group, any(name in names for name in groups.get(group, ())))
                for group in sorted(TOOL_GROUPS)
            )
        )
        if signature == getattr(cls, "_unity_visibility_signature", None):
            return None
        transforms = (
            ()
            if reset
            else tuple(
                Visibility(enabled, tags={f"group:{group}"}, components={"tool"})
                for group, enabled in signature
            )
        )
        return {
            "mcp": mcp,
            "old": old,
            "old_contents": tuple(old),
            "new": base + list(transforms),
            "owned": transforms,
            "start": len(base) if not reset else None,
            "signature": signature,
            "previous": (
                cls._unity_transform_start,
                owned,
                getattr(cls, "_unity_visibility_signature", None),
            ),
        }

    @classmethod
    def _commit_server_tool_visibility(cls, plan):
        if plan is None:
            return
        mcp = plan["mcp"]
        if (
            cls._mcp is not mcp
            or mcp._transforms is not plan["old"]
            or tuple(mcp._transforms) != plan["old_contents"]
        ):
            raise ValueError("Tool visibility ownership changed during publication")
        mcp._transforms = plan["new"]
        cls._unity_transform_start = plan["start"]
        cls._unity_transforms = plan["owned"]
        cls._unity_visibility_signature = plan["signature"]

    @classmethod
    def _rollback_server_tool_visibility(cls, plan):
        if plan is not None and plan["mcp"]._transforms is plan["new"]:
            plan["mcp"]._transforms = plan["old"]
            cls._unity_transform_start, cls._unity_transforms, cls._unity_visibility_signature = (
                plan["previous"]
            )

    @classmethod
    def _sync_server_tool_visibility(cls, registered_tools):
        try:
            cls._commit_server_tool_visibility(
                cls._prepare_server_tool_visibility(registered_tools)
            )
        except Exception:
            logger.debug("Failed to sync server-level tool visibility", exc_info=True)

    @classmethod
    def _publish_session_catalog(
        cls, session, tools, sessions, *, known=True, generation=None, withdraw=False
    ):
        """Synchronous callback under Hub -> registry locks; never reacquire either."""
        from services.custom_tool_service import CustomToolService

        advertised = [
            definition
            for sid, entry in sessions.items()
            for definition in (
                tools.values() if sid == session.session_id else entry.tools.values()
            )
        ]
        plan = cls._prepare_server_tool_visibility(advertised, reset=not sessions)
        visibility = (
            None
            if plan is None
            else (
                lambda: cls._commit_server_tool_visibility(plan),
                lambda: cls._rollback_server_tool_visibility(plan),
            )
        )
        service = CustomToolService._instance
        if service is None or cls._mcp is None or service._mcp is not cls._mcp:
            cls._commit_server_tool_visibility(plan)
            return {
                "changed": plan is not None
                or session.tools != tools
                or session.tools_known != known,
                "global_component_changed": False,
            }
        return service.replace_project_catalog(
            session.project_hash,
            list(tools.values()),
            kind="http",
            generation=generation,
            user_id=session.user_id,
            publish_global=not config.http_remote_hosted,
            known=known,
            visibility=visibility,
            withdraw=withdraw,
        )

    @classmethod
    async def _notify_committed_catalog(cls, outcome, scope):
        if not outcome or not outcome.get("changed"):
            return
        if not config.http_remote_hosted and outcome.get("global_component_changed"):
            await cls._notify_mcp_tool_list_changed()
        else:
            await cls._notify_mcp_tool_list_changed(**scope)

    @classmethod
    async def _unregister_catalog(cls, registry, session_id):
        """Caller owns the Hub lock; registry calls publication synchronously."""
        outcome = None
        scope = None

        def publish(session, remaining):
            nonlocal outcome, scope
            outcome = cls._publish_session_catalog(
                session, {}, remaining, known=False, withdraw=True
            )
            scope = dict(
                user_id=session.user_id,
                project_hash=session.project_hash,
                session_id=session.session_id,
                catalog_revision=session.tools_revision + 1,
            )

        await registry.unregister(session_id, _publish=publish)
        return outcome, scope

    @classmethod
    async def _notify_mcp_tool_list_changed(
        cls, *, user_id=None, project_hash=None, session_id=None, catalog_revision=None
    ) -> None:
        """Queue ``tools/list_changed`` for each connected MCP client.

        After server-level tool visibility is updated (e.g. when Unity reports
        its registered tools), existing MCP clients (especially stdio-based
        ones like Claude Code) must be told to re-fetch the tool list.
        FastMCP's ``mcp.enable()``/``mcp.disable()`` update the server-level
        transforms but do **not** push notifications to already-connected
        sessions — we do that here.
        """
        # Legacy connections have no trusted tenant/project routing metadata.
        # Keep this process-global invalidation local. The trusted arguments
        # remain available for the separately owned scoped modern publisher.
        if not config.http_remote_hosted:
            cls._catalog_notifications.publish(_active_mcp_sessions)
        subscriptions = getattr(cls._mcp, "_catalog_subscriptions", None)
        if subscriptions is not None:
            subscriptions.publish(
                user_id=user_id,
                project_hash=project_hash,
                session_id=session_id,
                catalog_revision=catalog_revision,
            )

    @classmethod
    def _publish_editor_state(cls, websocket: WebSocket, revision: int) -> None:
        """The caller holds the Hub lock; queue no snapshot or payload identity."""
        scope = getattr(websocket.state, "plugin_editor_state_publication", None)
        subscriptions = getattr(cls._mcp, "_catalog_subscriptions", None)
        if isinstance(scope, _EditorStatePublication) and subscriptions is not None:
            subscriptions.publish_editor_state(
                user_id=scope.user_id,
                project_hash=scope.project_hash,
                session_id=scope.session_id,
                generation=scope.generation,
                revision=revision,
            )

    async def _handle_editor_state(self, websocket: WebSocket, data: dict[str, Any]) -> None:
        cls = type(self)
        lock = cls._lock
        if lock is None or not cls._supports(websocket, "editor_state_v1"):
            return
        session_id = getattr(websocket.state, "plugin_session_id", None)
        registry = cls._registry
        if registry is None:
            return
        session = await registry.get_session(session_id)
        async with cls._authorized_socket_lock(websocket, lock):
            if cls._connections.get(session_id) is not websocket or registry is None:
                return
            if (
                cls._registry is not registry
                or cls._lock is not lock
                or cls._connections.get(session_id) is not websocket
                or session is None
                or session.user_id != getattr(websocket.state, "user_id", None)
            ):
                return
            lease = cls._socket_lease(websocket)
            if lease is not None:
                lease.check_now()
            before = cls._editor_states.change_token(session_id)
            cls._editor_states.accept(session_id, data)
            after = cls._editor_states.change_token(session_id)
            if after is not None and after != before:
                cls._publish_editor_state(websocket, after)

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
        close_invalid = False
        try:
            async with cls._authorized_socket_lock(websocket, lock):
                cls._expire_large_results()
                if isinstance(data, dict):
                    start = ResultStartMessage(**data)
                    if start.encoding == "gzip" and not cls._supports(
                        websocket, COMPRESSION_CAPABILITY
                    ):
                        raise LargeResultProtocolError("compression_not_negotiated", start.id)
                    entry = cls._pending.get(start.id)
                    deadline = entry.get("result_deadline") if entry is not None else None
                    # Convert clock domains once. The peer cannot extend the command
                    # by advertising a timeout or by continuing to send chunks.
                    remaining = (
                        max(0.0, deadline - asyncio.get_running_loop().time())
                        if deadline is not None
                        else None
                    )
                    assembler.begin(
                        generation,
                        start.id,
                        start.total_bytes,
                        start.chunk_count,
                        encoding=start.encoding,
                        decoded_bytes=start.decoded_bytes,
                        timeout_seconds=remaining,
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
            close_invalid = True
        except (ValueError, RecursionError):
            close_invalid = True
        finally:
            if command_id is not None:
                assembler.discard(generation, command_id)
            if close_invalid:
                completed = text = message = payload = start = data = None
        if close_invalid:
            # Closing can suspend. Release rejected payloads and decoder tracebacks first.
            await websocket.close(code=4400, reason="Invalid large result transfer")

    async def _handle_command_progress(self, websocket: WebSocket, data: dict[str, Any]) -> None:
        """Drop invalid/late optional observations without changing command state."""
        cls = type(self)
        command_id = data.get("id")
        lock = cls._lock
        if type(command_id) is not str or len(command_id) > 36 or lock is None:
            return
        async with lock:
            entry = cls._pending.get(command_id)
            if (
                entry is None
                or entry.get("progress_owner") is None
                or entry["future"].done()
                or cls._connections.get(entry["session_id"]) is not websocket
                or not getattr(websocket.state, "plugin_registered", False)
                or getattr(websocket.state, "plugin_session_id", None) != entry["session_id"]
                or getattr(websocket.state, "plugin_generation", None)
                != entry["progress_generation"]
                or getattr(websocket.state, "user_id", None) != entry["user_id"]
                or not cls._supports(websocket, PROGRESS_CAPABILITY)
            ):
                return
            frames = entry["progress_frames"]
            if frames >= MAX_PROGRESS_FRAMES:
                return
            entry["progress_frames"] = frames + 1
            progress = entry["progress_owner"]
        if (
            response_size(
                # response_size charges decoded objects/copies, not only wire
                # bytes. Keep a fixed working bound large enough for this 512-byte graph.
                data,
                max_bytes=MAX_PROGRESS_BYTES,
                max_depth=2,
                max_nodes=16,
                max_retained=16 * 1024,
            )
            is None
        ):
            return
        try:
            message = CommandProgressMessage.model_validate(data)
        except ValidationError:
            return
        # A single sender rechecks the captured fresh authority before every SDK
        # notification. No SDK call, journal I/O or Future settlement under Hub lock.
        progress.offer(CommandProgressUpdate(message.completed, message.total, message.stage))

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

        # Capture only an already-owned receipt under the existing socket fence.
        # Its journal I/O runs outside the Hub lock and outside the owner loop.
        candidate = cls._pending.get(command_id)
        if candidate is not None and candidate.get("progress_owner") is not None:
            async with cls._authorized_socket_lock(websocket, lock):
                if (
                    cls._pending.get(command_id) is candidate
                    and cls._connections.get(candidate["session_id"]) is websocket
                ):
                    candidate["progress_owner"].close()
        receipt = candidate.get("receipt_owner") if candidate is not None else None
        if receipt is not None:
            async with cls._authorized_socket_lock(websocket, lock):
                if (
                    cls._pending.get(command_id) is not candidate
                    or cls._connections.get(candidate["session_id"]) is not websocket
                ):
                    return
                receipt_session = candidate["session_id"]
                receipt_generation = getattr(websocket.state, "plugin_generation", None)
            await receipt.observe_async(
                kind="http",
                attempt_id=command_id,
                session_id=receipt_session,
                generation=receipt_generation,
                dispatched=True,
                observed=True,
                cause="observed",
            )
        close_offender = False
        async with cls._authorized_socket_lock(websocket, lock):
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
                            copy_charge = copy_owner.requested_copy_bytes
                            copy_charge = (
                                reservation["bytes"] if copy_charge is None else copy_charge
                            )
                            # Synchronous on the same owner loop: admission and
                            # insertion cannot interleave with another producer.
                            if (
                                ledger is not cls._retained_results
                                or ledger.get(source_id) is not reservation
                                or not 0 <= copy_charge <= reservation["bytes"]
                                or not cls._has_result_capacity(reservation, copy_charge)
                            ):
                                return False
                            copy_id = "copy:" + str(uuid.uuid4())
                            ledger[copy_id] = {**reservation, "bytes": copy_charge}
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
            async with cls._authorized_socket_lock(websocket, lock):
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
    async def _evict_connection(
        cls,
        session_id: str,
        reason: str,
        *,
        detach_on_cancel: bool = False,
        _expected: tuple[PluginRegistry | None, WebSocket | None] | None = None,
    ) -> None:
        if detach_on_cancel:
            key = (id(cls._registry), session_id)
            task = cls._eviction_tasks.get(key)
            if task is None:
                task = asyncio.create_task(
                    cls._evict_connection(
                        session_id,
                        reason,
                        _expected=(cls._registry, cls._connections.get(session_id)),
                    )
                )
                cls._eviction_tasks[key] = task

                def finished(completed: asyncio.Task) -> None:
                    if cls._eviction_tasks.get(key) is completed:
                        cls._eviction_tasks.pop(key, None)
                    if not completed.cancelled() and completed.exception() is not None:
                        logger.warning(
                            "Owned plugin eviction failed", exc_info=completed.exception()
                        )

                task.add_done_callback(finished)
            # The server, rather than an expired command, owns the remaining
            # registry/socket cleanup. Shielding never extends the caller's budget.
            await asyncio.shield(task)
            return
        # Once routing disappears, only this task can finish registry/socket cleanup.
        # Keep ownership through explicit Task.cancel() and AnyIO level cancellation.
        cleanup = asyncio.create_task(
            cls._evict_connection_owned(session_id, asyncio.current_task())
            if _expected is None
            else cls._evict_connection_owned(session_id, asyncio.current_task(), expected=_expected)
        )
        cancellation: asyncio.CancelledError | None = None
        with anyio.CancelScope(shield=True):
            while not cleanup.done():
                try:
                    await asyncio.wait({cleanup})
                except asyncio.CancelledError as error:
                    if cancellation is None:
                        cancellation = error
            cleanup_error = None if cleanup.cancelled() else cleanup.exception()
        try:
            await anyio.lowlevel.checkpoint_if_cancelled()
        except asyncio.CancelledError as error:
            if cancellation is None:
                cancellation = error
        if cancellation is not None:
            raise cancellation from cleanup_error
        cleanup.result()
        logger.debug("Evicted plugin session %s (%s)", session_id, reason)

    @classmethod
    async def _evict_connection_owned(
        cls,
        session_id: str,
        initiating_task: asyncio.Task | None,
        *,
        expected: tuple[PluginRegistry | None, WebSocket | None] | None = None,
    ) -> bool:
        """Drop a stale session from in-memory maps and registry."""
        lock = cls._lock
        if lock is None:
            return False

        websocket: WebSocket | None = None
        ping_task: asyncio.Task | None = None
        pending_futures: list[asyncio.Future] = []
        catalog_change = (None, None)
        publication_error = None
        async with lock:
            if expected is not None and (
                cls._registry is not expected[0]
                or cls._connections.get(session_id) is not expected[1]
            ):
                return False
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
            if registry is not None:
                try:
                    catalog_change = await cls._unregister_catalog(registry, session_id)
                except Exception as exc:
                    # A failed publication retains the prior catalog/registry;
                    # it cannot retain physical socket or command ownership.
                    publication_error = exc

        if ping_task is not None and ping_task is not initiating_task and not ping_task.done():
            ping_task.cancel()

        for future in pending_futures:
            if not future.done():
                future.set_exception(
                    PluginDisconnectedError(
                        f"Unity plugin session {session_id} disconnected while awaiting command_result"
                    )
                )

        try:
            await cls._notify_committed_catalog(*catalog_change)
        finally:
            if websocket is not None:
                await cls._close_websocket(websocket)
        if publication_error is not None:
            raise publication_error

        return registry is not None

    @classmethod
    async def _ensure_live_connection(cls, session_id: str) -> bool:
        """Best-effort pre-send liveness check for a plugin WebSocket."""
        try:
            websocket = await cls._get_connection(session_id)
        except RuntimeError:
            await cls._evict_connection(session_id, "missing_websocket", detach_on_cancel=True)
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
        await cls._evict_connection(session_id, "stale_websocket_state", detach_on_cancel=True)
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

        # Explicitly selected projects may be compiling/importing large workloads.
        # Unselected discovery retains its short window and ambiguity checks.
        max_wait_s = _read_bounded_wait_env(
            "UNITY_MCP_SESSION_RESOLVE_MAX_WAIT_S",
            default_s=300.0 if unity_instance else 20.0,
            max_s=MAX_COMMAND_TIMEOUT,
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
        _catalog_guard=None,
        _progress: ProgressSink | None = None,
    ) -> dict[str, Any]:
        """Send with one total execution budget, including recovery and admission.

        The existing per-command policy selects the budget, including long jobs.
        Cancellation cleanup may use up to CANCEL_TIMEOUT after it expires;
        stale-session eviction remains server-owned and is drained on shutdown.

        Args:
            unity_instance: Target instance (Name@hash or hash)
            command_type: Command type to execute
            params: Command parameters
            user_id: User ID for session isolation in remote-hosted mode
            retry_on_reload: If False, do not wait for session reconnect on reload.
        """
        with phase_diagnostics.invocation(command_type):
            timeout_s = cls._command_timeouts(command_type, params)[1]
            request = http_request_budget.get()
            budget = (
                request.command_budget(command_type, timeout_s)
                if request is not None
                else _CommandBudget(
                    asyncio.get_running_loop().time() + timeout_s, command=command_type
                )
            )
            try:
                async with asyncio.timeout_at(budget.expires_at):
                    result = await cls._send_command_for_instance(
                        unity_instance,
                        command_type,
                        params,
                        user_id,
                        retry_on_reload,
                        editor_state_read_mode=editor_state_read_mode,
                        budget=budget,
                        _catalog_guard=_catalog_guard,
                        **({"_progress": _progress} if _progress is not None else {}),
                    )
            except OperationPermissionError:
                if budget.dispatched:
                    result = command_outcome_unknown(
                        command_type,
                        "Remote operation permission ended",
                        session_id=budget.session_id,
                        command_id=budget.command_id,
                        phase=budget.phase,
                        cause="permission_ended",
                    )
                else:
                    result = MCPResponse(success=False, error="permission_denied").model_dump()
            except CredentialLeaseError:
                if budget.dispatched:
                    result = command_outcome_unknown(
                        command_type,
                        "Remote authorization ended",
                        session_id=budget.session_id,
                        command_id=budget.command_id,
                        phase=budget.phase,
                        cause="authentication_ended",
                    )
                else:
                    result = MCPResponse(success=False, error="auth_required").model_dump()
            except TimeoutError:
                result = budget.timeout_response(command_type)
            observe_native_result(result)
            return result

    @classmethod
    async def _send_command_for_instance(
        cls,
        unity_instance: str | None,
        command_type: str,
        params: dict[str, Any],
        user_id: str | None = None,
        retry_on_reload: bool = True,
        *,
        budget: _CommandBudget,
        editor_state_read_mode: Literal["ordinary", "authoritative"] | None = None,
        _catalog_guard=None,
        _progress: ProgressSink | None = None,
    ) -> dict[str, Any]:
        phase_diagnostics.step(command_type, "preflight")
        requester = await require_credential_lease(user_id) if config.http_remote_hosted else None
        if requester is not None:
            authorize_operation(
                requester.permissions,
                command_type,
                params,
                custom=_catalog_guard is not None,
                native=True,
            )
        context = local_project_context.get()
        if context is not None:
            selected = context.selection
            if (
                selected is None
                or full_hash(unity_instance or "") != selected.project_hash
                or user_id != selected.user_id
            ):
                return context.refuse("project_context_changed", params)
            return await cls.send_command(
                selected.session_id,
                command_type,
                params,
                _budget=budget,
                **({"_catalog_guard": _catalog_guard} if _catalog_guard is not None else {}),
                **({"_progress": _progress} if _progress is not None else {}),
            )
        if _catalog_guard is not None:
            retry_on_reload = False
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

        budget.session_id = session_id
        budget.phase = "recovery"
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
            budget.session_id = session_id
            if not await cls._ensure_live_connection(session_id):
                return cls._unavailable_retry_response("stale_connection")

        probe_allowed = True
        if requester is not None:
            websocket = cls._connections.get(session_id)
            if websocket is None:
                return cls._unavailable_retry_response("stale_connection")
            plugin = cls._socket_lease(websocket)
            if plugin is None or plugin.user_id != requester.user_id:
                raise CredentialLeaseError()
            await plugin.check()
            # Admit the operation before shared reads or optional native probes.
            authorize_operation(
                plugin.permissions,
                command_type,
                params,
                custom=_catalog_guard is not None,
                native=True,
            )
            try:
                for authority in (requester, plugin):
                    authorize_operation(authority.permissions, "ping", {}, native=True)
            except OperationPermissionError:
                probe_allowed = False
        budget.phase = "readiness"
        share_state = (
            command_type == "get_editor_state"
            and not params
            and editor_state_read_mode == "ordinary"
        )
        identity = None
        if command_type == "get_editor_state" or command_type in cls._FAST_FAIL_COMMANDS:
            identity = await cls._read_identity(
                session_id, authoritative=command_type == "get_editor_state" and not share_state
            )

        # During domain reload / immediate reconnect windows, the plugin may be connected but not yet
        # ready to process execute commands on the Unity main thread (which can be further delayed when
        # the Unity Editor is unfocused). For fast-path commands, we do a bounded readiness probe using
        # a main-thread ping command (handled by TransportCommandDispatcher) rather than waiting on
        # register_tools (which can be delayed by EditorApplication.delayCall).
        if (
            retry_on_reload
            and probe_allowed
            and command_type in cls._FAST_FAIL_COMMANDS
            and command_type != "ping"
        ):
            max_wait_s = _read_bounded_wait_env(
                "UNITY_MCP_SESSION_READY_WAIT_SECONDS", default_s=6.0, max_s=MAX_COMMAND_TIMEOUT
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

            def observe_flight(task: asyncio.Task) -> None:
                owner = cls._ordinary_state_flights.setdefault(task, budget)
                if owner is not budget:
                    budget.active = owner

            try:
                async with cls._ordinary_state_reads.session(identity) as read:
                    return await read.fetch(
                        lambda: cls.send_command(
                            session_id,
                            command_type,
                            params,
                            _expected_read=identity,
                            _budget=budget,
                        ),
                        on_flight=observe_flight,
                    )
            except SharedReadCapacityError:
                return response_limit_error("result_capacity")
        if identity is not None and command_type in cls._FAST_FAIL_COMMANDS:
            return await cls.send_command(
                session_id, command_type, params, _expected_read=identity, _budget=budget
            )
        return await cls.send_command(
            session_id,
            command_type,
            params,
            _budget=budget,
            **({"_catalog_guard": _catalog_guard} if _catalog_guard is not None else {}),
            **({"_progress": _progress} if _progress is not None else {}),
        )

    # ------------------------------------------------------------------
    # Blocking helpers for synchronous tool code
    # ------------------------------------------------------------------
    @classmethod
    def _run_coroutine_sync(cls, coro: Coroutine[Any, Any, Any]) -> Any:
        try:
            loop = cls._loop
            if loop is None:
                raise RuntimeError("PluginHub event loop not configured")
            if not loop.is_running():
                raise RuntimeError("PluginHub event loop is not running")
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
        except RuntimeError:
            # Before submission succeeds, this adapter still owns the coroutine.
            coro.close()
            raise
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
