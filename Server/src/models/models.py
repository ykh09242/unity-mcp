import json
from typing import Annotated, Any
from datetime import datetime
from pydantic import BaseModel, Field, StrictBool, StrictFloat, StrictInt, StrictStr, TypeAdapter, ValidationInfo, field_validator


def parse_tool_parameter_default(value: str | None, param_type: str | None) -> object:
    """Parse legacy descriptor strings without accepting malformed scalar defaults."""
    if value is None:
        return None
    value = TypeAdapter(StrictStr).validate_python(value)
    match (param_type or "string").lower():
        case "integer" | "int":
            return TypeAdapter(StrictInt).validate_python(int(value))
        case "number" | "float" | "double":
            return TypeAdapter(Annotated[StrictFloat, Field(allow_inf_nan=False)]).validate_python(float(value))
        case "bool" | "boolean":
            if value.lower() not in ("true", "false"):
                raise ValueError("Boolean defaults must be 'true' or 'false'")
            return value.lower() == "true"
        case "array" | "list":
            return TypeAdapter(list).validate_python(json.loads(value), strict=True)
        case "object" | "dict":
            return TypeAdapter(dict).validate_python(json.loads(value), strict=True)
        case _:
            return value


class MCPResponse(BaseModel):
    success: bool
    message: str | None = None
    error: str | None = None
    data: Any | None = None
    # Optional hint for clients about how to handle the response.
    # Supported values:
    #   - "retry": Unity is temporarily reloading; call should be retried politely.
    hint: str | None = None


class ToolParameterModel(BaseModel):
    name: str
    description: str | None = None
    type: str = Field(default="string")
    required: StrictBool = Field(default=True)
    default_value: str | None = None

    @field_validator("default_value")
    @classmethod
    def validate_default_value(cls, value: str | None, info: ValidationInfo) -> str | None:
        parse_tool_parameter_default(value, info.data.get("type", "string"))
        return value


class ToolDefinitionModel(BaseModel):
    name: str
    description: str | None = None
    structured_output: StrictBool | None = True
    requires_polling: StrictBool | None = False
    poll_action: str | None = "status"
    # Zero selects the server default; plugins cannot extend the server lifetime.
    max_poll_seconds: StrictInt = Field(default=0, ge=0, le=600)
    parameters: list[ToolParameterModel] = Field(default_factory=list)


class UnityInstanceInfo(BaseModel):
    """Information about a Unity Editor instance"""
    id: str  # "ProjectName@hash" or fallback to hash
    name: str  # Project name extracted from path
    path: str  # Full project path (Assets folder)
    hash: str  # 8-char hash of project path
    port: int  # TCP port
    status: str  # "running", "reloading", "offline"
    last_heartbeat: datetime | None = None
    unity_version: str | None = None
    project_scoped_tools: bool = False

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization"""
        return {
            "id": self.id,
            "name": self.name,
            "path": self.path,
            "hash": self.hash,
            "port": self.port,
            "status": self.status,
            "last_heartbeat": self.last_heartbeat.isoformat() if self.last_heartbeat else None,
            "unity_version": self.unity_version,
            "project_scoped_tools": self.project_scoped_tools,
        }
