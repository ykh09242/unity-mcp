"""Bounded, connection-owned editor snapshots; never an authority for mutations."""

from copy import deepcopy
from dataclasses import dataclass
import math
from threading import RLock
import time
from typing import Callable, Final, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, ValidationError
from models.response_limits import response_size

EDITOR_STATE_CAPABILITY: Final = "editor_state_v1"


class CompilationState(BaseModel):
    model_config = ConfigDict(extra="allow", strict=True, frozen=True)
    __pydantic_extra__: dict[str, JsonValue] = Field(init=False)
    is_compiling: bool
    is_domain_reload_pending: bool


class PushedState(BaseModel):
    model_config = ConfigDict(extra="allow", strict=True, frozen=True)
    __pydantic_extra__: dict[str, JsonValue] = Field(init=False)
    schema_version: Literal["unity-mcp/editor_state@2"]
    observed_at_unix_ms: int = Field(ge=0)
    sequence: int = Field(ge=0)
    compilation: CompilationState


class EditorStateMessage(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    type: Literal["editor_state"]
    epoch: str = Field(min_length=1, max_length=64)
    sequence: int = Field(ge=0)
    observed_at_unix_ms: int = Field(ge=0)
    state: PushedState


@dataclass(frozen=True, slots=True)
class StoredState:
    epoch: str
    sequence: int
    observed_ms: int
    received_at: float
    state: dict[str, JsonValue]
    content: dict[str, JsonValue]


class EditorStateStore:
    """Mutable latest-value slots, bounded by registered authenticated connections.

    The caller supplies the real socket's session ID, never a client payload ID.
    A domain/sequence reset requires registration of a new connection. Readers
    additionally supply the resolved registry owner and must check socket liveness.
    """

    def __init__(
        self,
        *,
        max_sessions: int = 256,
        monotonic: Callable[[], float] = time.monotonic,
        wall_time: Callable[[], float] = time.time,
    ) -> None:
        self._max_sessions = max_sessions
        self._monotonic = monotonic
        self._wall_time = wall_time
        self._owners: dict[str, str | None] = {}
        self._states: dict[str, StoredState] = {}
        self._invalidated: set[str] = set()
        self._lock = RLock()

    def register_session(self, session_id: str, user_id: str | None) -> bool:
        """Open one empty slot. Duplicate registration cannot reset its watermark."""
        with self._lock:
            if session_id in self._owners:
                return self._owners[session_id] == user_id
            if len(self._owners) >= self._max_sessions:
                return False
            self._owners[session_id] = user_id
            return True

    def remove_session(self, session_id: str) -> None:
        with self._lock:
            self._owners.pop(session_id, None)
            self._states.pop(session_id, None)
            self._invalidated.discard(session_id)

    def invalidate_session(self, session_id: str) -> None:
        """Require a new authenticated connection generation before serving state."""
        with self._lock:
            if session_id in self._owners:
                self._invalidated.add(session_id)
                self._states.pop(session_id, None)

    def reset(self) -> None:
        with self._lock:
            self._owners.clear()
            self._states.clear()
            self._invalidated.clear()

    def accept(self, session_id: str, payload: JsonValue) -> bool:
        """Parse a complete v1 message and replace only an equal/newer observation."""
        with self._lock:
            if session_id not in self._owners or session_id in self._invalidated:
                return False
        # Reuse the server's graph/serialization bounds before Pydantic allocates
        # model copies. The state channel is much smaller than a tool result.
        if (
            response_size(
                payload, max_bytes=128 * 1024, max_retained=512 * 1024, max_depth=16, max_nodes=4096
            )
            is None
        ):
            return False
        try:
            message = EditorStateMessage.model_validate(payload)
        except ValidationError:
            return False
        state = message.state
        if (
            message.sequence != state.sequence
            or message.observed_at_unix_ms != state.observed_at_unix_ms
        ):
            return False
        snapshot = state.model_dump(mode="json")
        content = {key: value for key, value in snapshot.items() if key != "observed_at_unix_ms"}
        with self._lock:
            if session_id not in self._owners or session_id in self._invalidated:
                return False
            previous = self._states.get(session_id)
            if previous is not None:
                if message.epoch != previous.epoch:
                    self.invalidate_session(session_id)
                    return False
                if (
                    message.sequence < previous.sequence
                    or message.observed_at_unix_ms < previous.observed_ms
                ):
                    return False
                # A heartbeat may advance liveness, never change content under a
                # reused sequence. Duplicates do not extend receive freshness.
                if message.sequence == previous.sequence:
                    if content != previous.content:
                        return False
                    if message.observed_at_unix_ms == previous.observed_ms:
                        return True
            self._states[session_id] = StoredState(
                message.epoch,
                message.sequence,
                message.observed_at_unix_ms,
                self._monotonic(),
                snapshot,
                content,
            )
            if state.compilation.is_domain_reload_pending:
                self.invalidate_session(session_id)
            return True

    def get(
        self, session_id: str, user_id: str | None, *, max_age_s: float = 2.0
    ) -> dict[str, JsonValue] | None:  # noqa: DICT_OK
        """Return a detached snapshot only for the registered owner while fresh."""
        if not math.isfinite(max_age_s) or max_age_s <= 0:
            return None
        with self._lock:
            if (
                session_id not in self._owners
                or self._owners[session_id] != user_id
                or session_id in self._invalidated
            ):
                return None
            state = self._states.get(session_id)
            if state is None:
                return None
            # Keep both limits finite, even if a caller requests an excessive TTL.
            age_limit = min(max_age_s, 2.0)
            observation_age_s = self._wall_time() - state.observed_ms / 1000.0
            if (
                self._monotonic() - state.received_at > age_limit
                or observation_age_s > age_limit
                or observation_age_s < -5.0
            ):
                return None
            return deepcopy(state.state)
