"""Bound final MCP output and retain result capacity through response handoff."""
from __future__ import annotations

import asyncio
import json
import re
from hashlib import sha256
from typing import Any
from urllib.parse import parse_qs

from fastmcp.server.middleware import Middleware, MiddlewareContext, CallNext
from fastmcp.tools.base import ToolResult
from fastmcp.resources.base import ResourceResult

from models.response_limits import (
    MAX_RESPONSE_BYTES, ResponseOwner, response_owner, response_size, response_limit_error,
)
from transport.remote_auth_middleware import AUTHENTICATED_USER_STATE
from transport.stdio_response_delivery import stdio_delivery

# Only owners of admitted plugin results enter this map; PluginHub's global
# retained-byte and pending-command budgets also bound this bookkeeping.
_http_response_owners: dict[tuple[str, str, type, str], list[ResponseOwner]] = {}
_response_envelope = re.compile(
    rb'^\s*\{\s*"jsonrpc"\s*:\s*"2\.0"\s*,\s*"id"\s*:\s*'
    rb'("(?:[^"\\]|\\.)*"|-?[0-9]+)\s*,\s*"(?:result|error)"\s*:')
_endpoint_session = re.compile(rb'session_id=([a-f0-9]{32})')
_OWNER_STATE = "unity_mcp_response_owners"


def _frame_id(body: bytes) -> str | int | None:
    """Inspect the bounded JSON-RPC envelope prefix, never the result graph."""
    prefix = body[:65_536]
    # Installed SDK envelopes put jsonrpc/id/result in this order. If an SDK
    # changes that order or chunks the prefix, retain until request/stream exit.
    if prefix.startswith((b'id:', b'event:', b'data:')):
        lines = prefix.splitlines()
        prefix = next((line[5:].lstrip() for line in lines if line.startswith(b'data:')), b'')
    match = _response_envelope.match(prefix)
    if match is None:
        return None
    try:
        return json.loads(match.group(1))
    except (ValueError, UnicodeError):
        return None


def _scope_session(scope) -> str | None:
    headers = dict(scope.get("headers", []))
    header = headers.get(b"mcp-session-id")
    if header is not None:
        return header.decode("latin-1")
    query = parse_qs(scope.get("query_string", b"").decode("latin-1"))
    return next(iter(query.get("session_id", [])), None)


def _owner_key(scope, session: str, request_id: str | int) -> tuple[str, str, type, str]:
    principal = scope.get("state", {}).get(AUTHENTICATED_USER_STATE) or "local"
    return (sha256(principal.encode("utf-8")).hexdigest(), session,
            type(request_id), sha256(str(request_id).encode("utf-8")).hexdigest())


def _release_http_owner(key: tuple[str, str, type, str], *, closing: bool = False) -> None:
    owners = _http_response_owners.get(key, [])
    # Concurrent duplicate IDs cannot identify which queued response was sent.
    # Keep all charges until their individual HTTP requests or SSE stream exit.
    if len(owners) > 1 and not closing:
        return
    _http_response_owners.pop(key, None)
    for owner in owners:
        owner.release()


