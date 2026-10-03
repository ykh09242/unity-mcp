from typing import Any
from pydantic import BaseModel, Field, field_validator
from models.models import ToolDefinitionModel

# Outgoing (Server -> Plugin)


class WelcomeMessage(BaseModel):
    type: str = "welcome"
    serverTimeout: int
    keepAliveInterval: int


class RegisteredMessage(BaseModel):
    type: str = "registered"
    session_id: str


class ExecuteCommandMessage(BaseModel):
    type: str = "execute"
    id: str
    name: str
    params: dict[str, Any]
    timeout: float


class PingMessage(BaseModel):
    """Server-initiated ping to detect dead connections."""
    type: str = "ping"

# Incoming (Plugin -> Server)


class RegisterMessage(BaseModel):
    type: str = "register"
    project_name: str = Field(default="Unknown Project", max_length=256)
    project_hash: str = Field(max_length=256)
    unity_version: str = Field(default="Unknown", max_length=64)
    project_path: str | None = Field(default=None, max_length=4096)

    @field_validator("project_name", "project_hash")
    @classmethod
    def reject_log_control_characters(cls, value: str) -> str:
        if any(ord(char) < 32 or 0x7F <= ord(char) <= 0x9F or char in "\u2028\u2029" for char in value):
            raise ValueError("Project identifiers must not contain control characters or line separators")
        return value


class RegisterToolsMessage(BaseModel):
    type: str = "register_tools"
    tools: list[ToolDefinitionModel] = Field(max_length=256)


class PongMessage(BaseModel):
    type: str = "pong"
    session_id: str | None = None


class CommandResultMessage(BaseModel):
    type: str = "command_result"
    id: str
    result: dict[str, Any] = Field(default_factory=dict)

# Session Info (API response)


class SessionDetails(BaseModel):
    project: str
    hash: str
    unity_version: str
    connected_at: str


class SessionList(BaseModel):
    sessions: dict[str, SessionDetails]
