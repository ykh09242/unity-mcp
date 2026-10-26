from services.execution_receipt_context import (
    execution_owner,
    observe_native_result,
    submit_owned_executor,
)
from uuid import uuid4
from core.config import config
from core.server_build import RUNNING_SERVER
import contextlib
from contextvars import Context, ContextVar
from dataclasses import dataclass, field
import errno
import json
import logging
import os
from pathlib import Path
from transport.legacy.port_discovery import PortDiscovery
import random
import re
import socket
import struct
import sys
import threading
import time
from typing import Any, Callable, TYPE_CHECKING
import weakref

from pydantic_core import from_json
from transport.json_decoder import decode_json

if TYPE_CHECKING:
    import asyncio

_resync_pool: ContextVar["UnityConnectionPool | None"] = ContextVar(
    "stdio_resync_pool", default=None
)

from models.models import MCPResponse, UnityInstanceInfo
from services.command_progress import (
    CAPABILITY,
    MAX_PROGRESS_BYTES,
    MAX_PROGRESS_FRAMES,
    CommandProgressUpdate,
    ProgressSink,
    ProgressSource,
)
from transport.blender_timeout import blender_command_timeout, SERVER_RESPONSE_GRACE
from transport.command_timeout import requested_command_timeout, configured_wait_timeout
from transport.legacy.stdio_port_registry import stdio_port_registry
from transport.legacy.stdio_auth import (
    StdioAuthentication,
    StdioAuthenticationError,
    StdioAuthenticatedSession,
    authenticate_stdio_session,
)
from transport.legacy.stdio_credentials import read_stdio_token
from transport.native_arbitration import (
    CAPABILITY as ARBITRATION_CAPABILITY,
    NativeArbitrationRequest,
    NativeArbitrationProtocolError,
    NativeNotStarted,
    decode_native_reply,
    valid_generation,
)


logger = logging.getLogger("mcp-for-unity-server")

# Module-level lock to guard global connection initialization
_connection_lock = threading.Lock()

# Maximum allowed framed payload size (128 MiB, including envelope overhead)
FRAMED_MAX = 128 * 1024 * 1024


class _UnityProtocolError(ValueError):
    """A Unity response cannot be safely decoded or reused."""


class _AsyncAdmissionGate:
    """Track holders and waiters, including the release-to-resume handoff."""

    def __init__(self):
        import asyncio

        self._lock = asyncio.Lock()
        self._participants = 0

    async def acquire(self) -> bool:
        self._participants += 1
        acquired = False
        try:
            acquired = await self._lock.acquire()
            return acquired
        finally:
            if not acquired:
                self._participants -= 1

    def release(self) -> None:
        self._lock.release()
        self._participants -= 1

    def in_use(self) -> bool:
        return self._participants > 0

    def locked(self) -> bool:
        return self._lock.locked()


def _decode_unity_response(data: bytes | bytearray) -> Any:
    """Decode JSON before callers enforce the existing object response shape."""
    # Retain this helper and its imported native decoder for existing adapters.
    return decode_json(data, native_decoder=from_json)


def read_status_file(target_hash: str | None = None) -> dict | None:
    try:
        base_path = PortDiscovery.get_registry_dir()
        # Canonical hashes contain no glob/path syntax. Narrow enumeration to
        # their suffix, preserving newer legacy files that share that suffix.
        pattern = "unity-mcp-status-*.json"
        if target_hash and re.fullmatch(r"[0-9a-fA-F]{8}(?:[0-9a-fA-F]{8})?", target_hash):
            pattern = f"unity-mcp-status-*{target_hash}.json"
        status_files = [
            path
            for path in base_path.glob(pattern)
            if not target_hash or path.stem.endswith(target_hash)
        ]
        # A single target needs no metadata stat; ambiguous suffixes and an
        # untargeted read retain the existing most-recent-file precedence.
        if len(status_files) > 1:
            status_files.sort(key=lambda path: path.stat().st_mtime, reverse=True)
        if not status_files:
            return None
        if target_hash:
            for status_path in status_files:
                if status_path.stem.endswith(target_hash):
                    with status_path.open("r") as f:
                        return json.load(f)
            return None
        # Untargeted legacy connections use the most recent status.
        with status_files[0].open("r") as f:
            return json.load(f)
    except FileNotFoundError:
        logger.debug("Unity status file disappeared before it could be read")
        return None
    except json.JSONDecodeError as exc:
        logger.warning(f"Malformed Unity status file: {exc}")
        return None
    except OSError as exc:
        logger.warning(f"Failed to read Unity status file: {exc}")
        return None
    except Exception as exc:
        logger.debug(f"Preflight status check failed: {exc}")
        return None


