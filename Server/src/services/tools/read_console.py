"""
Defines the read_console tool for accessing Unity Editor console messages.
"""

from typing import Annotated, Any, Literal
import hashlib
import json
import re

from fastmcp import Context
from mcp.types import ToolAnnotations
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError, model_validator

from services.registry import mcp_for_unity_tool
from services.tools import get_unity_instance_from_context
from services.tools.utils import coerce_int, coerce_bool, parse_json_payload
from transport.unity_transport import send_with_unity_instance
from transport.legacy.unity_connection import async_send_command_with_retry

ConsoleField = Literal["type", "message", "file", "line", "stackTrace"]
_CONSOLE_FIELDS = TypeAdapter(list[ConsoleField])


class ConsoleTailEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    type: Literal["Error", "Assert", "Warning", "Log", "Exception"]
    message: str = Field(max_length=8192)
    file: str | None = Field(default=None, max_length=1024)
    line: int | None = None
    stackTrace: str | None = Field(default=None, max_length=16384)
    truncated_fields: list[Literal["message", "file", "stackTrace"]] = Field(
        default_factory=list, max_length=3
    )

    @model_validator(mode="after")
    def bounded_text(self):
        for name, limit in (("message", 8192), ("file", 1024), ("stackTrace", 16384)):
            text = getattr(self, name)
            if text is not None and len(text.encode("utf-8")) > limit:
                raise ValueError("Tail text exceeds its byte bound")
        if len(set(self.truncated_fields)) != len(self.truncated_fields):
            raise ValueError("Duplicate truncation field")
        return self


