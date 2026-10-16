"""
Defines the execute_menu_item tool for executing and reading Unity Editor menu items.
"""

from typing import Annotated, Any, Literal

from fastmcp import Context
from mcp.types import ToolAnnotations
from pydantic import Field, StrictInt, StrictStr

from models import MCPResponse
from services.registry import mcp_for_unity_tool
from services.tools import get_unity_instance_from_context
from transport.unity_transport import send_with_unity_instance
from transport.legacy.unity_connection import async_send_command_with_retry


@mcp_for_unity_tool(
    description=(
        "Inspect menu existence/availability evidence or invoke a menu by exact path. "
        "Default execution reports invocation only; arbitrary completion and modal handling "
        "are not guaranteed. Only Window/General/Console supports console_window_open observation. "
        "CONTEXT execution is unsupported and File/Quit is blocked. Inspection and observation "
        "require native support; unsupported peers refuse without falling back to execution."
    ),
    annotations=ToolAnnotations(
        title="Execute Menu Item",
        destructiveHint=True,
    ),
)
async def execute_menu_item(
    ctx: Context,
    menu_path: Annotated[
        StrictStr,
        "Exact menu path, maximum 1024 UTF-16 characters/4096 UTF-8 bytes; no NUL/newlines.",
    ]
    | None = None,
    action: Annotated[
        Literal["execute", "inspect", "exists"] | None,
        Field(
            description="Default execute; inspect (or exists alias) does not invoke the command."
        ),
    ] = None,
    wait_for: Annotated[
        Literal["none", "console_window_open"] | None,
        Field(
            description="Optional allowlisted window-presence observation, not general menu completion."
        ),
    ] = None,
    timeout_ms: Annotated[
        StrictInt | None,
        Field(
            ge=1,
            le=10000,
            description="Observation ceiling, default 2000ms; permitted only with a supported wait_for.",
        ),
    ] = None,
) -> MCPResponse:
    # Validate direct callers as well as the registered SDK before resolving an instance.
    invalid = None
    if menu_path is not None:
        if not isinstance(menu_path, str):
            invalid = "menu_path must be a string."
        else:
            try:
                if (
                    len(menu_path.encode("utf-16-le")) // 2 > 1024
                    or len(menu_path.encode("utf-8")) > 4096
                    or any(character in menu_path for character in ("\0", "\r", "\n"))
                ):
                    invalid = "menu_path exceeds its bounds or contains NUL/newlines."
            except UnicodeEncodeError:
                invalid = "menu_path must contain valid Unicode."
    if action is not None and action not in ("execute", "inspect", "exists"):
        invalid = "action must be execute, inspect or exists."
    if wait_for is not None and wait_for not in ("none", "console_window_open"):
        invalid = "wait_for must be none or console_window_open."
    if wait_for == "console_window_open" and (
        action not in (None, "execute") or menu_path != "Window/General/Console"
    ):
        invalid = "console_window_open is supported only for Window/General/Console execution."
    if timeout_ms is not None and (
        type(timeout_ms) is not int
        or not 1 <= timeout_ms <= 10000
        or wait_for != "console_window_open"
    ):
        invalid = "timeout_ms must be integer 1..10000 with a supported wait_for."
    if invalid:
        return MCPResponse(success=False, error=invalid)
    unity_instance = await get_unity_instance_from_context(ctx)
    params_dict: dict[str, Any] = {
        "menuPath": menu_path,
        "action": action,
        "wait_for": wait_for,
        "timeout_ms": timeout_ms,
    }
    params_dict = {k: v for k, v in params_dict.items() if v is not None}
    command = (
        "inspect_menu_item"
        if action in ("inspect", "exists")
        else "execute_menu_item_observed"
        if wait_for == "console_window_open"
        else "execute_menu_item"
    )
    result = await send_with_unity_instance(
        async_send_command_with_retry,
        unity_instance,
        command,
        params_dict,
        retry_on_reload=command == "inspect_menu_item",
    )
    return MCPResponse(**result) if isinstance(result, dict) else result