class ResponseLimitMiddleware(Middleware):
    """Measure both text and structured content before the final MCP serializer."""

    async def on_message(self, context: MiddlewareContext, call_next: CallNext) -> Any:
        owner = ResponseOwner()
        token = response_owner.set(owner)
        task = asyncio.current_task()
        key = None
        ctx = context.fastmcp_context
        rc = ctx.request_context if ctx is not None else None
        request = rc.request if rc is not None else None
        delivery = stdio_delivery.get() if request is None else None
        delivery_entry = None
        if delivery is not None and rc is not None and rc.request_id is not None:
            # FastMCP 4's documented SDK escape hatch preserves int/string IDs.
            try:
                sdk_context = getattr(rc, "_srctx", None)
                if sdk_context is None or not hasattr(sdk_context, "request_id"):
                    raise RuntimeError("Installed FastMCP stdio response context is unsupported")
                delivery_entry = delivery.register(sdk_context.request_id, owner)
            except BaseException:
                owner.release()
                response_owner.reset(token)
                raise
        if request is not None and rc.request_id is not None:
            session = _scope_session(request.scope) or ctx.session_id
            # FastMCP's compatibility wrapper stringifies IDs; the SDK context
            # retains the original JSON-RPC int/string identity.
            request_id = rc._srctx.request_id
            if len(session) > 256 or len(str(request_id)) > 4096:
                owner.release()
                response_owner.reset(token)
                raise ValueError("MCP response identity exceeds supported limits")
            key = _owner_key(request.scope, session, request_id)
            if key in _http_response_owners:
                owner.release()
                response_owner.reset(token)
                raise ValueError("MCP response identity is already active")

        def task_done(completed) -> None:
            # A successful HTTP producer can finish while SDK queues or the
            # socket writer still retain its result. Delivery owns release.
            failed = completed.cancelled() or completed.exception() is not None
            if delivery_entry is not None:
                delivery.producer_done(delivery_entry, failed)
            elif key is None or failed:
                owner.release()

        if task is not None:
            task.add_done_callback(task_done)
        try:
            result = await call_next(context)
            if delivery is not None and delivery_entry is None and owner.entries:
                raise RuntimeError("Installed FastMCP stdio response context is unsupported")
            if delivery_entry is not None and not owner.entries:
                owner.release()
            if key is not None and owner.entries:
                _http_response_owners.setdefault(key, []).append(owner)

                def forget_owner() -> None:
                    owners = _http_response_owners.get(key, [])
                    owners[:] = [existing for existing in owners if existing is not owner]
                    if not owners:
                        _http_response_owners.pop(key, None)

                owner.on_release.append(forget_owner)
                request_owners = request.scope.get("state", {}).get(_OWNER_STATE)
                if request_owners is not None:
                    request_owners.append(owner)
            return result
        except BaseException as error:
            # SDK dispatch can translate an exception into a protocol error and
            # finish its task successfully. Failed producers own no deliverable
            # plugin result, so release here rather than waiting for task status.
            held_result = bool(owner.entries)
            owner.release()
            if held_result and isinstance(error, Exception):
                # Do not turn a rejected/failed plugin consumer's arbitrary
                # exception string into another unbounded protocol payload.
                raise ValueError("Unity response could not be completed") from None
            raise
        finally:
            response_owner.reset(token)
            if task is None:
                owner.release()

    async def on_call_tool(self, context: MiddlewareContext, call_next: CallNext) -> ToolResult:
        result = await call_next(context)
        raw = getattr(result, "_raw_mcp_result", None)
        wire = raw if raw is not None else {
            "content": result.content, "structuredContent": result.structured_content,
            "_meta": result.meta, "isError": result.is_error,
        }
        # Reserve envelope overhead independently of the plugin graph ceiling.
        if response_size(wire, max_bytes=MAX_RESPONSE_BYTES - 32_768) is None:
            error = response_limit_error()
            return ToolResult(content=error, structured_content=error, is_error=True)
        return result

    async def on_read_resource(self, context: MiddlewareContext, call_next: CallNext) -> ResourceResult:
        result = await call_next(context)
        wire = result.to_mcp_result(str(context.message.uri))
        if response_size(wire, max_bytes=MAX_RESPONSE_BYTES - 32_768) is None:
            return ResourceResult(json.dumps(response_limit_error()))
        return result


class ResponseRetentionMiddleware:
    """Keep HTTP command/result reservations through slow send and cancellation."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        owner = ResponseOwner()
        token = response_owner.set(owner)
        session = _scope_session(scope)
        sse = False
        legacy_sse = False
        request_owners: list[ResponseOwner] = []
        owned_scope = {**scope, "state": {**scope.get("state", {}), _OWNER_STATE: request_owners}}

        async def send_owned(message):
            nonlocal session, sse, legacy_sse
            if message["type"] == "http.response.start":
                headers = dict(message.get("headers", []))
                # Legacy SSE's endpoint can emit an empty final Response after
                # EventSourceResponse ends; preserve the stream's cleanup mode.
                sse = sse or headers.get(b"content-type", b"").startswith(b"text/event-stream")
                header = headers.get(b"mcp-session-id")
                if header is not None:
                    session = header.decode("latin-1")
            body = message.get("body", b"")
            # Only legacy SSE advertises a separate POST endpoint. A standalone
            # Streamable HTTP GET has no ownership of concurrent POST replies.
            if sse and body.startswith((b"event: endpoint\r\n", b"event: endpoint\n")):
                match = _endpoint_session.search(body[:65_536])
                if match is not None:
                    legacy_sse = True
                    session = match.group(1).decode("ascii")
            request_id = _frame_id(body) if body else None
            await send(message)
            # Release only after the actual socket/ASGI consumer accepts the
            # complete response frame. POST202 is never mistaken for delivery.
            complete = (body.endswith((b"\r\n\r\n", b"\n\n")) if sse
                        else not message.get("more_body", False))
            if session is not None and request_id is not None and complete:
                _release_http_owner(_owner_key(scope, session, request_id))
        try:
            await self.app(owned_scope, receive, send_owned)
        finally:
            response_owner.reset(token)
            owner.release()
            legacy_post = (scope.get("method") == "POST" and
                           "session_id" in parse_qs(scope.get("query_string", b"").decode("latin-1")) and
                           b"mcp-session-id" not in dict(scope.get("headers", [])))
            if not legacy_post:
                for request_owner in request_owners:
                    request_owner.release()
            if session is not None:
                if scope.get("method") == "GET" and legacy_sse:
                    for key in tuple(_http_response_owners):
                        if key[1] == session and key[0] == _owner_key(scope, session, 0)[0]:
                            _release_http_owner(key, closing=True)
