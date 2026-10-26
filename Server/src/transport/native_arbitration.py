"""Validate transport-owned admission separately from handler-controlled payloads."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Final
from uuid import uuid4

from services.operation_permissions import JsonValue

CAPABILITY: Final = "native_arbitration_v1"
_TRANSIENT: Final = frozenset(
    {"operation_busy", "editor_compiling", "editor_importing", "native_job_active"}
)
_IDENTITY: Final = re.compile(r"[0-9a-f]{32}\Z")


class NativeArbitrationProtocolError(RuntimeError):
    def __init__(self) -> None:
        super().__init__("invalid_native_arbitration_response")


@dataclass(frozen=True, slots=True)
class NativeArbitrationRequest:
    request_nonce: str
    instance_generation: str

    @classmethod
    def create(cls, generation: str) -> NativeArbitrationRequest:
        return cls(uuid4().hex, generation)

    def to_wire(self) -> dict[str, JsonValue]:
        return {
            "version": 1,
            "request_nonce": self.request_nonce,
            "instance_generation": self.instance_generation,
        }


@dataclass(frozen=True, slots=True)
class NativeReply:
    payload: dict[str, JsonValue]
    not_started: bool = False
    retryable: bool = False
    reason: str | None = None


class NativeNotStarted(RuntimeError):
    """Opaque transport proof; never constructed from a public tool's result."""

    def __init__(
        self,
        reply: NativeReply,
        *,
        transport_generation: str,
        params: dict[str, JsonValue],
    ) -> None:
        super().__init__("native_operation_not_started")
        self.reply = reply
        self.transport_generation = transport_generation
        self.params = params


def valid_generation(value: str | None) -> bool:
    return isinstance(value, str) and _IDENTITY.fullmatch(value) is not None


def may_retry_wire_attempt(command: str, reply: NativeReply) -> bool:
    from services.operation_policy import resolve_operation_policy

    # Classify the envelope itself, independently of handler-controlled fields
    # or malformed parameters. A whole batch is never replayed automatically.
    return (
        reply.retryable
        and resolve_operation_policy(command, {}, native=True).reason != "batch_envelope"
    )


def decode_native_reply(
    value: dict[str, JsonValue], request: NativeArbitrationRequest | None
) -> NativeReply:
    """Unwrap exactly one negotiated transport envelope, never its inner payload."""
    if request is None:
        return NativeReply(value)
    if set(value) != {"native_arbitration", "payload"}:
        raise NativeArbitrationProtocolError()
    proof, payload = value["native_arbitration"], value["payload"]
    if not isinstance(proof, dict) or not isinstance(payload, dict):
        raise NativeArbitrationProtocolError()
    if set(proof) - {
        "version",
        "request_nonce",
        "instance_generation",
        "disposition",
        "retryable",
        "reason",
    }:
        raise NativeArbitrationProtocolError()
    if (
        type(proof.get("version")) is not int
        or proof["version"] != 1
        or proof.get("request_nonce") != request.request_nonce
        or proof.get("instance_generation") != request.instance_generation
        or not valid_generation(request.instance_generation)
        or not valid_generation(request.request_nonce)
        or not isinstance(proof.get("disposition"), str)
        or proof["disposition"] not in {"started", "not_started"}
        or type(proof.get("retryable")) is not bool
    ):
        raise NativeArbitrationProtocolError()
    reason = proof.get("reason")
    if reason is not None and (not isinstance(reason, str) or len(reason) > 64):
        raise NativeArbitrationProtocolError()
    not_started = proof["disposition"] == "not_started"
    if not_started:
        from models.response_limits import response_size

        if (
            response_size(payload, max_bytes=8192, max_depth=8, max_nodes=128, max_retained=16384)
            is None
        ):
            raise NativeArbitrationProtocolError()
    elif proof["retryable"] or reason is not None:
        raise NativeArbitrationProtocolError()
    return NativeReply(
        payload,
        not_started,
        not_started and proof["retryable"] is True and reason in _TRANSIENT,
        reason,
    )