@dataclass
class UnityConnection:
    """Manages the socket connection to the Unity Editor."""

    host: str = config.unity_host
    port: int = None  # Will be set dynamically
    sock: socket.socket = None  # Socket for Unity communication
    use_framing: bool = False  # Negotiated per-connection
    instance_id: str | None = None  # Instance identifier for reconnection
    auth_token_provider: Callable[[str], str | None] | None = field(default=None, repr=False)
    allow_legacy_auth: bool | None = None
    session_generation: str | None = field(default=None, init=False)
    authenticated_session: StdioAuthenticatedSession | None = field(default=None, init=False)

    def __post_init__(self):
        """Set port from discovery if not explicitly provided"""
        if self.port is None:
            self.port = stdio_port_registry.get_port(self.instance_id)
        self._io_lock = threading.RLock()
        self._active_commands = 0
        self._retired = False
        self._pool_owner: weakref.ReferenceType[UnityConnectionPool] | None = None
        # Connection publication, liveness checks, close, and commands share one
        # lock. Lifecycle helpers are also called from a command transaction.
        self._conn_lock = self._io_lock
        self._resync_lock = threading.Lock()
        # Weak keys AND values avoid retaining a closed loop through a bound
        # asyncio.Lock. Active callers/completion callbacks retain their gate.
        self._async_admissions = weakref.WeakKeyDictionary()
        self._needs_tool_resync = False  # Set True after reconnection
        self._authentication_failure: StdioAuthenticationError | None = None
        self._progress_owner: tuple[threading.Event, ProgressSink] | None = None
        if self.allow_legacy_auth is None:
            self.allow_legacy_auth = os.environ.get("UNITY_MCP_STDIO_ALLOW_LEGACY") == "1"

    def _async_admission(self, loop: "asyncio.AbstractEventLoop") -> _AsyncAdmissionGate:
        """Share one worker admission per connection and event loop."""
        with self._resync_lock:
            reference = self._async_admissions.get(loop)
            gate = reference() if reference is not None else None
            if gate is None:
                gate = _AsyncAdmissionGate()
                self._async_admissions[loop] = weakref.ref(gate)
            return gate

    def claim_tool_resync(self) -> bool:
        """Claim reconnect metadata work without waiting for socket I/O."""
        with self._resync_lock:
            pending = self._needs_tool_resync
            self._needs_tool_resync = False
            return pending

    def _prepare_socket(self, sock: socket.socket) -> None:
        try:
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        except OSError as exc:
            logger.debug(f"Unable to set TCP_NODELAY: {exc}")

    def _close_stdio_progress(self) -> None:
        owner, self._progress_owner = getattr(self, "_progress_owner", None), None
        if owner is not None:
            active, sink = owner
            active.clear()
            with contextlib.suppress(Exception):
                sink.close()

    def _begin_stdio_progress(
        self,
        sink: ProgressSink | None,
        command_type: str,
        params: dict[str, Any],
        deadline: float | None,
    ) -> tuple[str, bytes] | None:
        """Bind one opaque foreground sink while this command owns the I/O lock."""
        self._close_stdio_progress()
        if sink is None:
            return None
        session = self.authenticated_session
        commands = params.get("commands") if isinstance(params, dict) else None
        if (
            command_type != "batch_execute"
            or params.get("validate_only")
            or not isinstance(commands, list)
            or not 1 <= len(commands) <= 100
            or not self.use_framing
            or session is None
            or session.session_generation != self.session_generation
            or CAPABILITY not in session.capabilities
        ):
            with contextlib.suppress(Exception):
                sink.close()
            return None
        command_id = str(uuid4())
        active = threading.Event()

        async def still_owned() -> bool:
            # The I/O owner clears this invocation-specific fence before response,
            # disconnect or reset. Never take its blocking lock on the SDK loop.
            return active.is_set()

        try:
            bound = sink.bind(
                ProgressSource(
                    transport="stdio",
                    command_id=command_id,
                    session_id=session.session_id,
                    generation=session.server_generation,
                    user_id=None,
                    expires_at=deadline,
                    guard=still_owned,
                )
            )
        except Exception:
            bound = False
        if not bound:
            with contextlib.suppress(Exception):
                sink.close()
            return None
        active.set()
        self._progress_owner = active, sink
        prelude = json.dumps(
            {"type": "command_progress_begin", "version": 1, "id": command_id},
            separators=(",", ":"),
        ).encode("ascii")
        return command_id, prelude

    def connect(self, connect_timeout: float | None = None, deadline: float | None = None) -> bool:
        """Establish a connection to the Unity Editor."""
        if config.http_remote_hosted:
            raise RuntimeError("Legacy Unity connections are disabled in remote-hosted mode")
        with self._conn_lock:
            if self._retired:
                return False
            if self.sock:
                return True
            self.session_generation = None
            self.authenticated_session = None
            self._close_stdio_progress()
            self._authentication_failure = None
            try:
                # Bounded connect to avoid indefinite blocking
                if connect_timeout is None:
                    connect_timeout = float(getattr(config, "connection_timeout", 1.0))
                self._check_deadline(deadline)
                connect_timeout = self._cap_to_deadline(connect_timeout, deadline)
                # We trust config.unity_host (default 127.0.0.1) but future improvements
                # could dynamically prefer 'localhost' depending on OS resolver behavior.
                self.sock = socket.create_connection((self.host, self.port), connect_timeout)
                self._check_deadline(deadline)
                self._prepare_socket(self.sock)
                with self._resync_lock:
                    self._needs_tool_resync = True
                logger.debug(f"Connected to Unity at {self.host}:{self.port}")

                # Strict handshake: require FRAMING=1
                try:
                    require_framing = getattr(config, "require_framing", True)
                    handshake_timeout = float(getattr(config, "handshake_timeout", 1.0))
                    self.sock.settimeout(handshake_timeout)
                    buf = bytearray()
                    handshake_deadline = time.monotonic() + handshake_timeout
                    if deadline is not None:
                        handshake_deadline = min(handshake_deadline, deadline)
                    while time.monotonic() < handshake_deadline and len(buf) < 512:
                        try:
                            self._set_socket_deadline(self.sock, handshake_deadline)
                            # TCP can coalesce the greeting with the next frame.
                            # Consume only through LF, leaving framed bytes for
                            # their existing reader (including v2 authentication).
                            chunk = self.sock.recv(1)
                            self._check_deadline(handshake_deadline)
                            if not chunk:
                                break
                            buf.extend(chunk)
                            if b"\n" in buf:
                                break
                        except socket.timeout:
                            break
                    self._check_deadline(handshake_deadline)
                    if b"\n" not in buf:
                        raise StdioAuthenticationError("Incomplete stdio greeting")
                    text = bytes(buf).decode("ascii").strip()

                    if text.startswith("WELCOME UNITY-MCP 2") or "AUTH=" in text:
                        session = authenticate_stdio_session(
                            self.sock,
                            text,
                            StdioAuthentication(
                                self.auth_token_provider or read_stdio_token, handshake_deadline
                            ),
                            capabilities=(CAPABILITY, ARBITRATION_CAPABILITY),
                        )
                        self._check_deadline(handshake_deadline)
                        self.authenticated_session = session
                        self.session_generation = session.session_generation
                        self.use_framing = True
                        logger.debug("Authenticated stdio connection established")
                    else:
                        if not self.allow_legacy_auth:
                            raise StdioAuthenticationError(
                                "Unauthenticated legacy stdio requires explicit UNITY_MCP_STDIO_ALLOW_LEGACY=1"
                            )
                        if not re.fullmatch(r"WELCOME UNITY-MCP 1(?: FRAMING=1)?", text):
                            raise StdioAuthenticationError("Unsupported stdio greeting")
                        if "FRAMING=1" in text:
                            self.use_framing = True
                            logger.warning(
                                "Unauthenticated legacy stdio enabled by explicit configuration"
                            )
                        elif require_framing:
                            # Best-effort plain-text advisory for legacy peers
                            with contextlib.suppress(Exception):
                                self._set_socket_deadline(self.sock, deadline)
                                self.sock.sendall(b"MCP for Unity requires FRAMING=1\n")
                                self._check_deadline(deadline)
                            raise ConnectionError(
                                f"MCP for Unity requires FRAMING=1, got: {text!r}"
                            )
                        else:
                            self.use_framing = False
                            logger.warning(
                                "MCP for Unity handshake missing FRAMING=1; proceeding in legacy mode by configuration"
                            )
                finally:
                    self.sock.settimeout(config.connection_timeout)
                self._check_deadline(handshake_deadline)
                self._check_deadline(deadline)
                return True
            except Exception as e:
                if isinstance(e, StdioAuthenticationError):
                    self._authentication_failure = e
                logger.error(f"Failed to connect to Unity: {str(e)}")
                try:
                    if self.sock:
                        self.sock.close()
                except Exception:
                    pass
                self.sock = None
                self.session_generation = None
                self.authenticated_session = None
                self._close_stdio_progress()
                return False

    def disconnect(self):
        """Close the connection to the Unity Editor."""
        with self._io_lock:
            self._close_stdio_progress()
            self.authenticated_session = None
            self.session_generation = None
            if self.sock:
                try:
                    self.sock.close()
                except Exception as e:
                    logger.error(f"Error disconnecting from Unity: {str(e)}")
                finally:
                    self.sock = None
                    self.session_generation = None

    def _try_retire(self) -> bool:
        """Seal an idle pooled generation without interrupting running/queued I/O."""
        if not self._io_lock.acquire(blocking=False):
            return False
        try:
            # RLock is reentrant: discovery called from this command must not
            # mistake its own held lock for an idle connection.
            if self._active_commands:
                return False
            with self._resync_lock:
                for reference in self._async_admissions.values():
                    gate = reference()
                    if gate is not None and gate.in_use():
                        return False
                self._retired = True
            self.disconnect()
            return True
        finally:
            self._io_lock.release()

    def _ensure_live_connection(self) -> None:
        """Detect and discard cleanly-closed sockets before sending.

        A graceful domain reload closes the bridge's TCP connections (FIN), so a
        non-blocking peek sees EOF and lets send_command reconnect instead of
        writing to a dead socket. A half-open socket left without a FIN is not
        caught here; that case is bounded by the per-command deadline and the
        reloading-status preflight.
        """
        with self._io_lock:
            if not self.sock:
                return
            original_socket = self.sock
            original_timeout = original_socket.gettimeout()
            try:
                self.sock.setblocking(False)
                data = self.sock.recv(1, socket.MSG_PEEK)
                if not data:
                    raise ConnectionError("peer closed")
            except BlockingIOError:
                pass  # No data pending; socket is alive
            except Exception:
                logger.debug("Stale socket detected; will reconnect on next send")
                try:
                    self.sock.close()
                except Exception:
                    pass
                self.sock = None
                self.session_generation = None
                self.authenticated_session = None
                self._close_stdio_progress()
            finally:
                if self.sock is original_socket:
                    original_socket.settimeout(original_timeout)

    def _read_exact(self, sock: socket.socket, count: int, deadline: float | None = None) -> bytes:
        return bytes(self._read_exact_buffer(sock, count, deadline))

    def _read_exact_buffer(
        self,
        sock: socket.socket,
        count: int,
        deadline: float | None = None,
        *,
        _prefix: bytearray | None = None,
    ) -> bytearray:
        """Grow only one receive slab ahead; keep the final payload buffer owned."""
        data = bytearray() if _prefix is None else _prefix
        received = len(data)
        receive_into = getattr(sock, "recv_into", None)
        while received < count:
            data.extend(b"\0" * min(65_536, count - received))
            with memoryview(data) as destination:
                while received < len(data):
                    self._set_socket_deadline(sock, deadline)
                    with destination[received:] as remaining:
                        if receive_into is not None:
                            size = receive_into(remaining)
                        else:
                            # Retain recv-only adapters used by legacy integrations.
                            chunk = sock.recv(len(remaining))
                            size = len(chunk)
                            remaining[:size] = chunk
                    self._check_deadline(deadline)
                    if not size:
                        raise ConnectionError("Connection closed before reading expected bytes")
                    received += size
        return data

    @staticmethod
    def _offer_stdio_progress(sink: ProgressSink, command_id: str, raw: bytearray) -> None:
        try:
            message = json.loads(raw)
        except (ValueError, UnicodeError):
            return
        if (
            not isinstance(message, dict)
            or set(message) != {"type", "id", "completed", "total", "stage"}
            or message["type"] != "command_progress"
            or message["id"] != command_id
            or type(message["completed"]) is not int
            or type(message["total"]) is not int
            or not 1 <= message["completed"] <= message["total"] <= 100
            or message["stage"] != "batch_items"
        ):
            return
        # The sink enforces the claimed total, monotonicity, foreground lifetime
        # and SDK-loop ownership. A notification failure never changes Unity work.
        with contextlib.suppress(Exception):
            sink.offer_from_thread(
                CommandProgressUpdate(message["completed"], message["total"], "batch_items")
            )

    def receive_full_response(
        self,
        sock,
        buffer_size=config.buffer_size,
        deadline: float | None = None,
        *,
        _progress: ProgressSink | None = None,
        _progress_id: str | None = None,
    ) -> bytes | bytearray:
        """Receive a complete response from Unity, handling chunked data."""
        if self.use_framing:
            # Heartbeat semantics: the Unity editor emits zero-length frames while
            # a long-running command is still executing. We tolerate a bounded
            # number of these frames (or a small time window) before surfacing a
            # timeout to the caller so tools can retry or fail gracefully.
            heartbeat_limit = getattr(config, "max_heartbeat_frames", 16)
            heartbeat_window = getattr(config, "heartbeat_timeout", 2.0)
            heartbeat_started = time.monotonic()
            heartbeat_count = 0
            progress_frames = 0
            try:
                while True:
                    header = self._read_exact(sock, 8, deadline)
                    payload_len = struct.unpack(">Q", header)[0]
                    if payload_len == 0:
                        heartbeat_count += 1
                        logger.debug(f"Received heartbeat frame #{heartbeat_count}")
                        if (
                            heartbeat_count >= heartbeat_limit
                            or (time.monotonic() - heartbeat_started) > heartbeat_window
                        ):
                            raise TimeoutError(
                                "Unity sent heartbeat frames without payload within configured threshold"
                            )
                        continue
                    if payload_len > FRAMED_MAX:
                        raise _UnityProtocolError(f"Invalid framed length: {payload_len}")
                    if _progress is not None and _progress_id is not None:
                        prefix = self._read_exact_buffer(sock, min(4, payload_len), deadline)
                        if prefix == b"MCPG":
                            progress_frames += 1
                            remaining = payload_len - 4
                            if (
                                remaining > MAX_PROGRESS_BYTES
                                or progress_frames > MAX_PROGRESS_FRAMES
                            ):
                                while remaining:
                                    slab = min(4096, remaining)
                                    self._read_exact_buffer(sock, slab, deadline)
                                    remaining -= slab
                            else:
                                raw = self._read_exact_buffer(sock, remaining, deadline)
                                self._offer_stdio_progress(_progress, _progress_id, raw)
                            continue
                        # Reuse the peeked slab as the start of the final buffer.
                        return self._read_exact_buffer(sock, payload_len, deadline, _prefix=prefix)
                    payload = self._read_exact_buffer(sock, payload_len, deadline)
                    logger.debug(f"Received framed response ({len(payload)} bytes)")
                    return payload
            except socket.timeout as exc:
                logger.warning("Socket timeout during framed receive")
                raise TimeoutError("Timeout receiving Unity response") from exc
            except Exception as exc:
                logger.error(f"Error during framed receive: {exc}")
                raise

        chunks = []
        # Respect the socket's currently configured timeout
        try:
            while True:
                self._set_socket_deadline(sock, deadline)
                chunk = sock.recv(buffer_size)
                self._check_deadline(deadline)
                if not chunk:
                    if not chunks:
                        raise Exception("Connection closed before receiving data")
                    break
                chunks.append(chunk)

                # Process the data received so far
                data = b"".join(chunks)
                decoded_data = data.decode("utf-8")

                # Check if we've received a complete response
                try:
                    # Special case for ping-pong
                    if decoded_data.strip().startswith(
                        '{"status":"success","result":{"message":"pong"'
                    ):
                        logger.debug("Received ping response")
                        return data

                    # Handle escaped quotes in the content
                    if '"content":' in decoded_data:
                        # Find the content field and its value
                        content_start = decoded_data.find('"content":') + 9
                        content_end = decoded_data.rfind('"', content_start)
                        if content_end > content_start:
                            # Replace escaped quotes in content with regular quotes
                            content = decoded_data[content_start:content_end]
                            content = content.replace('\\"', '"')
                            decoded_data = (
                                decoded_data[:content_start] + content + decoded_data[content_end:]
                            )

                    # Validate JSON format
                    json.loads(decoded_data)

                    # If we get here, we have valid JSON
                    logger.info(f"Received complete response ({len(data)} bytes)")
                    return data
                except json.JSONDecodeError:
                    # We haven't received a complete valid JSON response yet
                    continue
                except Exception as e:
                    logger.warning(f"Error processing response chunk: {str(e)}")
                    # Continue reading more chunks as this might not be the complete response
                    continue
        except socket.timeout:
            logger.warning("Socket timeout during receive")
            if deadline is not None:
                raise TimeoutError("Timeout receiving Unity response")
            raise Exception("Timeout receiving Unity response")
        except Exception as e:
            logger.error(f"Error during receive: {str(e)}")
            raise

    def _check_deadline(self, deadline: float | None) -> None:
        """Reject blocking I/O or completed responses after the command budget."""
        if deadline is not None and time.monotonic() >= deadline:
            raise TimeoutError("Unity command exceeded total deadline")

    def _set_socket_deadline(self, sock: socket.socket, deadline: float | None) -> None:
        """Cap the next socket operation to the remaining absolute budget."""
        if deadline is not None:
            self._check_deadline(deadline)
            remaining = deadline - time.monotonic()
            timeout = sock.gettimeout()
            sock.settimeout(remaining if timeout is None else min(timeout, remaining))

    def _cap_to_deadline(self, timeout: float, deadline: float | None, floor: float = 0.0) -> float:
        """Shrink a blocking timeout to whatever budget remains before the deadline."""
        if deadline is None:
            return timeout
        return max(floor, min(timeout, deadline - time.monotonic()))

    @contextlib.contextmanager
    def _command_lock(self, deadline: float | None):
        """Include time queued behind another command in the total budget."""
        self._check_deadline(deadline)
        acquired = (
            self._io_lock.acquire()
            if deadline is None
            else self._io_lock.acquire(timeout=max(0.0, deadline - time.monotonic()))
        )
        if not acquired:
            raise TimeoutError("Unity command exceeded total deadline waiting for connection")
        entered = False
        try:
            self._check_deadline(deadline)
            if self._retired:
                raise ConnectionError("Unity connection was retired; select the instance again")
            self._active_commands += 1
            entered = True
            yield
        finally:
            if entered:
                self._active_commands -= 1
            self._io_lock.release()

    def send_command(
        self,
        command_type: str,
        params: dict[str, Any] | None = None,
        max_attempts: int | None = None,
        deadline: float | None = None,
        _expected_generation: str | None = None,
        _catalog_guard=None,
        _progress: ProgressSink | None = None,
    ) -> dict[str, Any]:
        """Send a command with retry/backoff and port rediscovery. Pings only when requested.

        Args:
            command_type: The Unity command to send
            params: Command parameters
            max_attempts: Maximum retry attempts (None = use config default, 0 = no retries)
            deadline: Shared monotonic() ceiling across retries (None = derive from command_total_timeout)
        """
        if config.http_remote_hosted:
            raise RuntimeError("Legacy Unity connections are disabled in remote-hosted mode")
        # Defensive guard: catch empty/placeholder invocations early
        if not command_type:
            raise ValueError("MCP call missing command_type")
        if params is None:
            return MCPResponse(
                success=False, error="MCP call received with no parameters (client placeholder?)"
            )
        attempts = max(config.max_retries, 5) if max_attempts is None else max_attempts
        base_backoff = max(0.5, config.retry_delay)
        blender_timeout = (
            blender_command_timeout(params) if command_type == "blender_bridge" else None
        )
        requested_timeout = requested_command_timeout(params)
        command_sent = False
        receipt_owner = execution_owner.get()
        if receipt_owner is not None:
            deadline = (
                min(deadline, receipt_owner.expires_at)
                if deadline is not None
                else receipt_owner.expires_at
            )

        # Cap total time across all retries so a wedged socket can't block unbounded.
        total_timeout = max(0.0, float(getattr(config, "command_total_timeout", 90.0)))
        if blender_timeout is not None:
            total_timeout = blender_timeout + SERVER_RESPONSE_GRACE
        elif requested_timeout is not None:
            total_timeout = max(total_timeout, requested_timeout + SERVER_RESPONSE_GRACE)
        if deadline is None and total_timeout > 0:
            deadline = time.monotonic() + total_timeout

        # Canonical IDs end in the hash; older IDs may be just the hash.
        target_hash: str | None = None
        if self.instance_id:
            maybe_hash = self.instance_id.rsplit("@", 1)[-1].strip()
            if maybe_hash:
                target_hash = maybe_hash

        # Preflight: if Unity reports reloading, return a structured hint so clients can retry politely
        try:
            status = read_status_file(target_hash)
            if status and (status.get("reloading") or status.get("reason") == "reloading"):
                # Reload invalidates the socket; drop it under the I/O lock so this
                # close is serialized against the send/recv block, then reconnect next call.
                with self._command_lock(deadline):
                    self.disconnect()
                return MCPResponse(
                    success=False,
                    error="Unity is reloading; please retry",
                    hint="retry",
                    data={"reason": "reloading"},
                )
        except Exception as exc:
            logger.debug(f"Preflight status check failed: {exc}")

        for attempt in range(attempts + 1):
            if deadline is not None and time.monotonic() >= deadline:
                logger.warning(
                    "Command '%s' exceeded total deadline of %.1fs after %d attempt(s); giving up",
                    command_type,
                    total_timeout,
                    attempt,
                )
                raise TimeoutError(
                    f"Command '{command_type}' exceeded total deadline of "
                    f"{total_timeout:.1f}s (connection wedged or Unity unresponsive)"
                )
            response_received = False
            with self._command_lock(deadline):
                try:
                    # Discard stale sockets left over from a previous domain reload
                    # so we reconnect instead of writing to a dead connection.
                    self._ensure_live_connection()
                    if _expected_generation is not None and (
                        not self.sock or self.session_generation != _expected_generation
                    ):
                        return MCPResponse(
                            success=False,
                            error="observation_generation_changed",
                            data={"reason": "observation_generation_changed"},
                        ).model_dump()
                    # Ensure connected (handshake occurs within connect())
                    t_conn_start = time.time()
                    if not self.sock and not self.connect(
                        self._cap_to_deadline(config.connection_timeout, deadline),
                        deadline=deadline,
                    ):
                        if self._authentication_failure is not None:
                            raise self._authentication_failure
                        raise ConnectionError("Could not connect to Unity")
                    logger.info(
                        "[TIMING-STDIO] connect took %.3fs command=%s",
                        time.time() - t_conn_start,
                        command_type,
                    )

                    # Linearize catalog admission after liveness/reconnect,
                    # while this exact connection owns the existing I/O lock.
                    if _catalog_guard is not None and not _catalog_guard.check_stdio(self):
                        from services.custom_tool_service import custom_catalog_changed

                        return custom_catalog_changed().model_dump()

                    receipt = execution_owner.get()
                    receipt_attempt = uuid4().hex if receipt is not None else None
                    receipt_project = (
                        self.instance_id.rsplit("@", 1)[-1].lower()
                        if receipt is not None and self.instance_id is not None
                        else None
                    )
                    if receipt is not None:
                        receipt.native(
                            command_type,
                            params,
                            custom=_catalog_guard is not None,
                            kind="stdio",
                            attempt_id=receipt_attempt,
                            session_id="stdio:" + receipt_project,
                            generation=self.session_generation,
                            project_hash=receipt_project,
                        )
                    # Build payload
                    session = self.authenticated_session
                    arbitration = (
                        NativeArbitrationRequest.create(session.arbitration_generation)
                        if session is not None
                        and ARBITRATION_CAPABILITY in session.capabilities
                        and valid_generation(session.arbitration_generation)
                        else None
                    )
                    if command_type == "ping" and arbitration is None:
                        payload = b"ping"
                    else:
                        payload = json.dumps(
                            {
                                "type": command_type,
                                "params": params,
                                "server_info": RUNNING_SERVER.command_metadata(),
                                **(
                                    {"arbitration": arbitration.to_wire()}
                                    if arbitration is not None
                                    else {}
                                ),
                            }
                        ).encode("utf-8")

                    # The lifetime lock also protects timeout restoration.
                    mode = "framed" if self.use_framing else "legacy"
                    with contextlib.suppress(Exception):
                        logger.debug(
                            f"send {len(payload)} bytes; mode={mode}; head={payload[:32].decode('utf-8', 'ignore')}"
                        )
                    restore_timeout = self.sock.gettimeout()
                    try:
                        if blender_timeout is not None:
                            self.sock.settimeout(blender_timeout)
                        t_send_start = time.time()
                        association = self._begin_stdio_progress(
                            _progress, command_type, params, deadline
                        )
                        if association is not None:
                            _, prelude = association
                            # Association is not native dispatch. Keep the business
                            # payload byte-identical and both writes in this lock.
                            self._set_socket_deadline(self.sock, deadline)
                            self.sock.sendall(struct.pack(">Q", len(prelude)))
                            self._check_deadline(deadline)
                            self._set_socket_deadline(self.sock, deadline)
                            self.sock.sendall(prelude)
                            self._check_deadline(deadline)
                        if receipt is not None:
                            receipt.dispatch_started(
                                kind="stdio",
                                attempt_id=receipt_attempt,
                                session_id="stdio:" + receipt_project,
                                generation=self.session_generation,
                            )
                        if self.use_framing:
                            header = struct.pack(">Q", len(payload))
                            self._set_socket_deadline(self.sock, deadline)
                            self.sock.sendall(header)
                            self._check_deadline(deadline)
                        self._set_socket_deadline(self.sock, deadline)
                        command_sent = True
                        self.sock.sendall(payload)
                        self._check_deadline(deadline)
                        logger.info(
                            "[TIMING-STDIO] sendall took %.3fs command=%s",
                            time.time() - t_send_start,
                            command_type,
                        )

                        recv_timeout = (
                            blender_timeout
                            if blender_timeout is not None
                            else (1.0 if attempt > 0 else restore_timeout)
                        )
                        if requested_timeout is not None and blender_timeout is None:
                            recv_timeout = max(
                                recv_timeout or 0, requested_timeout + SERVER_RESPONSE_GRACE
                            )
                        if deadline is not None:
                            recv_timeout = self._cap_to_deadline(
                                recv_timeout or config.connection_timeout, deadline
                            )
                        self.sock.settimeout(recv_timeout)
                        t_recv_start = time.time()
                        progress_options = (
                            {"_progress": _progress, "_progress_id": association[0]}
                            if association is not None
                            else {}
                        )
                        response_data = self.receive_full_response(
                            self.sock, deadline=deadline, **progress_options
                        )
                        self._check_deadline(deadline)
                        logger.info(
                            "[TIMING-STDIO] receive took %.3fs command=%s len=%d",
                            time.time() - t_recv_start,
                            command_type,
                            len(response_data),
                        )
                        with contextlib.suppress(Exception):
                            logger.debug(f"recv {len(response_data)} bytes; mode={mode}")
                    finally:
                        self._close_stdio_progress()
                        self.sock.settimeout(restore_timeout)

                    # Parse
                    resp = _decode_unity_response(response_data)
                    if not isinstance(resp, dict):
                        raise _UnityProtocolError("Unity response must be a JSON object")
                    reply = decode_native_reply(resp, arbitration)
                    response_received = True
                    if reply.not_started:
                        raise NativeNotStarted(
                            reply,
                            transport_generation=self.session_generation,
                            params=json.loads(payload)["params"],
                        )
                    resp = reply.payload
                    if command_type == "ping":
                        response_received = True
                        if receipt is not None:
                            receipt.observe(
                                kind="stdio",
                                attempt_id=receipt_attempt,
                                session_id="stdio:" + receipt_project,
                                generation=self.session_generation,
                                dispatched=True,
                                observed=True,
                                cause="observed",
                            )
                        if (
                            resp.get("status") == "success"
                            and resp.get("result", {}).get("message") == "pong"
                        ):
                            return {"message": "pong"}
                        raise Exception("Ping unsuccessful")

                    response_received = True
                    if receipt is not None:
                        receipt.observe(
                            kind="stdio",
                            attempt_id=receipt_attempt,
                            session_id="stdio:" + receipt_project,
                            generation=self.session_generation,
                            dispatched=True,
                            observed=True,
                            cause="observed",
                        )
                    if resp.get("status") == "error":
                        err = resp.get("error") or resp.get("message", "Unknown Unity error")
                        raise Exception(err)
                    return resp.get("result", {})
                except Exception as e:
                    logger.warning(
                        "Unity communication attempt %d failed (%s)", attempt + 1, type(e).__name__
                    )
                    if response_received:
                        # Unity answered: an application error is not a reconnect signal.
                        raise
                    protocol_error = command_sent and isinstance(
                        e,
                        (
                            _UnityProtocolError,
                            NativeArbitrationProtocolError,
                            json.JSONDecodeError,
                            UnicodeDecodeError,
                        ),
                    )
                    if not isinstance(e, (OSError, ConnectionError)) and not protocol_error:
                        raise
                    self.disconnect()
                    if command_sent:
                        if blender_timeout is not None:
                            # Preserve the Blender bridge's existing exception contract.
                            raise
                        # A lost reply cannot prove whether Unity applied a mutation.
                        # Do not silently dispatch the payload a second time.
                        return MCPResponse(
                            success=False,
                            error=str(e),
                            hint="inspect_state_before_retry",
                            data={"reason": "outcome_unknown", "command": command_type},
                        )
                    last_error = e

            # Re-discover the port for this specific instance
            try:
                stdio_port_registry.get_instances(force_refresh=True)
                new_port: int | None = None
                if self.instance_id:
                    # Try to rediscover the specific instance via shared registry
                    refreshed_instance = stdio_port_registry.get_instance(self.instance_id)
                    if refreshed_instance and isinstance(refreshed_instance.port, int):
                        new_port = refreshed_instance.port
                        logger.debug(f"Rediscovered instance {self.instance_id} on port {new_port}")
                    else:
                        logger.warning(
                            f"Instance {self.instance_id} not found during reconnection; resolving through shared registry",
                        )

                # Resolve through the shared registry with the same explicit target.
                if new_port is None:
                    new_port = stdio_port_registry.get_port(self.instance_id)
                    logger.info(f"Using Unity port from stdio_port_registry: {new_port}")

                if new_port != self.port:
                    logger.info(f"Unity port changed {self.port} -> {new_port}")
                with self._command_lock(deadline):
                    if self.port != new_port:
                        self.disconnect()
                        self.port = new_port
            except TimeoutError:
                raise
            except Exception as de:
                logger.debug(f"Port discovery failed: {de}")

            if attempt < attempts:
                # Heartbeat-aware, jittered backoff
                status = read_status_file(target_hash)
                # Base exponential backoff
                backoff = base_backoff * (2**attempt)
                # Decorrelated jitter multiplier
                jitter = random.uniform(0.1, 0.3)

                # Fast‑retry for transient socket failures
                fast_error = isinstance(
                    last_error, (ConnectionRefusedError, ConnectionResetError, TimeoutError)
                )
                if not fast_error:
                    try:
                        err_no = getattr(last_error, "errno", None)
                        fast_error = err_no in (
                            errno.ECONNREFUSED,
                            errno.ECONNRESET,
                            errno.ETIMEDOUT,
                        )
                    except Exception:
                        pass

                # Cap backoff depending on state
                if status and status.get("reloading"):
                    # Domain reload can take 10-20s; use longer waits
                    cap = 5.0
                elif fast_error:
                    cap = 0.25
                else:
                    cap = 3.0

                sleep_s = min(cap, jitter * (2**attempt))
                sleep_s = self._cap_to_deadline(sleep_s, deadline, floor=0.0)
                time.sleep(sleep_s)
                continue
            raise last_error