class ConsoleTailItem(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    sequence: int = Field(ge=1, le=2147483647)
    entry: ConsoleTailEntry


class ConsoleTailPage(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    version: Literal[1]
    generation: str = Field(pattern=r"^[a-f0-9]{32}$")
    filter_identity: str = Field(pattern=r"^[a-f0-9]{64}$")
    items: list[ConsoleTailItem] = Field(max_length=200)
    next_cursor: str = Field(pattern=r"^ct1\.[a-f0-9]{32}\.[a-f0-9]{32}$")
    snapshot_rows: int = Field(ge=0, le=2147483647)
    scanned_rows: int = Field(ge=0, le=4096)
    filtered_rows: int = Field(ge=0, le=4096)
    native_entry_reads: int = Field(ge=0, le=4099)
    has_more: bool
    stop_reason: Literal["snapshot_end", "page_size", "row_budget", "text_budget", "payload_budget"]
    loss_unknown: Literal[True]
    continuity: Literal["bounded_anchors"]

    @model_validator(mode="before")
    @classmethod
    def strict_literals(cls, value):
        if isinstance(value, dict) and (
            type(value.get("version")) is not int or type(value.get("loss_unknown")) is not bool
        ):
            raise ValueError("Invalid tail literal type")
        return value

    @model_validator(mode="after")
    def consistent_page(self):
        sequences = [item.sequence for item in self.items]
        if sequences != sorted(set(sequences)) or any(
            value > self.snapshot_rows for value in sequences
        ):
            raise ValueError("Invalid tail sequence")
        if (
            self.scanned_rows != self.filtered_rows + len(self.items)
            or self.native_entry_reads < self.scanned_rows
        ):
            raise ValueError("Invalid tail read counters")
        if type(self.loss_unknown) is not bool or type(self.version) is not int:
            raise ValueError("Invalid tail literal type")
        if self.next_cursor.split(".")[1] != self.generation:
            raise ValueError("Mismatched tail generation")
        return self


def _tail_identity(params: dict[str, Any]) -> str:
    types = params["types"]
    canonical = {
        "types": sorted({"error", "warning", "log"} if "all" in types else set(types)),
        "filter": params.get("filterText", "").encode("utf-8").hex(),
        "includeMcp": params["includeMcpLogs"],
        "includeStack": params["includeStacktrace"],
        "format": "json",
        "fields": sorted(params["fields"]) if "fields" in params else None,
    }
    return hashlib.sha256(
        json.dumps(canonical, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _tail_error(reason: str) -> dict[str, Any]:
    return {
        "success": False,
        "message": "Console tail is unavailable for this request.",
        "data": {"reason": reason, "restart_required": True, "loss_unknown": True},
    }


def _strip_stacktrace_from_list(items: list) -> None:
    """Remove stacktrace fields from a list of log entries."""
    for item in items:
        if isinstance(item, dict) and "stacktrace" in item:
            item.pop("stacktrace", None)


@mcp_for_unity_tool(
    description="Gets messages from or clears the Unity Editor console. Defaults to 10 most recent entries, excluding MCP internal logs; set include_mcp_logs=true for troubleshooting. Use page_size/cursor for paging. Note: For maximum client compatibility, pass count as a quoted string (e.g., '5'). The 'get' action is read-only; 'clear' modifies ephemeral UI state (not project data).",
    annotations=ToolAnnotations(
        title="Read Console",
        # 'clear' wipes the ephemeral Editor console buffer only — Unity still
        # mirrors every entry to the Editor log file — so nothing is destroyed.
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    ),
)
async def read_console(
    ctx: Context,
    action: Annotated[
        Literal["get", "clear"],
        "Get or clear the Unity Editor console. Defaults to 'get' if omitted.",
    ]
    | None = None,
    types: Annotated[
        list[Literal["error", "warning", "log", "all"]] | str,
        "Message types to get (accepts list or JSON string)",
    ]
    | None = None,
    count: Annotated[
        int | str,
        "Max messages to return in non-paging mode (default 10; accepts int or string, e.g., 5 or '5'). Pass 'all' or '*' to return all matches. Ignored when paging with page_size/cursor.",
    ]
    | None = None,
    filter_text: Annotated[str, "Text filter for messages"] | None = None,
    page_size: Annotated[
        int | str, "Page size for paginated console reads. Defaults to 50 when omitted."
    ]
    | None = None,
    cursor: Annotated[int | str, "Opaque cursor for paging (0-based offset). Defaults to 0."]
    | None = None,
    format: Annotated[Literal["plain", "detailed", "json"], "Output format"] | None = None,
    include_stacktrace: Annotated[
        bool | str, "Include stack traces in output (accepts true/false or 'true'/'false')"
    ]
    | None = None,
    fields: Annotated[
        list[ConsoleField] | str,
        "Optional fields for get with json/detailed format; accepts a list or JSON list string. Must include type and message. stackTrace requires include_stacktrace=true. Omit to preserve the full existing entry schema.",
    ]
    | None = None,
    include_mcp_logs: Annotated[
        bool | str,
        "Include MCP internal logger messages (default false). Filtering occurs before count and pagination.",
    ] = False,
    tail_cursor: Annotated[
        str,
        "Opt-in incremental tail: begin starts at the current end; otherwise pass the returned opaque token. Loss cannot be ruled out.",
    ]
    | None = None,
) -> dict[str, Any]:
    # Validate the raw opt-in before legacy count/format defaults or instance lookup.
    tail = tail_cursor is not None
    if tail:
        if (
            not isinstance(tail_cursor, str)
            or len(tail_cursor) > 96
            or (
                tail_cursor != "begin"
                and re.fullmatch(r"ct1\.[a-f0-9]{32}\.[a-f0-9]{32}", tail_cursor) is None
            )
        ):
            return _tail_error("invalid_cursor")
        if (
            count is not None
            or cursor is not None
            or action not in (None, "get")
            or format not in (None, "json")
        ):
            return _tail_error("invalid_tail_options")
        if filter_text is not None and (not isinstance(filter_text, str) or len(filter_text) > 256):
            return _tail_error("invalid_filter")
        try:
            tail_page_size = coerce_int(page_size, default=50)
        except ValueError:
            return _tail_error("invalid_page_size")
        if isinstance(page_size, bool) or not 1 <= tail_page_size <= 200:
            return _tail_error("invalid_page_size")
        format = "json"
    # Get active instance from session state
    # Removed session_state import
    # Set defaults if values are None
    action = action if action is not None else "get"

    # Parse types if it's a JSON string (handles client compatibility issue #561)
    if isinstance(types, str):
        types = parse_json_payload(types)
    # Validate types is a list after parsing
    if types is not None and not isinstance(types, list):
        return {
            "success": False,
            "message": (
                f"types must be a list, got {type(types).__name__}. "
                'If passing as JSON string, use format: \'["error", "warning"]\''
            ),
        }
    if types is not None:
        allowed_types = {"error", "warning", "log", "all"}
        normalized_types = []
        for entry in types:
            if not isinstance(entry, str):
                return {
                    "success": False,
                    "message": f"types entries must be strings, got {type(entry).__name__}",
                }
            normalized = entry.strip().lower()
            if normalized not in allowed_types:
                return {
                    "success": False,
                    "message": (
                        f"invalid types entry '{entry}'. Allowed values: {sorted(allowed_types)}"
                    ),
                }
            normalized_types.append(normalized)
        types = normalized_types
    else:
        types = ["error", "warning", "log"]

    format = format if format is not None else "plain"
    # Coerce booleans defensively (strings like 'true'/'false')

    include_stacktrace = coerce_bool(include_stacktrace, default=False)
    include_mcp_logs = coerce_bool(include_mcp_logs, default=False)
    coerced_page_size = coerce_int(page_size, default=None)
    coerced_cursor = coerce_int(cursor, default=None)

    # Normalize action if it's a string
    if isinstance(action, str):
        action = action.lower()

    selected_fields = None
    if fields is not None:
        try:
            selected_fields = _CONSOLE_FIELDS.validate_python(
                parse_json_payload(fields), strict=True
            )
        except ValidationError:
            return {
                "success": False,
                "message": "fields must be a list of type, message, file, line, or stackTrace.",
            }
        if (
            not selected_fields
            or len(set(selected_fields)) != len(selected_fields)
            or not {"type", "message"}.issubset(selected_fields)
        ):
            return {
                "success": False,
                "message": "fields must be unique and include both type and message.",
            }
        if action != "get" or format not in ("json", "detailed"):
            return {
                "success": False,
                "message": "fields is supported only for get with json or detailed format.",
            }
        if "stackTrace" in selected_fields and not include_stacktrace:
            return {
                "success": False,
                "message": "The stackTrace field requires include_stacktrace=true.",
            }

    if tail:
        tail_params = {
            "tail_cursor": tail_cursor,
            "types": types,
            "format": "json",
            "pageSize": coerce_int(page_size, default=50),
            "includeStacktrace": include_stacktrace,
            "includeMcpLogs": include_mcp_logs,
        }
        if filter_text:
            tail_params["filterText"] = filter_text
        if selected_fields is not None:
            tail_params["fields"] = selected_fields
        try:
            identity = _tail_identity(tail_params)
        except UnicodeEncodeError:
            return _tail_error("invalid_filter")
        instance = await get_unity_instance_from_context(ctx)
        response = await send_with_unity_instance(
            async_send_command_with_retry,
            instance,
            "tail_console",
            tail_params,
            retry_on_reload=False,
        )
        if not isinstance(response, dict) or response.get("success") is not True:
            return response if isinstance(response, dict) else _tail_error("tail_unsupported")
        try:
            page = ConsoleTailPage.model_validate(response.get("data"))
            if page.filter_identity != identity or len(page.items) > tail_params["pageSize"]:
                return _tail_error("invalid_tail_response")
            if tail_cursor != "begin" and page.generation != tail_cursor.split(".")[1]:
                return _tail_error("invalid_tail_response")
            if tail_cursor == "begin" and (page.items or page.scanned_rows or page.has_more):
                return _tail_error("invalid_tail_response")
            if len(json.dumps(response["data"], ensure_ascii=False).encode("utf-8")) > 262144:
                return _tail_error("invalid_tail_response")
        except (ValidationError, UnicodeEncodeError):
            return _tail_error("invalid_tail_response")
        return response

    # Read an integer count without boolean conversion or fractional truncation.
    # Important: leaving count unset previously meant "return all console entries", which can be extremely slow
    # (and can exceed the plugin command timeout when Unity has a large console).
    # To keep the tool responsive by default, we cap the default to a reasonable number of most-recent entries.
    # If a client truly wants everything, it can pass count="all" (or count="*") explicitly.
    read_all = isinstance(count, str) and count.strip().lower() in ("all", "*")
    if read_all:
        count = None
    else:
        count = coerce_int(count)

    if action == "get" and count is None and not read_all:
        count = 10

    # Prepare parameters for the C# handler
    params_dict = {
        "action": action,
        "types": types,
        "count": count,
        "filterText": filter_text,
        "pageSize": coerced_page_size,
        "cursor": coerced_cursor,
        "format": format.lower() if isinstance(format, str) else format,
        "includeStacktrace": include_stacktrace,
    }

    # Remove None values unless it's 'count' (as None might mean 'all')
    params_dict = {k: v for k, v in params_dict.items() if v is not None or k == "count"}

    # Add count back if it was None, explicitly sending null might be important for C# logic
    if "count" not in params_dict:
        params_dict["count"] = None

    if selected_fields is not None:
        params_dict["fields"] = selected_fields
    # Omission uses the Editor's false default, preserving legacy request shape.
    if include_mcp_logs:
        params_dict["includeMcpLogs"] = True

    # Use centralized retry helper with instance routing
    unity_instance = await get_unity_instance_from_context(ctx)
    resp = await send_with_unity_instance(
        async_send_command_with_retry, unity_instance, "read_console", params_dict
    )
    if isinstance(resp, dict) and resp.get("success") and not include_stacktrace:
        # Strip stacktrace fields from returned lines if present
        try:
            data = resp.get("data")
            if isinstance(data, dict):
                for key in ("lines", "items"):
                    if key in data and isinstance(data[key], list):
                        _strip_stacktrace_from_list(data[key])
                        break
            elif isinstance(data, list):
                _strip_stacktrace_from_list(data)
        except Exception:
            pass
    return resp if isinstance(resp, dict) else {"success": False, "message": str(resp)}
