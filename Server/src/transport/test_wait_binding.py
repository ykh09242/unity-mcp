"""Bind one existing test-job wait to Tasks using the pinned SDK result kernel."""

from __future__ import annotations

import asyncio
import inspect
import logging
import time
from dataclasses import asdict
from importlib.metadata import version
from pathlib import Path
from uuid import uuid4

from fastmcp import Context, FastMCP
from fastmcp.exceptions import DisabledError, FastMCPError, NotFoundError
from fastmcp.tools.base import ToolResult
from mcp.server.connection import Connection
from mcp.shared.exceptions import MCPError
from mcp_types import INTERNAL_ERROR, INVALID_PARAMS
from pydantic import ValidationError

from core.sdk_compatibility import is_reviewed_sdk
from services.credential_lease import require_credential_lease

from services.test_wait_journal import (
    DeadlineCheckpoint,
    JournalUnavailable,
    WaitJournal,
    WaitOperation,
)
from services.tools.test_job_wait import test_job_wait_deadline
from services.tools.wait_for_operation import capture_target
from transport.local_auth_middleware import AUTHENTICATED_LOCAL_STATE
from transport.remote_auth_middleware import AUTHENTICATED_USER_STATE
from transport.plugin_hub import capture_http_request_budget, detached_http_request_budget
from transport.response_limit_middleware import ResponseLimitMiddleware
from transport.test_job_tasks import CapturedWait, TestJobTasks

logger = logging.getLogger(__name__)


class _SdkResultKernel:
    """One explicitly gated pinned bridge; the SDK keeps its formatter/policy."""

    def __init__(self, server: FastMCP):
        if not is_reviewed_sdk(version):
            raise RuntimeError("Unreviewed Tasks SDK version")
        # Private pinned hooks are optional: import only after the version gate,
        # so a different supported base SDK cannot break default-disabled startup.
        from fastmcp.server.dependencies import _background_task_session_id
        from fastmcp.server.low_level import LowLevelServer
        from fastmcp.server.mixins.mcp_operations import _normalize_call_tool_result
        from mcp.server.runner import ServerRunner

        if not isinstance(server._mcp_server, LowLevelServer):
            raise RuntimeError("Unsupported Tasks SDK server")
        if tuple(inspect.signature(_normalize_call_tool_result).parameters) != ("result",):
            raise RuntimeError("Unsupported Tasks SDK normalizer")
        if tuple(inspect.signature(ServerRunner._serialize).parameters) != (
            "self",
            "method",
            "version",
            "result",
        ):
            raise RuntimeError("Unsupported Tasks SDK serialization")
        if "run_middleware" not in inspect.signature(server.call_tool).parameters:
            raise RuntimeError("Unsupported Tasks SDK invocation")
        self.server = server
        self.normalize = _normalize_call_tool_result
        self.session_id = _background_task_session_id
        self.admission = ResponseLimitMiddleware()
        # An owned no-channel SDK connection is used solely for its result
        # kernel, never for dispatch/routing or inherited request state.
        self.runner = ServerRunner(
            server._mcp_server, Connection.from_envelope("2026-07-28", None, {}), {}
        )
        probe = self.serialize(ToolResult(content=[]))
        if probe.get(
            "resultType"
        ) != "complete" or "io.modelcontextprotocol/serverInfo" not in probe.get("_meta", {}):
            raise RuntimeError("Unsupported Tasks SDK result shape")

    def serialize(self, result: ToolResult) -> dict:
        bounded = self.admission.admit_tool_result(result)
        normalized = self.normalize(bounded.to_mcp_result())
        return self.runner._serialize("tools/call", "2026-07-28", normalized)

    async def invoke(self, operation: WaitOperation) -> ToolResult:
        session = self.session_id.set(f"unity-wait:{uuid4().hex}")
        try:
            async with Context(fastmcp=self.server) as context:
                if context.request_context is not None:
                    raise MCPError(INTERNAL_ERROR, "Detached wait inherited an HTTP context")
                for key, value in (
                    ("unity_instance", operation.instance),
                    ("unity_session_id", operation.session_id),
                    ("user_id", operation.user_id),
                    ("operation_wait_generation", operation.generation),
                ):
                    await context.set_state(key, value, serializable=False)
                try:
                    # Public SDK execution performs real schema validation,
                    # FunctionTool conversion and configured error masking.
                    arguments = {"job_id": operation.job_id, "wait_timeout": operation.wait_timeout}
                    if operation.tool_name == "wait_for_operation":
                        arguments["kind"] = operation.job_kind
                    return await self.server.call_tool(
                        operation.tool_name,
                        arguments,
                        run_middleware=False,
                    )
                except (DisabledError, NotFoundError):
                    return ToolResult(
                        content=f"Unknown tool: {operation.tool_name!r}", is_error=True
                    )
                except FastMCPError as error:
                    # This is the SDK handler's bridge AFTER public call_tool
                    # applied masking. Do not recreate its exception policy.
                    return ToolResult(content=str(error), is_error=True)
        finally:
            self.session_id.reset(session)


