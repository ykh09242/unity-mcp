"""Named supported simulation settings with revision checks and conditional restore."""

from typing import Annotated, Final, Literal, get_args

from fastmcp import Context
from mcp.types import ToolAnnotations
from pydantic import JsonValue

from services.registry import mcp_for_unity_tool
from services.tools import get_unity_instance_from_context
from transport.legacy.unity_connection import async_send_command_with_retry
from transport.unity_transport import send_with_unity_instance

SimulationProfileAction = Literal[
    "capabilities",
    "capture",
    "list",
    "get",
    "diff",
    "validate",
    "apply",
    "restore",
    "receipt",
    "delete",
]
SimulationDomain = Literal["time", "physics3d", "physics2d", "audio", "input"]
ALL_ACTIONS: Final = get_args(SimulationProfileAction)
MUTATING_ACTIONS: Final = frozenset({"capture", "apply", "restore", "delete"})
NAMED_ACTIONS: Final = frozenset({"capture", "get", "diff", "validate", "apply", "delete"})


@mcp_for_unity_tool(
    group="testing",
    description=(
        "Capture named Time/Physics3D/Physics2D/Audio/Input configuration profiles, inspect "
        "per-domain applicability, diff/validate against current settings, explicitly apply, "
        "and conditionally restore confirmed changes. capabilities lists exact supported keys. "
        "capture optionally stores validated settings replacements without applying globals. "
        "apply requires profile_revision and current_revision returned by validate/diff. "
        "restore requires a retained receipt_id and preserves external writer drift, owner "
        "changes and uncertain writes. No cross-domain transaction, automatic simulation, "
        "input injection, hardware audibility, persistent Undo or asset save is promised. "
        "Profiles/receipts are bounded project-local Library metadata. delete removes a named "
        "profile plus its receipts and does not restore settings; restore first when needed."
    ),
    annotations=ToolAnnotations(title="Manage Simulation Profiles", destructiveHint=True),
)
async def manage_simulation_profiles(
    ctx: Context,
    action: Annotated[SimulationProfileAction, "Explicit named profile action."],
    name: Annotated[str | None, "Exact profile name, 1..64 visible characters."] = None,
    domains: Annotated[
        list[SimulationDomain] | None,
        "Capture domains; omitted captures currently applicable supported domains.",
    ] = None,
    settings: Annotated[
        dict[str, JsonValue] | None,
        "Optional supported replacement values stored by capture, at most 32 flat keys "
        "such as time.timeScale, physics3d.gravity, audio.volume. No global application here.",
    ] = None,
    profile_revision: Annotated[
        str | None, "Required for apply or replacement capture; read from get/validate/diff."
    ] = None,
    current_revision: Annotated[
        str | None, "Required for apply; exact current-state revision returned by validate/diff."
    ] = None,
    receipt_id: Annotated[str | None, "Retained apply receipt for receipt/restore."] = None,
    offset: Annotated[int | None, "Nonnegative list offset."] = None,
    limit: Annotated[int | None, "List page size 1..32, default 20."] = None,
) -> dict[str, JsonValue]:
    """Route profile requests without retrying mutations across domain reload."""
    if action not in ALL_ACTIONS:
        return {"success": False, "message": f"Unknown simulation profile action: {action}."}
    if action in NAMED_ACTIONS and (
        name is None
        or not name.strip()
        or len(name) > 64
        or any(ord(character) < 32 or ord(character) == 127 for character in name)
    ):
        return {"success": False, "message": "name must contain 1..64 visible characters."}
    if action in {"restore", "receipt"} and not receipt_id:
        return {"success": False, "message": "receipt_id is required."}
    if action == "apply" and (not profile_revision or not current_revision):
        return {
            "success": False,
            "message": "apply requires profile_revision and current_revision.",
        }
    if domains is not None and (
        not 1 <= len(domains) <= 5
        or any(domain not in get_args(SimulationDomain) for domain in domains)
    ):
        return {"success": False, "message": "domains must contain 1..5 supported domain names."}
    if settings is not None and len(settings) > 32:
        return {"success": False, "message": "settings is limited to 32 supported keys."}
    if limit is not None and (type(limit) is not int or not 1 <= limit <= 32):
        return {"success": False, "message": "limit must be an integer between 1 and 32."}
    if offset is not None and (type(offset) is not int or not 0 <= offset <= 2_147_483_647):
        return {"success": False, "message": "offset must be a nonnegative 32-bit integer."}
    values: dict[str, JsonValue] = {
        "action": action,
        "name": name,
        "domains": domains,
        "settings": settings,
        "profile_revision": profile_revision,
        "current_revision": current_revision,
        "receipt_id": receipt_id,
        "offset": offset,
        "limit": limit,
    }
    params = {key: value for key, value in values.items() if value is not None}
    instance = await get_unity_instance_from_context(ctx)
    response = await send_with_unity_instance(
        async_send_command_with_retry,
        instance,
        "manage_simulation_profiles",
        params,
        retry_on_reload=action not in MUTATING_ACTIONS,
    )
    return response if isinstance(response, dict) else {"success": False, "message": str(response)}
