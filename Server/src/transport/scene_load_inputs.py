"""Optional, bounded missing-scene input before the ordinary authorized load."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict, dataclass
from hashlib import sha256
from importlib.metadata import version
import inspect
import logging
import os
import secrets
from threading import Lock
import time
from typing import TYPE_CHECKING

from fastmcp.server.middleware import CallNext, Middleware, MiddlewareContext
from fastmcp.tools.base import ToolResult
from mcp.shared.exceptions import MCPError
from mcp_types import INVALID_PARAMS

from core.config import config
from models.response_limits import MAX_RESPONSE_BYTES, response_size
from transport.local_auth_middleware import AUTHENTICATED_LOCAL_STATE
from transport.remote_auth_middleware import AUTHENTICATED_USER_STATE

if TYPE_CHECKING:
    from fastmcp import Context
    from fastmcp.server.dependencies import FastMCPRequestContext
    from mcp.server.context import ServerRequestContext

logger = logging.getLogger(__name__)
TTL_SECONDS = 120
STATE_PREFIX = "scene-load:v1:"


def _invalid() -> MCPError:
    return MCPError(INVALID_PARAMS, "Invalid or expired scene input")


@dataclass(frozen=True, slots=True)
class SceneScope:
    """Logical selection identity; unrelated read/command epochs are excluded."""

    owner: str
    instance: str
    project_hash: str
    session_id: str
    socket_id: int
    generation: str


@dataclass(slots=True)
class _Record:
    scope: SceneScope
    expires_at: float
    attempted: bool = False
    active: bool = False


class InputCapacityError(Exception):
    """The bounded ledger cannot admit another input question."""


class InputLedger:
    """Atomic one-attempt admission; active charges outlive expiry/shutdown."""

    def __init__(
        self, *, clock: Callable[[], float] = time.monotonic, total: int = 128, principal: int = 16
    ) -> None:
        self._clock = clock
        self._total = total
        self._principal = principal
        self._lock = Lock()
        self._records: dict[str, _Record] = {}
        self._closed = False

    def _prune(self) -> None:
        now = self._clock()
        for nonce, record in tuple(self._records.items()):
            if not record.active and (self._closed or record.expires_at <= now):
                del self._records[nonce]

    def issue(self, scope: SceneScope) -> str:
        if response_size(asdict(scope), max_bytes=4096, max_retained=16_384) is None:
            raise InputCapacityError()
        with self._lock:
            self._prune()
            count = sum(record.scope.owner == scope.owner for record in self._records.values())
            if self._closed or len(self._records) >= self._total or count >= self._principal:
                raise InputCapacityError()
            nonce = secrets.token_hex(16)
            self._records[nonce] = _Record(scope, self._clock() + TTL_SECONDS)
            return nonce

    def _lookup(self, nonce: str, scope: SceneScope) -> _Record:
        self._prune()
        record = self._records.get(nonce)
        if (
            self._closed
            or record is None
            or record.scope != scope
            or record.expires_at <= self._clock()
        ):
            raise _invalid()
        return record

    def lookup(self, nonce: str, scope: SceneScope) -> bool:
        """Return attempt status without extending the original input deadline."""
        with self._lock:
            return self._lookup(nonce, scope).attempted

    def claim(self, nonce: str, scope: SceneScope) -> bool:
        with self._lock:
            record = self._lookup(nonce, scope)
            if record.attempted:
                return False
            record.attempted = record.active = True
            return True

    def finish(self, nonce: str) -> None:
        with self._lock:
            record = self._records.get(nonce)
            if record is not None:
                record.active = False
            self._prune()

    def close(self) -> None:
        with self._lock:
            self._closed = True
            self._prune()

    @property
    def record_count(self) -> int:
        with self._lock:
            self._prune()
            return len(self._records)


def _scene_path(value: str | int | float | bool | list[str] | None) -> bool:
    """A literal Assets-relative scene path, never a search or inferred target."""
    return (
        type(value) is str
        and 0 < len(value) <= 512
        and value.startswith("Assets/")
        and value.endswith(".unity")
        and not any(character in value for character in ("\\", "\x00", ":"))
        and all(part and part not in (".", "..") for part in value.split("/"))
    )


def _outcome(reason: str) -> ToolResult:
    return ToolResult(structured_content={"success": False, "error": reason, "message": reason})


class SceneLoadInputs(Middleware):
    """One process-local SDK state policy plus a bounded scene-path guard."""

    def __init__(self, *, remote_hosted: bool) -> None:
        # Optional public APIs are imported only after the explicit version gate.
        from fastmcp import Context, FastMCP
        from fastmcp.tools.base import InputRequiredToolResult
        from mcp.server.request_state import RequestStateSecurity
        from mcp_types import (
            ElicitRequest,
            ElicitRequestFormParams,
            ElicitResult,
            InputRequiredResult,
        )

        if "request_state_security" not in inspect.signature(FastMCP).parameters:
            raise RuntimeError("Unsupported MRTR request-state policy")
        if not all(hasattr(FastMCP, name) for name in ("get_tool", "add_middleware")):
            raise RuntimeError("Unsupported MRTR middleware API")
        if not all(
            hasattr(Context, name) for name in ("request_state", "input_responses", "session")
        ):
            raise RuntimeError("Unsupported MRTR context API")
        if not callable(getattr(MiddlewareContext, "copy", None)):
            raise RuntimeError("Unsupported MRTR typed continuation")
        self.remote_hosted = remote_hosted
        self.local_owner = "local:v1:" + secrets.token_hex(16)
        self.ledger = InputLedger()
        self.policy = RequestStateSecurity(
            keys=[secrets.token_bytes(32)],
            ttl=TTL_SECONDS,
            bind_principal=self.principal,
            audience="unity-mcp/scene-load-input/v1",
        )
        self._input_result = InputRequiredResult
        self._input_wrapper = InputRequiredToolResult
        self._elicit_result = ElicitResult
        self._question = ElicitRequest(
            params=ElicitRequestFormParams(
                message="Which existing scene should Unity load? Supply its Assets-relative path.",
                requested_schema={
                    "type": "object",
                    "properties": {"path": {"type": "string", "minLength": 1, "maxLength": 512}},
                    "required": ["path"],
                },
            )
        )
        self._ask("0" * 32)  # Fail installation before publishing an inadmissible question.

    def principal(self, context: ServerRequestContext | FastMCPRequestContext) -> str:
        """Bind SDK envelopes to server-authored ASGI authentication state only."""
        request = context.request
        if request is None:
            raise _invalid()
        state = request.scope.get("state", {})
        if self.remote_hosted:
            user = state.get(AUTHENTICATED_USER_STATE)
            if type(user) is str and user:
                return "hosted:v1:" + sha256(user.encode("utf-8")).hexdigest()
            raise _invalid()
        if state.get(AUTHENTICATED_LOCAL_STATE) is not True:
            raise _invalid()
        return self.local_owner

    def _ask(self, nonce: str) -> ToolResult:
        result = self._input_result(
            input_requests={"scene_path": self._question}, request_state=STATE_PREFIX + nonce
        )
        if response_size(result, max_bytes=MAX_RESPONSE_BYTES - 32_768) is None:
            raise RuntimeError("Scene input exceeds the ordinary response budget")
        return self._input_wrapper(result)

    async def _scope(self, ctx: Context, owner: str) -> SceneScope | None:
        from transport.plugin_hub import PluginHub

        instance = await ctx.get_state("unity_instance")
        session_id = await ctx.get_state("unity_session_id")
        if not instance or not session_id or not PluginHub.is_configured():
            return None
        identity = await PluginHub.capture_tool_identity(session_id)
        if not identity:
            return None
        user_id = await ctx.get_state("user_id")
        if self.remote_hosted and identity.user_id != user_id:
            raise _invalid()
        return SceneScope(
            owner,
            instance,
            instance.rsplit("@", 1)[-1],
            identity.session_id,
            identity.socket_id,
            identity.generation,
        )

    async def on_call_tool(self, context: MiddlewareContext, call_next: CallNext) -> ToolResult:
        from fastmcp.tools.function_tool import FunctionTool
        from services.tools.manage_scene import manage_scene

        ctx = context.fastmcp_context
        message = context.message
        if message.name != "manage_scene" or ctx is None or ctx.request_context is None:
            return await call_next(context)
        from transport.execution_receipts import execution_options, reject_continuation
        from services.execution_receipt_identity import ReceiptUnavailable

        if execution_options(ctx) is not None:
            reject_continuation(ctx)
            # Explicit paths use ordinary execution; an ask is an unsupported leg.
            args = message.arguments or {}
            if args.get("action") == "load" and not any(
                args.get(key) is not None for key in ("name", "path", "build_index")
            ):
                raise ReceiptUnavailable("execution_receipt_scene_input_unsupported")
            return await call_next(context)
        args = message.arguments or {}
        if (
            args.get("action") != "load"
            or any(args.get(key) is not None for key in ("name", "path", "build_index"))
            or set(args) - {"action", "name", "path", "build_index", "additive"}
            or (args.get("additive") is not None and type(args["additive"]) is not bool)
        ):
            return await call_next(context)
        if ctx.session.protocol_version != "2026-07-28":
            return await call_next(context)
        capabilities = ctx.session.client_capabilities
        elicitation = capabilities.elicitation if capabilities else None
        if elicitation is None or (elicitation.form is None and elicitation.url is not None):
            return await call_next(context)
        tool = await ctx.fastmcp.get_tool(message.name)
        if type(tool) is not FunctionTool or inspect.unwrap(tool.fn) is not inspect.unwrap(
            manage_scene
        ):
            return await call_next(context)
        owner = self.principal(ctx.request_context)
        scope = await self._scope(ctx, owner)
        if scope is None:
            if ctx.request_state is not None:
                raise _invalid()
            return await call_next(context)
        state = ctx.request_state
        answers = ctx.input_responses
        if state is None:
            if answers is not None:
                raise _invalid()
            try:
                nonce = self.ledger.issue(scope)
            except InputCapacityError:
                return await call_next(context)
            return self._ask(nonce)
        if (
            not state.startswith(STATE_PREFIX)
            or len(state) != len(STATE_PREFIX) + 32
            or any(character not in "0123456789abcdef" for character in state[len(STATE_PREFIX) :])
        ):
            raise _invalid()
        nonce = state[len(STATE_PREFIX) :]
        if self.ledger.lookup(nonce, scope):
            return _outcome("elicitation_already_attempted")
        answer = (answers or {}).get("scene_path")
        if answer is None:
            return self._ask(nonce)
        if not isinstance(answer, self._elicit_result):
            raise _invalid()
        match answer.action:
            case "accept":
                path = (answer.content or {}).get("path")
                if not _scene_path(path):
                    raise _invalid()
                if not self.ledger.claim(nonce, scope):
                    return _outcome("elicitation_already_attempted")
                try:
                    # Copy only the typed continuation: SDK raw arguments remain
                    # the original envelope binding, with their supplied selector.
                    continued = message.model_copy(update={"arguments": {**args, "path": path}})
                    return await call_next(context.copy(message=continued))
                finally:
                    self.ledger.finish(nonce)
            case "decline" | "cancel":
                if not self.ledger.claim(nonce, scope):
                    return _outcome("elicitation_already_attempted")
                self.ledger.finish(nonce)
                return _outcome("elicitation_" + answer.action)

    def close(self) -> None:
        self.ledger.close()


def prepare_scene_load_inputs() -> SceneLoadInputs | None:
    """Default-disabled, HTTP-only, exact reviewed public-SDK compatibility gate."""
    if config.transport_mode != "http" or os.environ.get("UNITY_MCP_SCENE_LOAD_INPUTS") != "1":
        return None
    try:
        if version("mcp") != "2.3.0" or version("fastmcp") != "4.1.1":
            raise RuntimeError("Unreviewed MRTR SDK version")
        return SceneLoadInputs(remote_hosted=config.http_remote_hosted)
    except (ImportError, AttributeError, TypeError, ValueError, RuntimeError):
        logger.warning(
            "Optional scene input flow unavailable; ordinary scene arguments remain supported"
        )
        return None
