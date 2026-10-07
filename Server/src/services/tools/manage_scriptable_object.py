"""
Tool wrapper for managing ScriptableObject assets via Unity MCP.

Unity-side handler: MCPForUnity.Editor.Tools.ManageScriptableObject
Command name: "manage_scriptable_object"
Actions:
  - create: create an SO asset (optionally with patches)
  - modify: apply serialized property patches to an existing SO asset
"""

from __future__ import annotations

import logging
import re
from typing import Annotated, Any, Literal

from fastmcp import Context
from mcp.types import ToolAnnotations

from services.registry import mcp_for_unity_tool
from services.tools import get_unity_instance_from_context
from services.tools.utils import coerce_bool, coerce_int, parse_json_payload
from transport.unity_transport import send_with_unity_instance
from transport.legacy.unity_connection import async_send_command_with_retry

logger = logging.getLogger("mcp-for-unity-server")


def _array_numeric_error(patches: list[Any]) -> str | None:
    """Unity knows existing sizes; only reject impossible numeric domains here."""
    for patch in patches:
        if not isinstance(patch, dict):
            continue
        path = patch.get("propertyPath", patch.get("property_path", patch.get("path", "")))
        if not isinstance(path, str):
            continue
        for digits in re.findall(r"\[(-?\d+)\]", path):
            try:
                index = int(digits)
                if index < 0 or index >= 2_147_483_647:
                    return "Array index is outside the supported Int32 range."
            except ValueError:
                return "Array index is outside the supported Int32 range."
        op = patch.get("op") or "set"
        if not isinstance(op, str):
            continue
        if op.strip().lower() != "array_resize" and not (
            op.strip().lower() == "set" and path.endswith(".Array.size")
        ):
            continue
        value = patch.get("value")
        try:
            size = coerce_int(value)
            if size is None or size < 0 or size > 2_147_483_647:
                raise ValueError
        except (ValueError, OverflowError):
            return "Array size must be a non-negative Int32 value."
    return None


@mcp_for_unity_tool(
    group="scripting_ext",
    description="Creates and modifies ScriptableObject assets using Unity SerializedObject property paths.",
    annotations=ToolAnnotations(
        title="Manage Scriptable Object",
        destructiveHint=True,
    ),
)
async def manage_scriptable_object(
    ctx: Context,
    action: Annotated[Literal["create", "modify"], "Action to perform: create or modify."],
    # --- create params ---
    type_name: Annotated[
        str | None, "Namespace-qualified ScriptableObject type name (for create)."
    ] = None,
    folder_path: Annotated[str | None, "Target folder under Assets/... (for create)."] = None,
    asset_name: Annotated[str | None, "Asset file name without extension (for create)."] = None,
    overwrite: Annotated[
        bool | str | None, "If true, overwrite existing asset at same path (for create)."
    ] = None,
    # --- modify params ---
    target: Annotated[
        dict[str, Any] | str | None, "Target asset reference {guid|path} (for modify)."
    ] = None,
    # --- shared ---
    patches: Annotated[
        list[dict[str, Any]] | str | None,
        "Patch list (or JSON string) to apply. "
        'For object references: use {"ref": {"guid": "..."}} or {"value": {"guid": "..."}}. '
        'For Sprite sub-assets: include "spriteName" in the ref/value object. '
        "Single-sprite textures auto-resolve from guid/path alone. "
        "Unity preflights growth: 1,048,576 elements per growing array and "
        "2,097,152 added serialized elements/fields per request, including nested copies.",
    ] = None,
    # --- validation ---
    dry_run: Annotated[
        bool | str | None, "If true, validate patches without applying (modify only)."
    ] = None,
) -> dict[str, Any]:
    flags = {}
    for field, value in (("overwrite", overwrite), ("dry_run", dry_run)):
        try:
            flags[field] = coerce_bool(value)
        except ValueError as exc:
            return {"success": False, "message": f"manage_scriptable_object: '{field}': {exc}"}
    # Tolerate JSON-string payloads (LLMs sometimes stringify complex objects)
    parsed_target = parse_json_payload(target)
    parsed_patches = parse_json_payload(patches)

    if parsed_target is not None and not isinstance(parsed_target, dict):
        return {
            "success": False,
            "message": "manage_scriptable_object: 'target' must be an object {guid|path} (or JSON string of such).",
        }

    if parsed_patches is not None and not isinstance(parsed_patches, list):
        return {
            "success": False,
            "message": "manage_scriptable_object: 'patches' must be a list (or JSON string of a list).",
        }

    if parsed_patches is not None:
        numeric_error = _array_numeric_error(parsed_patches)
        if numeric_error:
            return {"success": False, "message": numeric_error}

    unity_instance = await get_unity_instance_from_context(ctx)

    params: dict[str, Any] = {
        "action": action,
        "typeName": type_name,
        "folderPath": folder_path,
        "assetName": asset_name,
        "overwrite": flags["overwrite"],
        "target": parsed_target,
        "patches": parsed_patches,
        "dryRun": flags["dry_run"],
    }

    # Remove None values to keep Unity handler simpler
    params = {k: v for k, v in params.items() if v is not None}

    response = await send_with_unity_instance(
        async_send_command_with_retry,
        unity_instance,
        "manage_scriptable_object",
        params,
    )
    logger.info("manage_scriptable_object response received")
    return (
        response
        if isinstance(response, dict)
        else {"success": False, "message": "Unexpected response from Unity."}
    )
