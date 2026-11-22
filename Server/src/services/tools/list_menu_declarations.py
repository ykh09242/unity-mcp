"""List managed MenuItem declaration paths through the selected Editor's resource."""

from fastmcp import Context
from mcp.types import ToolAnnotations

from services.registry import mcp_for_unity_tool
from services.registry.action_catalog import OBSERVATION_META
from services.registry.capability_catalog import MENU_DECLARATIONS_TOOL, MENU_DECLARATIONS_URI
from services.registry.integration_action_guidance import MENU_INVENTORY_OBSERVATION
from services.resource_inventory import read_inventory


@mcp_for_unity_tool(
    name=MENU_DECLARATIONS_TOOL,
    group="core",
    unity_target=None,
    description=(
        "List managed MenuItem declarations in the selected Editor to find a command's exact menu path. "
        "Match the returned strings by name. Some native entries have no managed declaration. "
        "Reading the list does not evaluate availability or invoke commands. "
        f"Equivalent to {MENU_DECLARATIONS_URI}."
    ),
    annotations=ToolAnnotations(
        title="List Menu Declarations",
        readOnlyHint=True,
        destructiveHint=False,
        openWorldHint=False,
    ),
    meta={OBSERVATION_META: MENU_INVENTORY_OBSERVATION},
)
async def list_menu_declarations(ctx: Context) -> dict:
    return await read_inventory(ctx, MENU_DECLARATIONS_URI, "menu declaration inventory")
