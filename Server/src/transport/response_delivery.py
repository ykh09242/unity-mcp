"""Adapt authenticated MCP delivery to bounded, recoverable result encodings."""

from __future__ import annotations

from dataclasses import dataclass
from contextvars import ContextVar

from fastmcp import Context
from pydantic import BaseModel, JsonValue
from pydantic_core import to_json

from core.config import config
from models.response_limits import response_size
from services.credential_lease import require_credential_lease
from services.operation_permissions import Permission
from services.response_delivery import (
    ResponseAuthority,
    ResponseScope,
    ResponseStore,
    response_store,
)
from transport.local_project_context import full_hash
from transport.protocol import is_sessionless

MAX_MCP_RESPONSE_BYTES = 8 * 1024 * 1024
# Covers the typed request ID, JSON-RPC envelope and pinned SDK protocol metadata.
MCP_RESULT_BYTES = MAX_MCP_RESPONSE_BYTES - 32_768
_EXECUTION_META = "io.github.ykh09242.unity-mcp/execution"


@dataclass(frozen=True, slots=True)
class ResponseDelivery:
    store: ResponseStore
    authority: ResponseAuthority


@dataclass(frozen=True, slots=True)
class ResponseOutcome:
    is_error: bool
    structured: dict[str, JsonValue] | None
    meta: dict[str, JsonValue] | None


response_delivery: ContextVar[ResponseDelivery | None] = ContextVar(
    "unity_response_delivery", default=None
)
response_lookup_selector: ContextVar[str | None] = ContextVar(
    "unity_response_lookup_selector", default=None
)


async def capture_response_delivery(
    ctx: Context | None, *, lookup_selector: str | None = None
) -> ResponseDelivery | None:
    """Capture resolved state for writes or a project constraint for an artifact lookup."""
    if ctx is None:
        return None
    instance = (
        lookup_selector if lookup_selector is not None else await ctx.get_state("unity_instance")
    )
    project = full_hash(instance) if isinstance(instance, str) else ""
    if isinstance(instance, str) and project is None:
        return None
    session = "" if is_sessionless(ctx) else ctx.session_id
    if len(session) > 256:
        return None
    if config.http_remote_hosted:
        # An unbound result has no safe retention domain. Scope capture must not
        # add an unrelated credential lookup to an otherwise completed response.
        if not project:
            return None
        lease = await require_credential_lease()
        identity = await ctx.get_state("mcpforunity.tool_permission_identity")
        # A native identity, when present, must agree with the current principal.
        # Server-only reads may have no live socket after the operation finishes.
        if identity is not None and getattr(identity, "user_id", None) != lease.user_id:
            return None
        principal = "remote:" + lease.user_id
        permissions = frozenset(permission.value for permission in lease.permissions.permissions)
    else:
        principal = "local"
        permissions = frozenset(permission.value for permission in Permission)
    return ResponseDelivery(
        response_store(ctx.fastmcp),
        ResponseAuthority(ResponseScope(principal, project or "", session), permissions, instance),
    )


def retained_response(
    wire: BaseModel | dict[str, JsonValue], delivery: ResponseDelivery | None
) -> dict[str, JsonValue]:
    """Retain a trusted graph admitted by the existing internal workload bound."""
    if delivery is not None:
        # Admission excludes extension serializers before this trusted projection.
        projection = (
            BaseModel.model_dump(wire, mode="json", by_alias=True, exclude_none=True)
            if isinstance(wire, BaseModel)
            else wire
        )
        payload = to_json(projection, by_alias=True, exclude_none=True)
        handle = delivery.store.put(payload, delivery.authority)
        if handle is not None:
            return handle
    return {
        "state": "unavailable",
        "reason": "response_retention_unavailable",
        "retry_safe": False,
    }


def response_summary(
    handle: dict[str, JsonValue], outcome: ResponseOutcome
) -> dict[str, JsonValue]:
    """Describe delivery separately from the original operation's observed outcome."""
    summary: dict[str, JsonValue] = {
        "delivery": handle,
        "original_is_error": outcome.is_error,
        "message": "The operation returned an oversized response. Use read_response when a "
        "handle is available. Do not repeat the operation to retrieve its response.",
    }
    structured = outcome.structured
    if type(structured) is dict:
        sdk = outcome.meta.get("fastmcp") if type(outcome.meta) is dict else None
        if (
            type(sdk) is dict
            and sdk.get("wrap_result") is True
            and len(structured) == 1
            and type(structured.get("result")) is dict
        ):
            structured = structured["result"]
        # Only preserve bounded top-level outcome evidence; business data stays in the handle.
        for key in ("success", "status", "code", "error", "receipt_id"):
            value = structured.get(key)
            if type(value) in (str, bool, int) and response_size(value, max_bytes=2048) is not None:
                summary[key] = value
        evidence = structured.get("data")
        if type(evidence) is dict:
            if response_size(evidence, max_bytes=8192) is not None:
                summary["data"] = evidence
            else:
                bounded: dict[str, JsonValue] = {}
                for key in (
                    "reason",
                    "command_id",
                    "session_id",
                    "generation",
                    "job_id",
                    "receipt_id",
                    "outcome",
                    "status",
                    "state",
                    "dispatched",
                    "observed",
                    "retry_safe",
                    "phase",
                ):
                    value = evidence.get(key)
                    if (
                        type(value) in (str, bool, int)
                        and response_size(value, max_bytes=2048) is not None
                    ):
                        bounded[key] = value
                if bounded:
                    summary["data"] = bounded
    return summary


def delivery_meta(meta: dict[str, JsonValue] | None) -> dict[str, JsonValue] | None:
    """Keep bounded execution evidence even when unrelated metadata is oversized."""
    if meta is None:
        return None
    if response_size(meta, max_bytes=16_384) is not None:
        # The delivery summary has its own direct object shape. Its original
        # wrapped response and metadata remain unchanged in the retained bytes.
        return {key: value for key, value in meta.items() if key != "fastmcp"}
    execution = meta.get(_EXECUTION_META)
    if execution is not None and response_size(execution, max_bytes=8192) is not None:
        return {_EXECUTION_META: execution}
    return None