def install_test_job_tasks(
    server: FastMCP, root: Path, *, remote_hosted: bool
) -> TestJobTasks | None:
    """Explicit opt-in; unsupported SDK/private store leaves Tasks unadvertised."""
    server._test_wait_tasks_requested = True
    journal = None
    try:
        sdk = _SdkResultKernel(server)
        journal = WaitJournal(root)
    except (RuntimeError, JournalUnavailable, OSError, ImportError, AttributeError, TypeError):
        if journal is not None:
            journal.close()
        logger.warning("Optional bounded-wait Tasks are unavailable")
        return None

    async def owner(ctx):
        request = ctx.request
        if request is None:
            raise MCPError(INVALID_PARAMS, "Authenticated HTTP is required for wait Tasks")
        state = request.scope.get("state", {})
        if remote_hosted:
            lease = await require_credential_lease()
            user_id = state.get(AUTHENTICATED_USER_STATE)
            if user_id != lease.user_id:
                raise MCPError(INVALID_PARAMS, "Authenticated wait owner is required")
            if not isinstance(user_id, str) or not user_id:
                raise MCPError(INVALID_PARAMS, "Authenticated wait owner is required")
            return f"hosted:v1:{user_id}"
        if state.get(AUTHENTICATED_LOCAL_STATE) is not True:
            raise MCPError(INVALID_PARAMS, "Authenticated local control is required")
        return journal.local_owner

    async def capture(params, context):
        snapshot = capture_http_request_budget()
        if snapshot is None:
            return None
        loop = asyncio.get_running_loop()
        wait_timeout = params.arguments.get("wait_timeout", 30)
        inner = loop.time() + wait_timeout
        instance = await context.get_state("unity_instance")
        session_id = await context.get_state("unity_session_id")
        user_id = await context.get_state("user_id") if remote_hosted else None
        if (
            not isinstance(instance, str)
            or not isinstance(session_id, str)
            or not instance
            or not session_id
        ):
            return None
        target = await capture_target(context) if params.name == "wait_for_operation" else None
        if params.name == "wait_for_operation" and target is None:
            return None
        wall, now = time.time(), loop.time()
        try:
            operation = WaitOperation(
                owner=await owner(context.request_context._srctx),
                user_id=user_id,
                project=instance.rpartition("@")[2],
                instance=instance,
                session_id=session_id,
                job_id=params.arguments["job_id"],
                wait_timeout=wait_timeout,
                tool_name=params.name,
                job_kind=params.arguments.get("kind") if target is not None else None,
                generation=target.generation if target is not None else None,
                wait=DeadlineCheckpoint.capture(max(0, inner - now), now=wall),
                outer=DeadlineCheckpoint.capture(max(0, snapshot.expires_at - now), now=wall),
                evidence=asdict(snapshot.evidence),
            )
        except ValidationError:
            return None
        lease = await require_credential_lease(user_id) if remote_hosted else None
        return CapturedWait(
            operation, inner, snapshot.expires_at, {}, snapshot, credential_lease=lease
        )

    async def fallback(captured, call_next):
        token = test_job_wait_deadline.set(captured.wait_deadline)
        try:
            return await call_next()
        finally:
            test_job_wait_deadline.reset(token)

    async def execute(captured):
        guard = asyncio.timeout_at(captured.outer_deadline)
        token = test_job_wait_deadline.set(captured.wait_deadline)
        try:
            with detached_http_request_budget(captured.budget_snapshot, guard) as budget:
                if asyncio.get_running_loop().time() >= captured.outer_deadline:
                    result = ToolResult(
                        structured_content=budget.budget.timeout_response(
                            captured.operation.tool_name
                        ),
                        is_error=True,
                    )
                else:
                    try:
                        async with guard:
                            result = await sdk.invoke(captured.operation)
                    except TimeoutError:
                        if not guard.expired():
                            raise
                        result = ToolResult(
                            structured_content=budget.budget.timeout_response(
                                captured.operation.tool_name
                            ),
                            is_error=True,
                        )
                return sdk.serialize(result)
        finally:
            test_job_wait_deadline.reset(token)

    runtime = TestJobTasks(
        journal,
        capture=capture,
        execute=execute,
        owner=owner,
        fallback=fallback,
        executor_owns_deadline=True,
    )
    server.add_extension(runtime)
    server._test_job_tasks = runtime
    return runtime
