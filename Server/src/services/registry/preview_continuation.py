"""Bind a source-owned preview proposal to its public operation and selected Editor."""

import re
import math
import struct
from collections.abc import Callable
from dataclasses import dataclass

from jsonschema import Draft202012Validator
from referencing import Registry
from referencing.exceptions import Unresolvable

from core.config import config
from services.operation_permissions import remote_operation_allowed
from services.registry.operation_target import OperationTarget


@dataclass(frozen=True)
class PreviewApply:
    arguments: dict
    verify: dict
    # Exact large inputs that can be copied from the original public preview call.
    copy_arguments: tuple[str, ...] = ()


PreviewContract = Callable[[dict, dict], PreviewApply | None]


@dataclass(frozen=True)
class PreviewBinding:
    definition: object
    fn: object
    key: object
    auth: object
    version: object

    @classmethod
    def capture(cls, definition):
        return cls(
            definition,
            getattr(definition, "fn", None),
            getattr(definition, "key", None),
            getattr(definition, "auth", None),
            getattr(definition, "version", None),
        )

    def matches(self, current):
        return (
            current is not None
            and type(current) is type(self.definition)
            and (current is self.definition or self.fn is not None)
            and getattr(current, "fn", None) is self.fn
            and getattr(current, "key", None) == self.key
            and getattr(current, "auth", None) is self.auth
            and getattr(current, "version", None) == self.version
        )


@dataclass
class PreviewRegistration:
    """The one SDK descriptor built around this particular invocation wrapper."""

    contracts: dict[str, PreviewContract]
    binding: PreviewBinding | None = None


def is_preview_digest(value, length: int) -> bool:
    return (
        isinstance(value, str)
        and len(value) == length
        and re.fullmatch("[a-f0-9]+", value) is not None
    )


def has_field_edits(rows):
    return (
        isinstance(rows, list)
        and bool(rows)
        and all(
            isinstance(row, dict)
            and isinstance(row.get("property"), str)
            and "before" in row
            and "after" in row
            for row in rows
        )
        and any(row["before"] != row["after"] for row in rows)
    )


def native_plan_id(data) -> str | None:
    token = data.get("preview_id")
    lifetime = data.get("expires_in_seconds")
    if is_preview_digest(token, 32) and type(lifetime) in (int, float) and 0 < lifetime <= 600:
        return token
    return None


def same_serialized_value(requested, observed) -> bool:
    """Compare the native Boolean, Single and fixed-length vector/quaternion codecs."""
    if type(requested) is bool:
        return observed is requested
    if isinstance(requested, list):
        return (
            isinstance(observed, list)
            and len(requested) == len(observed) in (3, 4)
            and all(
                type(left) in (int, float) and same_serialized_value(left, right)
                for left, right in zip(requested, observed)
            )
        )
    if type(requested) not in (int, float) or type(observed) not in (int, float):
        return False
    try:
        return (
            math.isfinite(requested)
            and math.isfinite(observed)
            and struct.pack("!f", requested) == struct.pack("!f", observed)
        )
    except (OverflowError, struct.error):
        return False


def matches_serialized_edit(requested, row) -> bool:
    before, after = row["before"], row["after"]
    if type(after) is bool:
        valid_before = type(before) is bool
    elif isinstance(after, list):
        valid_before = isinstance(before, list) and len(before) == len(after)
    else:
        valid_before = type(before) in (float, int)
    return (
        valid_before
        and same_serialized_value(before, before)
        and same_serialized_value(requested, after)
    )


async def with_preview_continuation(
    value,
    public_name,
    contract: PreviewContract,
    params,
    definition,
    ctx,
    instance,
    target: OperationTarget | None,
    *,
    binding: PreviewBinding,
):
    from transport.legacy.unity_connection import UnityConnectionPool
    from transport.unity_instance_middleware import (
        EXPLICIT_REQUEST_INSTANCE_STATE,
        get_unity_instance_middleware,
    )

    if (
        not isinstance(value, dict)
        or value.get("success") is not True
        or not isinstance(data := value.get("data"), dict)
        or "follow_up" in data
        or not UnityConnectionPool.is_exact_instance_id(instance)
    ):
        return value
    # A successful legacy stdio command can precede a transport-generation fence.
    # Only its exact, middleware-resolved selector can bind a subsequent call.
    if target is None:
        if (
            config.transport_mode != "stdio"
            or config.http_remote_hosted
            or await ctx.get_state(EXPLICIT_REQUEST_INSTANCE_STATE) != instance
        ):
            return value
    elif target.scope.instance != instance.rsplit("@", 1)[-1].lower() or not target.is_current():
        return value
    proposal = contract(params, data)
    if proposal is None:
        return value
    schema = getattr(definition, "parameters", {}) or {}
    arguments = {**proposal.arguments, "unity_instance": instance}
    if not arguments.keys() <= schema.get("properties", {}).keys():
        return value
    if config.require_explicit_target and not config.http_remote_hosted:
        # An automatically selected read cannot supply an explicit write selection.
        if await ctx.get_state(EXPLICIT_REQUEST_INSTANCE_STATE) != instance:
            if await get_unity_instance_middleware().get_active_instance(ctx) != instance:
                return value
    if not await remote_operation_allowed(public_name, proposal.arguments, ctx=ctx):
        return value
    current = await ctx.fastmcp.get_tool(public_name)
    if (
        not binding.matches(current)
        or not arguments.keys() <= current.parameters.get("properties", {}).keys()
    ):
        return value
    try:
        if not Draft202012Validator(current.parameters, registry=Registry()).is_valid(arguments):
            return value
    except Unresolvable:
        return value
    if target is not None and not target.is_current():
        return value
    follow_up = {
        "action": "apply_if_requested",
        "condition": "Submit the write if the user requested this change, the preview matches, and the host permits it.",
        "verify": proposal.verify,
    }
    call = {"tool": public_name, "arguments": arguments}
    if proposal.copy_arguments:
        for field in proposal.copy_arguments:
            if field not in params or field not in arguments or arguments[field] != params[field]:
                return value
            del arguments[field]
        follow_up["call_template"] = call
        follow_up["copy_from_preview_arguments"] = list(proposal.copy_arguments)
    else:
        follow_up["next_call"] = call
    return {**value, "data": {**data, "follow_up": follow_up}}