# -----------------------------
# Connection Pool for Multiple Unity Instances
# -----------------------------


class UnityConnectionPool:
    """Manages connections to multiple Unity Editor instances"""

    MAX_CONNECTIONS = 64
    MISSING_GRACE_SECONDS = 60.0

    def __init__(self):
        self._connections: dict[str, UnityConnection] = {}
        self._known_instances: dict[str, UnityInstanceInfo] = {}
        self._last_full_scan: float | None = None
        self._target_refreshes: dict[str, float] = {}
        self._scan_interval: float = 5.0  # Cache for 5 seconds
        self._pool_lock = threading.Lock()
        self._scan_lock = threading.Lock()
        self._default_instance_id: str | None = None
        self._last_used: dict[str, float] = {}
        self._missing_since: dict[str, float] = {}
        self._retired = False
        self._resync_tasks: set[asyncio.Task] = set()
        self._resync_workers: set[asyncio.Future] = set()
        self._close_task: asyncio.Task | None = None

        # Check for default instance from environment
        env_default = os.environ.get("UNITY_MCP_DEFAULT_INSTANCE", "").strip()
        if env_default:
            self._default_instance_id = env_default
            logger.info(f"Default Unity instance set from environment: {env_default}")

    def _retire_missing_locked(self, now: float) -> None:
        """Reclaim proven-missing, unused generations while holding the pool lock."""
        for instance_id, missing_at in list(self._missing_since.items()):
            if (
                now - missing_at < self.MISSING_GRACE_SECONDS
                or now - self._last_used.get(instance_id, missing_at) < self.MISSING_GRACE_SECONDS
            ):
                continue
            connection = self._connections.get(instance_id)
            if connection is None or not connection._try_retire():
                continue
            del self._connections[instance_id]
            self._missing_since.pop(instance_id, None)
            self._last_used.pop(instance_id, None)
            self._target_refreshes.pop(instance_id, None)
            if instance_id in self._known_instances:
                self._known_instances = {
                    key: value for key, value in self._known_instances.items() if key != instance_id
                }
            logger.debug("Retired an idle Unity connection absent from discovery")

    def discover_all_instances(self, force_refresh: bool = False) -> list[UnityInstanceInfo]:
        """
        Discover all running Unity Editor instances.

        Args:
            force_refresh: If True, bypass cache and scan immediately

        Returns:
            List of UnityInstanceInfo objects
        """
        with self._scan_lock:
            now = time.time()

            # Return cached results if valid
            if (
                not force_refresh
                and self._last_full_scan is not None
                and (now - self._last_full_scan) < self._scan_interval
            ):
                logger.debug(
                    f"Returning cached Unity instances (age: {now - self._last_full_scan:.1f}s)"
                )
                return list(self._known_instances.values())

            # Scan for instances
            logger.debug("Scanning for Unity instances...")
            instances = PortDiscovery.discover_all_unity_instances()

            # Update cache
            with self._pool_lock:
                self._known_instances = {inst.id: inst for inst in instances}
                self._last_full_scan = time.time()
                self._target_refreshes.clear()
                now_monotonic = time.monotonic()
                for instance_id in self._connections:
                    if instance_id in self._known_instances:
                        self._missing_since.pop(instance_id, None)
                    else:
                        self._missing_since.setdefault(instance_id, now_monotonic)
                self._retire_missing_locked(now_monotonic)

            logger.info(
                f"Found {len(instances)} Unity instances: {[inst.id for inst in instances]}"
            )
            return instances

    @staticmethod
    def is_exact_instance_id(identifier: str | None) -> bool:
        """Only canonical full IDs can avoid global selection discovery."""
        return (
            isinstance(identifier, str)
            and re.fullmatch(r"[^@/\\]+@[0-9a-fA-F]{8}(?:[0-9a-fA-F]{8})?", identifier.strip())
            is not None
        )

    def resolve_instance(
        self, instance_identifier: str | None = None, force_refresh: bool = False
    ) -> UnityInstanceInfo:
        """Resolve metadata without connecting or mistaking a target for a full scan."""
        if config.http_remote_hosted:
            raise RuntimeError("Legacy Unity connections are disabled in remote-hosted mode")
        if self.is_exact_instance_id(instance_identifier):
            identifier = instance_identifier.strip()
            with self._scan_lock:
                now = time.time()
                if (
                    not force_refresh
                    and self._last_full_scan is not None
                    and now - self._last_full_scan < self._scan_interval
                ):
                    return self._resolve_instance_id(
                        identifier, list(self._known_instances.values())
                    )
                refreshed = self._target_refreshes.get(identifier)
                if (
                    not force_refresh
                    and refreshed is not None
                    and now - refreshed < self._scan_interval
                ):
                    return self._known_instances[identifier]
                target = PortDiscovery.discover_unity_instance(identifier)
                if target is not None:
                    with self._pool_lock:
                        # A refreshed port owner displaces stale metadata, while
                        # retaining the proven target root for editor resources.
                        displaced = [
                            key
                            for key, instance in self._known_instances.items()
                            if instance.port == target.port or key == target.id
                        ]
                        known_instances = {
                            key: instance
                            for key, instance in self._known_instances.items()
                            if key not in displaced
                        }
                        for key in displaced:
                            self._target_refreshes.pop(key, None)
                            if key != target.id and key in self._connections:
                                self._missing_since.setdefault(key, time.monotonic())
                        known_instances[target.id] = target
                        # Publish atomically for resource readers that iterate
                        # metadata without holding the pool's internal locks.
                        self._known_instances = known_instances
                        self._target_refreshes[target.id] = time.time()
                        self._missing_since.pop(target.id, None)
                        # A partial refresh cannot certify a global inventory.
                        self._last_full_scan = None
                    return target
        # Ambiguous, noncanonical, missing and displaced selectors keep the
        # existing global discovery and helpful selection errors.
        return self._resolve_instance_id(
            instance_identifier, self.discover_all_instances(force_refresh=force_refresh)
        )

    def _resolve_instance_id(
        self, instance_identifier: str | None, instances: list[UnityInstanceInfo]
    ) -> UnityInstanceInfo:
        """
        Resolve an instance identifier to a specific Unity instance.

        Args:
            instance_identifier: User-provided identifier (name, hash, name@hash, path, port, or None)
            instances: List of available instances

        Returns:
            Resolved UnityInstanceInfo

        Raises:
            ConnectionError: If instance cannot be resolved
        """
        if not instances:
            raise ConnectionError(
                "No Unity Editor instances found. Please ensure Unity is running with MCP for Unity bridge."
            )

        # Use default instance if no identifier provided
        if instance_identifier is None:
            if self._default_instance_id:
                instance_identifier = self._default_instance_id
                logger.debug(f"Using default instance: {instance_identifier}")
            elif len(instances) == 1:
                # Sole instance: unambiguous, select it without requiring a hint.
                return instances[0]
            else:
                # 2+ instances connected and nothing pinned. Refuse to guess —
                # silently routing to the most-recently-heartbeated editor lets
                # an unbound session retarget another project's Unity (#1023).
                # Mirror the HTTP "multiple connected, no active set" guard.
                available_ids = [inst.id for inst in instances]
                raise ConnectionError(
                    "Multiple Unity instances are connected and none is selected. "
                    "Pass unity_instance on the call or use set_active_instance "
                    f"with one of: {available_ids}. "
                    "Read mcpforunity://instances for current sessions."
                )

        identifier = instance_identifier.strip()

        # Try exact ID match first
        for inst in instances:
            if inst.id == identifier:
                return inst

        # Try project name match
        name_matches = [inst for inst in instances if inst.name == identifier]
        if len(name_matches) == 1:
            return name_matches[0]
        elif len(name_matches) > 1:
            # Multiple projects with same name - return helpful error
            suggestions = [
                {
                    "id": inst.id,
                    "path": inst.path,
                    "port": inst.port,
                    "suggest": f"Use unity_instance='{inst.id}'",
                }
                for inst in name_matches
            ]
            raise ConnectionError(
                f"Project name '{identifier}' matches {len(name_matches)} instances. "
                f"Please use the full format (e.g., '{name_matches[0].id}'). "
                f"Available instances: {suggestions}"
            )

        # Try hash match
        hash_matches = [
            inst
            for inst in instances
            if inst.hash == identifier or inst.hash.startswith(identifier)
        ]
        if len(hash_matches) == 1:
            return hash_matches[0]
        elif len(hash_matches) > 1:
            raise ConnectionError(
                f"Hash '{identifier}' matches multiple instances: {[inst.id for inst in hash_matches]}"
            )

        # Try composite format: Name@Hash or Name@Port
        if "@" in identifier:
            name_part, hint_part = identifier.split("@", 1)
            composite_matches = [
                inst
                for inst in instances
                if inst.name == name_part
                and (inst.hash.startswith(hint_part) or str(inst.port) == hint_part)
            ]
            if len(composite_matches) == 1:
                return composite_matches[0]

        # Try port match (as string)
        try:
            port_num = int(identifier)
            port_matches = [inst for inst in instances if inst.port == port_num]
            if len(port_matches) == 1:
                return port_matches[0]
        except ValueError:
            pass

        # Try path match
        path_matches = [inst for inst in instances if inst.path == identifier]
        if len(path_matches) == 1:
            return path_matches[0]

        # Nothing matched
        available_ids = [inst.id for inst in instances]
        raise ConnectionError(
            f"Unity instance '{identifier}' not found. "
            f"Available instances: {available_ids}. "
            f"Check mcpforunity://instances resource for all instances."
        )

    def get_connection(self, instance_identifier: str | None = None) -> UnityConnection:
        """
        Get or create a connection to a Unity instance.

        Args:
            instance_identifier: Optional identifier (name, hash, name@hash, etc.)
                                If None, uses default or most recent instance

        Returns:
            UnityConnection to the specified instance

        Raises:
            ConnectionError: If instance cannot be found or connected
        """
        if self._retired:
            raise ConnectionError("Unity connection pool is retired")
        target = self.resolve_instance(instance_identifier)

        # An exact-ID lookup may have refreshed only one target for a long time.
        # At capacity, obtain a complete inventory before deciding admission.
        with self._pool_lock:
            needs_scan = (
                target.id not in self._connections
                and len(self._connections) >= self.MAX_CONNECTIONS
            )
        if needs_scan:
            self.discover_all_instances(force_refresh=True)

        # Return existing connection or create new one
        with self._pool_lock:
            if self._retired:
                raise ConnectionError("Unity connection pool is retired")
            now = time.monotonic()
            self._missing_since.pop(target.id, None)
            self._retire_missing_locked(now)
            if target.id not in self._connections:
                if len(self._connections) >= self.MAX_CONNECTIONS:
                    raise ConnectionError(
                        "Unity connection pool capacity reached; close unused editors and "
                        "retry after discovery's idle grace period"
                    )
                logger.info(
                    f"Creating new connection to Unity instance: {target.id} (port {target.port})"
                )
                conn = UnityConnection(port=target.port, instance_id=target.id)
                if not conn.connect():
                    raise ConnectionError(
                        f"Failed to connect to Unity instance '{target.id}' on port {target.port}. "
                        f"Ensure the Unity Editor is running."
                    )
                if self._retired:
                    # Retirement may race the blocking initial connection.
                    conn._retired = True
                    conn.disconnect()
                    raise ConnectionError("Unity connection pool is retired")
                self._connections[target.id] = conn
            else:
                # Update existing connection with instance_id and port if changed
                conn = self._connections[target.id]
                conn.instance_id = target.id
                if conn.port != target.port:
                    logger.info(
                        f"Updating cached port for {target.id}: {conn.port} -> {target.port}"
                    )
                    with conn._io_lock:
                        conn.disconnect()
                        conn.port = target.port
                logger.debug(f"Reusing existing connection to: {target.id}")

            self._last_used[target.id] = now
            self._connections[target.id]._pool_owner = weakref.ref(self)
            return self._connections[target.id]

    def schedule_tool_resync(self, connection: UnityConnection) -> bool:
        """Own reconnect metadata in the selected pool's event-loop lifetime."""
        import asyncio  # noqa: ANYIO_OK -- transport task ownership uses the existing loop.

        if self._retired or self._connections.get(connection.instance_id) is not connection:
            return False
        context = Context()
        context.run(_resync_pool.set, self)
        task = asyncio.create_task(
            _resync_tools_after_reconnect(connection.instance_id), context=context
        )
        self._resync_tasks.add(task)

        def completed(future):
            self._resync_tasks.discard(future)
            _consume_async_exception(future)

        task.add_done_callback(completed)
        return True

    async def aclose(self) -> None:
        """Retire resync admission and drain actual work before disposing sockets."""
        import asyncio  # noqa: ANYIO_OK -- retire the existing asyncio transport generation.
        from services.reference_graph.reconcile import drained_task

        self._retired = True
        for connection in tuple(self._connections.values()):
            # Seal reconnect/retry without taking the I/O lock or disposing a
            # running command's socket. Actual disposal follows the drain.
            connection._retired = True
        if self._close_task is None:
            self._close_task = asyncio.create_task(self._close_generation(), context=Context())
        await drained_task(self._close_task)

    async def _close_generation(self) -> None:
        """Keep one cleanup owner even when close waiters cancel or overlap."""
        import asyncio  # noqa: ANYIO_OK -- drain existing transport tasks and executor futures.

        global _unity_connection_pool
        tasks = tuple(self._resync_tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        # A cancelled task may have left shielded executor I/O running. The
        # future remains owned until actual completion, including queued work.
        await asyncio.gather(
            *(asyncio.shield(worker) for worker in tuple(self._resync_workers)),
            return_exceptions=True,
        )
        await asyncio.to_thread(self.disconnect_all)
        with _pool_init_lock:
            if _unity_connection_pool is self:
                _unity_connection_pool = None

    def disconnect_all(self):
        """Disconnect all active connections and release this pool's discovery generation."""
        # Discovery publishes under scan -> pool locks. Teardown uses the same
        # order so a scan already in progress cannot repopulate closed metadata.
        with self._scan_lock, self._pool_lock:
            for instance_id, conn in self._connections.items():
                try:
                    logger.info(f"Disconnecting from Unity instance: {instance_id}")
                    if self._retired:
                        # A selector can publish after aclose's initial snapshot.
                        # Seal under the publication lock before final disposal.
                        conn._retired = True
                    conn.disconnect()
                except Exception:
                    logger.exception(f"Error disconnecting from {instance_id}")
            self._connections.clear()
            self._last_used.clear()
            self._missing_since.clear()
            # Replace metadata rather than mutating an existing resource reader's snapshot.
            self._known_instances = {}
            self._target_refreshes.clear()
            self._last_full_scan = None


# Global Unity connection pool
_unity_connection_pool: UnityConnectionPool | None = None
_pool_init_lock = threading.Lock()


def get_unity_connection_pool() -> UnityConnectionPool:
    """Get or create the global Unity connection pool"""
    if config.http_remote_hosted:
        raise RuntimeError("Legacy Unity connections are disabled in remote-hosted mode")
    global _unity_connection_pool

    if _unity_connection_pool is not None:
        return _unity_connection_pool

    with _pool_init_lock:
        if _unity_connection_pool is not None:
            return _unity_connection_pool

        logger.info("Initializing Unity connection pool")
        _unity_connection_pool = UnityConnectionPool()
        return _unity_connection_pool


# Backwards compatibility: keep old single-connection function
def get_unity_connection(instance_identifier: str | None = None) -> UnityConnection:
    """Retrieve or establish a Unity connection.

    Args:
        instance_identifier: Optional identifier for specific Unity instance.
                           If None, uses default or most recent instance.

    Returns:
        UnityConnection to the specified or default Unity instance

    Note: This function now uses the connection pool internally.
    """
    pool = get_unity_connection_pool()
    return pool.get_connection(instance_identifier)


def _commit_stdio_catalog(connection, generation, publish):
    """Publish only while the cached authenticated socket still owns its source.

    Never wait on blocking transport locks from the server loop. The caller can
    retry a busy refresh; ordinary commands retain their existing lock policy.
    """
    pool = _unity_connection_pool
    if pool is None or not pool._pool_lock.acquire(blocking=False):
        raise ValueError("Unity catalog connection ownership unavailable")
    try:
        if pool._retired or pool._connections.get(connection.instance_id) is not connection:
            raise ValueError("Unity catalog connection ownership changed")
        if not connection._io_lock.acquire(blocking=False):
            raise ValueError("Unity catalog connection is busy")
        try:
            if (
                not generation
                or connection.sock is None
                or connection.session_generation != generation
            ):
                raise ValueError("Unity catalog generation changed before publication")
            return publish()
        finally:
            connection._io_lock.release()
    finally:
        pool._pool_lock.release()


async def get_authenticated_stdio_generation(instance_id: str | None) -> str | None:
    """Verify the selected socket before permitting an authenticated cache hit.

    Inspect only an already-selected connection using registered metadata. No
    discovery, connection, ping or command is sent. A clean FIN discards the old generation; the
    next RPC establishes a new authenticated session. Half-open connections are
    still bounded by command deadlines and resource freshness (at most 1s).
    """
    import asyncio

    if not instance_id or config.http_remote_hosted:
        return None
    pool = _unity_connection_pool
    if pool is None:
        return None
    loop = asyncio.get_running_loop()

    def inspect() -> str | None:
        if not pool._pool_lock.acquire(timeout=0.1):
            return None
        try:
            try:
                target = pool._resolve_instance_id(
                    instance_id, list(pool._known_instances.values())
                )
            except ConnectionError:
                return None
            conn = pool._connections.get(target.id)
        finally:
            pool._pool_lock.release()
        if conn is None:
            return None
        if not conn._io_lock.acquire(timeout=0.1):
            return None  # A running RPC owns this socket; no cache hit is safe.
        try:
            conn._ensure_live_connection()
            return conn.session_generation if conn.sock else None
        finally:
            conn._io_lock.release()

    future = submit_owned_executor(loop, None, inspect)
    future.add_done_callback(_consume_async_exception)
    try:
        async with asyncio.timeout(1.0):
            return await asyncio.shield(future)
    except (OSError, RuntimeError, TimeoutError):
        return None


async def get_stdio_catalog_generation(instance_id: str | None) -> str | None:
    """Retain availability during active work without authorizing cached reads.

    An RPC may own the socket throughout a long Unity operation. The stricter
    cache probe cannot inspect that socket, but its authenticated catalog still
    caps discovery. Only the exact retained connection/session may supply this
    fallback; an idle FIN, retirement or replacement invalidates it.
    """
    if not instance_id or config.http_remote_hosted:
        return None
    pool = _unity_connection_pool
    connection = pool._connections.get(instance_id) if pool is not None else None
    session = connection.authenticated_session if connection is not None else None
    sock = connection.sock if connection is not None else None
    generation = await get_authenticated_stdio_generation(instance_id)
    if generation is not None:
        return generation
    if (
        session is not None
        and sock is not None
        and pool is _unity_connection_pool
        and pool._connections.get(instance_id) is connection
        and instance_id in pool._known_instances
        and not connection._retired
        and connection.authenticated_session is session
        and connection.sock is sock
        and connection.session_generation == session.session_generation
    ):
        return session.session_generation
    return None


# -----------------------------
# Centralized retry helpers
# -----------------------------


def _extract_response_reason(resp: object) -> str | None:
    """Extract a normalized (lowercase) reason string from a response.

    Returns lowercase reason values to enable case-insensitive comparisons
    by callers (e.g. _is_reloading_response, refresh_unity).
    """
    if isinstance(resp, MCPResponse):
        data = getattr(resp, "data", None)
        if isinstance(data, dict):
            reason = data.get("reason")
            if isinstance(reason, str):
                return reason.lower()
        message_text = f"{resp.message or ''} {resp.error or ''}".lower()
        if "reload" in message_text:
            return "reloading"
        return None

    if isinstance(resp, dict):
        if resp.get("state") == "reloading":
            return "reloading"
        data = resp.get("data")
        if isinstance(data, dict):
            reason = data.get("reason")
            if isinstance(reason, str):
                return reason.lower()
        message_text = (resp.get("message") or resp.get("error") or "").lower()
        if "reload" in message_text:
            return "reloading"
        return None

    return None


def _is_reloading_response(resp: object, *, require_preflight: bool = False) -> bool:
    """Return True if the Unity response indicates the editor is reloading.

    Supports both raw dict payloads from Unity and MCPResponse objects returned
    by preflight checks or transport helpers.
    """
    if require_preflight:
        # Only the local preflight returns this model; on-wire tool results are
        # dictionaries and can mention reload after already changing Blender.
        return (
            isinstance(resp, MCPResponse)
            and isinstance(resp.data, dict)
            and resp.data.get("reason") == "reloading"
        )
    return _extract_response_reason(resp) == "reloading"


def send_command_with_retry(
    command_type: str,
    params: dict[str, Any],
    *,
    instance_id: str | None = None,
    max_retries: int | None = None,
    retry_ms: int | None = None,
    retry_on_reload: bool = True,
    _progress: ProgressSink | None = None,
) -> dict[str, Any] | MCPResponse:
    """Send a command to a Unity instance, waiting politely through Unity reloads.

    Args:
        command_type: The command type to send
        params: Command parameters
        instance_id: Optional Unity instance identifier (name, hash, name@hash, etc.)
        max_retries: Maximum number of retries for reload states
        retry_ms: Delay between retries in milliseconds
        retry_on_reload: If False, don't retry when Unity is reloading (for commands
            that trigger compilation/reload and shouldn't be re-sent)

    Returns:
        Response dictionary or MCPResponse from Unity

    Uses config.reload_retry_ms and config.reload_max_retries by default. Preserves the
    structured failure if retries are exhausted.
    """
    deadline = _command_deadline(command_type, params)
    connection = None
    expected_generation = None
    try:
        while True:
            try:
                response, _ = _send_command_with_retry(
                    command_type,
                    params,
                    instance_id=instance_id,
                    max_retries=max_retries,
                    retry_ms=retry_ms,
                    retry_on_reload=retry_on_reload,
                    _deadline=deadline,
                    **({"_connection": connection} if connection is not None else {}),
                    **(
                        {"_expected_generation": expected_generation}
                        if expected_generation is not None
                        else {}
                    ),
                    **({"_progress": _progress} if _progress is not None else {}),
                )
                return response
            except NativeNotStarted as refusal:
                if not refusal.reply.retryable or command_type == "batch_execute":
                    return refusal.reply.payload
                connection = get_unity_connection(instance_id) if connection is None else connection
                expected_generation = refusal.transport_generation
                params = refusal.params
                if deadline is not None and time.monotonic() >= deadline:
                    raise TimeoutError("Unity command deadline expired before dispatch") from None
                # send_command has released the exact transaction lock.
                time.sleep(
                    min(0.1, max(0.0, deadline - time.monotonic())) if deadline is not None else 0.1
                )
    finally:
        if _progress is not None:
            with contextlib.suppress(Exception):
                _progress.close()


def _command_deadline(command_type: str, params: dict[str, Any]) -> float | None:
    """Use the existing command/Blender budget across selection and admission."""
    total_timeout = max(0.0, float(getattr(config, "command_total_timeout", 90.0)))
    if command_type == "blender_bridge":
        total_timeout = blender_command_timeout(params) + SERVER_RESPONSE_GRACE
    elif (requested := requested_command_timeout(params)) is not None:
        total_timeout = max(total_timeout, requested + SERVER_RESPONSE_GRACE)
    deadline = time.monotonic() + total_timeout if total_timeout > 0 else None
    from services.registry.operation_context import active_operation

    owner = active_operation()
    if owner is not None and owner.request.expires_at is not None:
        deadline = (
            min(deadline, owner.request.expires_at)
            if deadline is not None
            else owner.request.expires_at
        )
    return deadline


def _send_command_with_retry(
    command_type: str,
    params: dict[str, Any],
    *,
    instance_id: str | None = None,
    max_retries: int | None = None,
    retry_ms: int | None = None,
    retry_on_reload: bool = True,
    _connection: UnityConnection | None = None,
    _deadline: float | None = None,
    _expected_generation: str | None = None,
    _catalog_guard=None,
    _progress: ProgressSink | None = None,
) -> tuple[dict[str, Any] | MCPResponse, UnityConnection]:
    """Retain dispatch provenance for async completion without another lookup.

    The public sync helper returns only the response and leaves reconnect work
    pending. Async callers receive the exact connection used by this command.
    """
    t_retry_start = time.time()
    logger.info("[TIMING-STDIO] send_command_with_retry START command=%s", command_type)
    t_get_conn = time.time()
    conn = _connection if _connection is not None else get_unity_connection(instance_id)
    logger.info(
        "[TIMING-STDIO] get_unity_connection took %.3fs command=%s",
        time.time() - t_get_conn,
        command_type,
    )
    if max_retries is None:
        max_retries = getattr(config, "reload_max_retries", None)
    if retry_ms is None:
        retry_ms = getattr(config, "reload_retry_ms", 250)
    # Only explicit pre-dispatch reload refusals are replayed. A slow compilation
    # can consume this configurable window, still bounded by the parent command.
    max_wait_s = configured_wait_timeout("UNITY_MCP_RELOAD_MAX_WAIT_S", 300.0)

    deadline = _deadline if _deadline is not None else _command_deadline(command_type, params)

    # If retry_on_reload=False, disable connection-level retries too (issue #577)
    # Commands that trigger compilation/reload shouldn't retry on disconnect
    if _catalog_guard is not None or _expected_generation is not None:
        retry_on_reload = False
    send_max_attempts = None if retry_on_reload else 0

    response = conn.send_command(
        command_type,
        params,
        max_attempts=send_max_attempts,
        deadline=deadline,
        **(
            {"_expected_generation": _expected_generation}
            if _expected_generation is not None
            else {}
        ),
        **({"_catalog_guard": _catalog_guard} if _catalog_guard is not None else {}),
        **({"_progress": _progress} if _progress is not None else {}),
    )
    retries = 0
    wait_started = None
    reason = _extract_response_reason(response)
    require_preflight = command_type == "blender_bridge"
    while (
        retry_on_reload
        and _is_reloading_response(response, require_preflight=require_preflight)
        and (max_retries is None or retries < max_retries)
        and (deadline is None or time.monotonic() < deadline)
    ):
        if wait_started is None:
            wait_started = time.monotonic()
            logger.debug(
                "Unity reload wait started: command=%s instance=%s reason=%s max_wait_s=%.2f",
                command_type,
                instance_id or "default",
                reason or "reloading",
                max_wait_s,
            )
        if max_wait_s <= 0:
            break
        elapsed = time.monotonic() - wait_started
        if elapsed >= max_wait_s:
            break
        delay_ms = retry_ms
        if isinstance(response, dict):
            retry_after = response.get("retry_after_ms")
            if retry_after is None and isinstance(response.get("data"), dict):
                retry_after = response["data"].get("retry_after_ms")
            if retry_after is not None:
                delay_ms = int(retry_after)
        sleep_ms = max(50, min(int(delay_ms), 250))
        logger.debug(
            "Unity reload wait retry: command=%s instance=%s reason=%s retry_after_ms=%s sleep_ms=%s",
            command_type,
            instance_id or "default",
            reason or "reloading",
            delay_ms,
            sleep_ms,
        )
        sleep_s = min(sleep_ms / 1000.0, max_wait_s - elapsed)
        if deadline is not None:
            sleep_s = min(sleep_s, max(0.0, deadline - time.monotonic()))
        if sleep_s <= 0:
            break
        time.sleep(sleep_s)
        now = time.monotonic()
        if (deadline is not None and now >= deadline) or now - wait_started >= max_wait_s:
            break
        retries += 1
        response = conn.send_command(
            command_type,
            params,
            deadline=deadline,
            **({"_progress": _progress} if _progress is not None else {}),
        )
        reason = _extract_response_reason(response)

    if wait_started is not None:
        waited = time.monotonic() - wait_started
        if _is_reloading_response(response, require_preflight=require_preflight):
            logger.debug(
                "Unity reload wait exceeded budget: command=%s instance=%s waited_s=%.3f",
                command_type,
                instance_id or "default",
                waited,
            )
            return MCPResponse(
                success=False,
                error="Unity is reloading; please retry",
                hint="retry",
                data={
                    "reason": "reloading",
                    "retry_after_ms": min(250, max(50, retry_ms)),
                },
            ), conn
        logger.debug(
            "Unity reload wait completed: command=%s instance=%s waited_s=%.3f",
            command_type,
            instance_id or "default",
            waited,
        )
    logger.info(
        "[TIMING-STDIO] send_command_with_retry DONE total=%.3fs command=%s",
        time.time() - t_retry_start,
        command_type,
    )
    return response, conn


class _AsyncDispatchState:
    """Atomically abort an executor-queued request without stopping running I/O."""

    def __init__(self):
        self._lock = threading.Lock()
        self._started = False
        self._aborted = False

    def start(self) -> bool:
        """Claim dispatch only if cancellation/deadline did not win the race."""
        with self._lock:
            if self._aborted:
                return False
            self._started = True
            return True

    def abort_before_start(self) -> bool:
        """Return whether no worker has begun this command."""
        with self._lock:
            if self._started:
                return False
            self._aborted = True
            return True


def _consume_async_exception(future: "asyncio.Future") -> None:
    """Retrieve worker failures even after their awaiting caller is cancelled."""
    if not future.cancelled():
        future.exception()


async def async_send_command_with_retry(
    command_type: str,
    params: dict[str, Any],
    *,
    instance_id: str | None = None,
    loop=None,
    max_retries: int | None = None,
    retry_ms: int | None = None,
    retry_on_reload: bool = True,
    _deadline: float | None = None,
    _expected_generation: str | None = None,
    _catalog_guard=None,
    _catalog_capture=None,
    _progress: ProgressSink | None = None,
) -> dict[str, Any] | MCPResponse:
    """Preserve existing dispatch behavior and observe canonical uncertainty."""
    result = await _async_send_command_response(
        command_type,
        params,
        instance_id=instance_id,
        loop=loop,
        max_retries=max_retries,
        retry_ms=retry_ms,
        retry_on_reload=retry_on_reload,
        _deadline=_deadline,
        _expected_generation=_expected_generation,
        _catalog_guard=_catalog_guard,
        _catalog_capture=_catalog_capture,
        **({"_progress": _progress} if _progress is not None else {}),
    )
    observe_native_result({"data": result.data} if isinstance(result, MCPResponse) else result)
    return result


async def _async_send_command_response(
    command_type: str,
    params: dict[str, Any],
    *,
    instance_id: str | None = None,
    loop=None,
    max_retries: int | None = None,
    retry_ms: int | None = None,
    retry_on_reload: bool = True,
    _deadline: float | None = None,
    _expected_generation: str | None = None,
    _catalog_guard=None,
    _catalog_capture=None,
    _progress: ProgressSink | None = None,
) -> dict[str, Any] | MCPResponse:
    """Own one selected instance while exact pre-handler refusals wait off-wire."""
    import asyncio  # noqa: ANYIO_OK -- existing connection admission uses asyncio.
    from services.operation_policy import resolve_operation_policy
    from services.registry.operation_invocation import (
        admission_for,
        operation_session,
        is_observational_continuation,
    )
    from services.registry.operation_target import capture_stdio_target

    if config.http_remote_hosted:
        return MCPResponse(
            success=False, error="Legacy Unity connections are disabled in remote-hosted mode"
        )
    policy_deadline = _command_deadline(command_type, params)
    deadline = (
        min(policy_deadline, _deadline)
        if policy_deadline is not None and _deadline is not None
        else _deadline
        if _deadline is not None
        else policy_deadline
    )
    try:
        owner_pool = _resync_pool.get()
        if owner_pool is not None and owner_pool._retired:
            raise asyncio.CancelledError
        selection = submit_owned_executor(
            asyncio.get_running_loop(),
            None,
            owner_pool.get_connection if owner_pool is not None else get_unity_connection,
            instance_id,
        )
        if owner_pool is not None:
            owner_pool._resync_workers.add(selection)
            selection.add_done_callback(owner_pool._resync_workers.discard)
        selection.add_done_callback(_consume_async_exception)
        async with asyncio.timeout_at(deadline):
            connection = await asyncio.shield(selection)
        target = capture_stdio_target(connection)
        policy = resolve_operation_policy(
            command_type, params, native=True, trusted_builtin=_catalog_guard is None
        )
        async with operation_session(
            admission_for(
                target,
                policy.mode,
                expires_at=deadline,
                observational_continuation=is_observational_continuation(command_type, params),
            )
        ):
            while True:
                try:
                    return await _async_send_command_attempt(
                        command_type,
                        params,
                        instance_id=instance_id,
                        loop=loop,
                        max_retries=max_retries,
                        retry_ms=retry_ms,
                        retry_on_reload=retry_on_reload,
                        _deadline=deadline,
                        _expected_generation=_expected_generation,
                        _catalog_guard=_catalog_guard,
                        _catalog_capture=_catalog_capture,
                        _progress=_progress,
                        _connection=connection,
                    )
                except NativeNotStarted as refusal:
                    if not refusal.reply.retryable or command_type == "batch_execute":
                        return refusal.reply.payload
                    params = refusal.params
                    _expected_generation = refusal.transport_generation
                    # The completed callback releases the connection gate before
                    # this wait. A status/cancel caller can now own the sole wire.
                    async with asyncio.timeout_at(deadline):
                        await asyncio.sleep(0.1)
    except TimeoutError:
        return MCPResponse(
            success=False,
            error="Unity command deadline expired before dispatch",
            data={"reason": "command_deadline_exceeded", "dispatch": "not_dispatched"},
        )
    except Exception as error:
        return MCPResponse(success=False, error=str(error))


async def _async_send_command_attempt(
    command_type: str,
    params: dict[str, Any],
    *,
    instance_id: str | None = None,
    loop=None,
    max_retries: int | None = None,
    retry_ms: int | None = None,
    retry_on_reload: bool = True,
    _deadline: float | None = None,
    _expected_generation: str | None = None,
    _catalog_guard=None,
    _catalog_capture=None,
    _progress: ProgressSink | None = None,
    _connection: UnityConnection | None = None,
) -> dict[str, Any] | MCPResponse:
    """Admit one blocking command worker per actual connection and event loop.

    Selection and admission share the command deadline. Cancellation before
    worker dispatch aborts the queued command; running I/O keeps its admission
    until completion and retains the existing unknown-outcome/retry contract.

    Args:
        command_type: The command type to send
        params: Command parameters
        instance_id: Optional Unity instance identifier
        loop: Optional asyncio event loop
        max_retries: Maximum number of retries for reload states
        retry_ms: Delay between retries in milliseconds
        retry_on_reload: If False, don't retry when Unity is reloading
        _deadline: Optional absolute monotonic caller ceiling; may only shorten the policy

    Returns:
        Response dictionary or MCPResponse on error
    """
    try:
        import asyncio  # local import to avoid mandatory asyncio dependency for sync callers

        if loop is None:
            loop = asyncio.get_running_loop()
        policy_deadline = _command_deadline(command_type, params)
        receipt = execution_owner.get()
        if receipt is not None:
            _deadline = (
                min(_deadline, receipt.expires_at) if _deadline is not None else receipt.expires_at
            )
        # A bounded read may shorten the existing policy, never restart or extend it.
        deadline = (
            min(policy_deadline, _deadline)
            if policy_deadline is not None and _deadline is not None
            else _deadline
            if _deadline is not None
            else policy_deadline
        )
        resync_pool = _resync_pool.get()
        if resync_pool is not None and resync_pool._retired:
            raise asyncio.CancelledError

        def submit(function, *args):
            future = submit_owned_executor(loop, None, function, *args)
            if resync_pool is not None:
                resync_pool._resync_workers.add(future)
                future.add_done_callback(resync_pool._resync_workers.discard)
            return future

        if _connection is not None:
            conn = _connection
        else:
            selection = submit(
                resync_pool.get_connection if resync_pool is not None else get_unity_connection,
                instance_id,
            )
            selection.add_done_callback(_consume_async_exception)
            try:
                async with asyncio.timeout_at(deadline):
                    conn = await asyncio.shield(selection)
            except TimeoutError as exc:
                raise TimeoutError(
                    "Unity command exceeded total deadline during connection selection"
                ) from exc

        if _catalog_guard is not None:
            _catalog_guard.select_stdio(conn)
        gate = conn._async_admission(loop)
        if deadline is None:
            await gate.acquire()
        else:
            try:
                async with asyncio.timeout_at(deadline):
                    await gate.acquire()
            except TimeoutError as exc:
                raise TimeoutError(
                    "Unity command exceeded total deadline waiting for connection"
                ) from exc

        state = _AsyncDispatchState()

        def dispatch() -> tuple[dict[str, Any] | MCPResponse, UnityConnection] | None:
            if not state.start():
                # Only a caller already exiting on cancellation/deadline sets
                # aborted. No public await consumes this private sentinel.
                return None
            conn._check_deadline(deadline)

            def send():
                return _send_command_with_retry(
                    command_type,
                    params,
                    instance_id=conn.instance_id,
                    max_retries=max_retries,
                    retry_ms=retry_ms,
                    retry_on_reload=retry_on_reload,
                    _connection=conn,
                    _deadline=deadline,
                    **(
                        {"_expected_generation": _expected_generation}
                        if _expected_generation is not None
                        else {}
                    ),
                    **({"_catalog_guard": _catalog_guard} if _catalog_guard is not None else {}),
                    **({"_progress": _progress} if _progress is not None else {}),
                )

            if _catalog_capture is None:
                return send()
            # Only catalog discovery requests capture provenance. The lock is
            # reentrant; ordinary readers keep their existing lifetime.
            with conn._command_lock(deadline):
                result = send()
                _catalog_capture.append((conn, conn.session_generation))
                return result

        try:
            completion = submit(dispatch)
        except Exception:
            gate.release()
            raise

        def completed(future):
            # A cancelled await never releases a still-running worker's gate.
            gate.release()
            _consume_async_exception(future)

        completion.add_done_callback(completed)
        try:
            if deadline is None:
                result, conn = await asyncio.shield(completion)
            else:
                try:
                    async with asyncio.timeout_at(deadline):
                        result, conn = await asyncio.shield(completion)
                except TimeoutError as exc:
                    if state.abort_before_start():
                        raise TimeoutError(
                            "Unity command exceeded total deadline waiting for command worker"
                        ) from exc
                    # Running transport owns deadline classification, including
                    # a lost response's outcome_unknown. Do not replace it.
                    result, conn = await asyncio.shield(completion)
        except asyncio.CancelledError:
            state.abort_before_start()
            raise

        # After a successful command, check if the connection was freshly
        # established (reconnection after domain reload).  If so, re-sync
        # tool visibility and custom tool registration from Unity.
        # Always clear the flag, but only schedule the background resync
        # when this call is not itself get_tool_states (to avoid recursion).
        try:
            if conn.claim_tool_resync():
                if command_type != "get_tool_states":
                    logger.info("Detected reconnection to Unity; scheduling tool re-sync")
                    owner = conn._pool_owner() if conn._pool_owner is not None else None
                    if owner is not None:
                        owner.schedule_tool_resync(conn)
        except Exception as exc:
            logger.debug(
                "Failed to schedule post-reconnection tool re-sync: %s",
                exc,
            )

        return result
    except NativeNotStarted:
        raise
    except Exception as e:
        return MCPResponse(success=False, error=str(e))
    finally:
        if _progress is not None:
            with contextlib.suppress(Exception):
                _progress.close()


async def _resync_tools_after_reconnect(instance_id: str | None) -> None:
    """Background task: re-sync tool visibility and custom tools after reconnection."""
    try:
        from services.tools import sync_tool_visibility_from_unity

        result = await sync_tool_visibility_from_unity(
            instance_id=instance_id,
            notify=True,
        )
        if result.get("synced"):
            logger.info(
                "Post-reconnection tool re-sync complete: "
                "enabled=[%s], disabled=[%s], custom_tools=%d",
                ", ".join(result.get("enabled_groups", [])),
                ", ".join(result.get("disabled_groups", [])),
                result.get("custom_tool_count", 0),
            )
        else:
            logger.debug(
                "Post-reconnection tool re-sync skipped: %s",
                result.get("error", "unknown"),
            )
    except Exception as exc:
        logger.debug("Post-reconnection tool re-sync failed: %s", exc)
