"""Negotiated, sender-owned continuation of one native invocation across reloads."""

from __future__ import annotations

from dataclasses import dataclass, field
from threading import Event
import json
import time
from typing import Final, Literal
from uuid import uuid4

from services.operation_permissions import JsonValue
from services.operation_policy import OperationMode, resolve_operation_policy
from transport.native_arbitration import NativeReply, valid_generation

CAPABILITY: Final = "command_resume_v1"
ResumeAction = Literal["new", "status", "resume", "cancel"]
_STATES: Final = frozenset(
    {"queued", "executing", "completed", "canceled", "expired", "unknown", "unavailable"}
)


def resume_eligible(command: str, params: dict[str, JsonValue], *, custom: bool = False) -> bool:
    """Observations retain their existing read recovery without consuming mutation fences."""
    return (
        resolve_operation_policy(command, params, native=True, trusted_builtin=not custom).mode
        == OperationMode.EXCLUSIVE
    )


class CommandResumeProtocolError(RuntimeError):
    def __init__(self) -> None:
        super().__init__("invalid_command_resume_response")


class CommandResumeInterrupted(ConnectionError):
    """A negotiated attempt lost its transport; only the original sender may continue."""


class CommandResumeAbandoned(RuntimeError):
    """The original caller ended before another transport attempt could begin."""


@dataclass(slots=True)
class CommandResumeInvocation:
    """Mutable attempt action with an immutable identity/deadline and thread-safe cancellation."""

    invocation_id: str
    editor_session_id: str
    deadline_unix_ms: int
    action: ResumeAction = "new"
    state: str | None = None
    dispatched: bool = False
    cancelled: Event = field(default_factory=Event, repr=False)

    @classmethod
    def create(cls, editor_session_id: str, deadline: float) -> CommandResumeInvocation:
        if not valid_generation(editor_session_id):
            raise CommandResumeProtocolError()
        remaining = max(0.0, deadline - time.monotonic())
        return cls(uuid4().hex, editor_session_id, int((time.time() + remaining) * 1000))

    def to_wire(self) -> dict[str, JsonValue]:
        return {
            "version": 1,
            "invocation_id": self.invocation_id,
            "editor_session_id": self.editor_session_id,
            "deadline_unix_ms": self.deadline_unix_ms,
            "action": "cancel" if self.cancelled.is_set() else self.action,
        }

    def interrupted(self) -> None:
        if self.dispatched:
            self.action = "status" if self.state == "executing" else "resume"

    def dispatch(self) -> None:
        self.dispatched = True

    def cancel(self) -> None:
        self.cancelled.set()

    def unwrap(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        """Accept exactly one transport-owned envelope bound to the original invocation."""
        if set(value) != {"command_resume", "payload"}:
            raise CommandResumeProtocolError()
        proof, payload = value["command_resume"], value["payload"]
        if not isinstance(proof, dict) or not isinstance(payload, dict):
            raise CommandResumeProtocolError()
        if (
            set(proof) != {"version", "invocation_id", "editor_session_id", "state"}
            or type(proof.get("version")) is not int
            or proof["version"] != 1
            or proof.get("invocation_id") != self.invocation_id
            or proof.get("editor_session_id") != self.editor_session_id
            or not isinstance(proof.get("state"), str)
            or proof["state"] not in _STATES
        ):
            raise CommandResumeProtocolError()
        self.state = proof["state"]
        return payload

    def validate_reply(self, reply: NativeReply) -> None:
        waiting = self.state in {"queued", "executing"}
        if waiting != (reply.not_started and reply.retryable):
            raise CommandResumeProtocolError()
        if waiting:
            self.interrupted()

    def normalize_reply(self, command: str, reply: NativeReply) -> NativeReply:
        """Keep terminal uncertainty structured through both existing transport adapters."""
        if self.state not in {"unknown", "unavailable"}:
            return reply
        detail = reply.payload.get("error")
        detail = (
            detail[:512] if isinstance(detail, str) else "Original command result is unavailable"
        )
        code = reply.payload.get("code")
        code = code[:64] if isinstance(code, str) else "command_resume_" + self.state
        data: dict[str, JsonValue] = {
            "reason": "outcome_unknown",
            "command": command,
            "native_resume_state": self.state,
        }
        if code == "completed_result_not_retained":
            data["execution"] = "completed"
        return NativeReply(
            {
                "success": False,
                "error": detail,
                "hint": "inspect_state_before_retry",
                "code": code,
                "data": data,
            },
            reply.not_started,
            reply.retryable,
            reply.reason,
        )

    @property
    def waiting(self) -> bool:
        return self.state in {"queued", "executing"} and not self.cancelled.is_set()


@dataclass(slots=True)
class CommandResumeOwner:
    """Share cancellation with a shielded worker before its first authenticated bind."""

    deadline: float | None
    invocation: CommandResumeInvocation | None = None
    cancelled: Event = field(default_factory=Event, repr=False)
    params_json: str | None = field(default=None, repr=False)
    instance_id: str | None = None

    def bind(self, generation: str | None) -> CommandResumeInvocation | None:
        if self.cancelled.is_set():
            raise CommandResumeAbandoned()
        if self.invocation is None and self.deadline is not None and valid_generation(generation):
            self.invocation = CommandResumeInvocation.create(generation, self.deadline)
        return self.invocation

    def snapshot(self, params: dict[str, JsonValue]) -> dict[str, JsonValue]:
        if self.params_json is None:
            self.params_json = json.dumps(params)
        return json.loads(self.params_json)

    def cancel(self) -> None:
        self.cancelled.set()
        if self.invocation is not None:
            self.invocation.cancel()
