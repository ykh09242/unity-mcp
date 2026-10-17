"""Request-local project fences for local REST commands, never MCP session state."""

from contextvars import ContextVar
from dataclasses import dataclass
import os
import re

from pydantic import BaseModel, ConfigDict, Field, JsonValue
from starlette.websockets import WebSocket, WebSocketState
from transport.plugin_registry import PluginRegistry, PluginSession


def full_hash(value: str) -> str | None:
    """Only a complete native path hash is valid in a guarded command."""
    if len(value) > 256 or any(ord(c) < 32 or ord(c) == 127 for c in value):
        return None
    tail = value.rsplit("@", 1)[-1]
    return tail.lower() if re.fullmatch(r"[0-9a-fA-F]{16}", tail) else None


def normalized_root(value: str | None) -> str | None:
    """Bounded lexical local comparison; no stat/search/secret-bearing path access."""
    if (
        not value
        or len(value) > 4096
        or not os.path.isabs(value)
        or any(ord(c) < 32 or ord(c) == 127 for c in value)
    ):
        return None
    return os.path.normcase(os.path.normpath(value))


class ProjectExpectation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    root: str = Field(min_length=1, max_length=4096)


class ContextCommand(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    type: str = Field(min_length=1, max_length=256)
    params: dict[str, JsonValue] = Field(default_factory=dict)
    unity_instance: str = Field(min_length=1, max_length=256)
    expected_project: ProjectExpectation


@dataclass(frozen=True, slots=True)
class ProjectSelection:
    registry: PluginRegistry
    session_id: str
    websocket: WebSocket
    generation: str
    root: str
    project_hash: str
    user_id: str | None

    def matches(
        self,
        registry: PluginRegistry | None,
        session_id: str,
        websocket: WebSocket,
        session: PluginSession | None,
    ) -> bool:
        """Called under the hub lock, before any reservation/epoch/send."""
        return (
            registry is self.registry
            and session_id == self.session_id
            and websocket.client_state == WebSocketState.CONNECTED
            and websocket.application_state == WebSocketState.CONNECTED
            and websocket is self.websocket
            and session is not None
            and session.project_hash == self.project_hash
            and session.user_id == self.user_id
            and normalized_root(session.project_path) == self.root
            and getattr(websocket.state, "plugin_generation", None) == self.generation
            and getattr(websocket.state, "plugin_session_id", None) == self.session_id
        )


@dataclass(slots=True)
class LocalProjectContext:
    """One immutable selection and one bounded refusal, scoped to one REST request."""

    selection: ProjectSelection | None = None
    refusal: str | None = None
    job_id: str | None = None

    def refuse(
        self, reason: str, params: dict[str, JsonValue] | None = None
    ) -> dict[str, JsonValue]:
        """Terminal structured failure; custom polling must not retry it as transport I/O."""
        self.refusal = self.refusal or reason
        job = params.get("job_id") if params else None
        if (
            isinstance(job, str)
            and 0 < len(job) <= 256
            and all(ord(c) >= 32 and ord(c) != 127 for c in job)
        ):
            self.job_id = job
        data: dict[str, JsonValue] = {"reason": self.refusal, "dispatch": "not_dispatched"}
        if self.job_id is not None:
            data["job_id"] = self.job_id
        return {
            "success": False,
            "error": "Project context could not be verified for this dispatch",
            "hint": "select_instance",
            "data": data,
        }


local_project_context: ContextVar[LocalProjectContext | None] = ContextVar(
    "local_project_context", default=None
)
