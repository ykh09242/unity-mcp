"""Read-only, explicitly scoped Netcode for GameObjects configuration diagnostics."""

from typing import Annotated, Literal, assert_never

from fastmcp import Context
from mcp.types import ToolAnnotations
from pydantic import BaseModel, ConfigDict, Field, JsonValue, StrictBool, StrictInt

from services.registry import mcp_for_unity_tool
from services.tools import get_unity_instance_from_context
from transport.legacy.unity_connection import async_send_command_with_retry
from transport.unity_transport import send_with_unity_instance


class NetcodeExpectedSettings(BaseModel):
    """Optional scalar expectations compared with the selected scene's managers."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    protocol_version: StrictInt | None = Field(
        default=None, alias="ProtocolVersion", ge=0, le=65535
    )
    tick_rate: StrictInt | None = Field(default=None, alias="TickRate", ge=1, le=4294967295)
    enable_scene_management: StrictBool | None = Field(default=None, alias="EnableSceneManagement")
    force_same_prefabs: StrictBool | None = Field(default=None, alias="ForceSamePrefabs")
    connection_approval: StrictBool | None = Field(default=None, alias="ConnectionApproval")


@mcp_for_unity_tool(
    group="core",
    description=(
        "Inspect local Netcode for GameObjects configuration without starting a network session. "
        "capabilities reports optional package detection/version; preflight requires an explicit "
        "loaded scene_handle and checks managers, active same-scene transports, raw serialized "
        "prefab registrations, root NetworkObject references and supported scalar settings. "
        "expected_settings compares selected values; omitted/null values add no expectation. "
        "Multiple managers compare unspecified settings against the first manager. "
        "Limits: 16 managers, 10000 scene objects, max_prefabs total registrations and 200 "
        "diagnostics; budget overflow fails without reporting readiness. Package absence is explicit. "
        "Static configuration only: no connectivity, peer/build compatibility, hosting, deployment, "
        "runtime prefab or callback guarantee; no implicit installation or configuration edits."
    ),
    annotations=ToolAnnotations(
        title="Manage Netcode", readOnlyHint=True, destructiveHint=False, idempotentHint=True
    ),
)
async def manage_netcode(
    ctx: Context,
    action: Annotated[Literal["capabilities", "preflight"], "Read-only diagnostic action."],
    scene_handle: Annotated[StrictInt | None, "Exact loaded scene handle for preflight."] = None,
    expected_settings: Annotated[
        NetcodeExpectedSettings | None, "Supported NetworkConfig scalar expectations."
    ] = None,
    max_prefabs: Annotated[
        StrictInt, Field(ge=1, le=1000), "Maximum total inspected prefab registrations."
    ] = 1000,
) -> dict[str, JsonValue]:
    """Forward parsed diagnostic inputs through the selected Unity instance transport."""
    params: dict[str, JsonValue] = {"action": action}
    match action:
        case "capabilities":
            pass
        case "preflight":
            if scene_handle is None:
                return {"success": False, "message": "preflight requires an explicit scene_handle."}
            params["scene_handle"] = scene_handle
            params["max_prefabs"] = max_prefabs
            if expected_settings is not None:
                params["expected_settings"] = expected_settings.model_dump(
                    by_alias=True, exclude_none=True
                )
        case unreachable:
            assert_never(unreachable)
    instance = await get_unity_instance_from_context(ctx)
    response = await send_with_unity_instance(
        async_send_command_with_retry, instance, "manage_netcode", params
    )
    # Existing transport JSON envelopes remain authoritative, including native refusals.
    if isinstance(response, dict):
        return response
    return {"success": False, "message": str(response)}
