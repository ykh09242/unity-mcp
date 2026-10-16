from typing import Annotated, Any, Optional

from fastmcp import Context
from mcp.types import ToolAnnotations
from pydantic import Field, StrictInt

from services.registry import mcp_for_unity_tool
from services.tools import get_unity_instance_from_context
from transport.unity_transport import send_with_unity_instance
from transport.legacy.unity_connection import async_send_command_with_retry

ALL_ACTIONS = ["get_type", "get_member", "search"]

VALID_SCOPES = ["unity", "packages", "project", "all"]
VALID_TYPE_KINDS = ["class", "interface", "struct", "enum", "delegate"]


async def _send_reflect_command(
    ctx: Context,
    params_dict: dict[str, Any],
) -> dict[str, Any]:
    unity_instance = await get_unity_instance_from_context(ctx)
    result = await send_with_unity_instance(
        async_send_command_with_retry, unity_instance, "unity_reflect", params_dict
    )
    return result if isinstance(result, dict) else {"success": False, "message": str(result)}


@mcp_for_unity_tool(
    group="docs",
    description=(
        "Inspect Unity's live C# API via reflection. Use this to verify that classes, "
        "methods, and properties exist before writing C# code — training data may be "
        "wrong or outdated.\n\n"
        "Actions:\n"
        "- get_type: Member summary (names only) for a class. Requires class_name.\n"
        "- get_member: Full signature detail for one member. Requires class_name + member_name.\n"
        "- search: Ranked public type search across loaded assemblies. Requires query. "
        "Optional scope, assembly, type_kind, page_size (default 25, maximum 100), cursor. "
        "Repeat the same inputs with next_cursor within five minutes; reload/catalog changes "
        "require restarting. complete=false reports assemblies that could not be enumerated. "
        "scope retains its assembly-name heuristics; assembly is an additional case-insensitive filter. "
        "Cursor continuation requires the original assembly spelling exactly, including case."
    ),
    annotations=ToolAnnotations(
        title="Unity Reflect",
        readOnlyHint=True,
        destructiveHint=False,
    ),
)
async def unity_reflect(
    ctx: Context,
    action: Annotated[str, "The reflection action to perform."],
    class_name: Annotated[Optional[str], "Fully qualified or simple C# class name."] = None,
    member_name: Annotated[Optional[str], "Method, property, or field name to inspect."] = None,
    query: Annotated[Optional[str], "Search query for type name search."] = None,
    scope: Annotated[
        Optional[str], "Assembly scope for search: unity, packages, project, all."
    ] = None,
    page_size: Annotated[
        StrictInt | None,
        Field(
            ge=1,
            le=100,
            description="Search page size 1..100; omitted uses 25. Repeat the same value for cursor continuation.",
        ),
    ] = None,
    cursor: Annotated[
        str | None,
        Field(
            description="Opaque next_cursor from the same Editor/catalog and identical search inputs. Maximum 1024 ASCII characters; expires five minutes after the first page."
        ),
    ] = None,
    assembly: Annotated[
        str | None,
        Field(
            description="Search filter: exact simple or full assembly identity, case insensitive; intersects scope. Maximum 256 characters. With cursor, repeat the original spelling exactly, including case."
        ),
    ] = None,
    type_kind: Annotated[
        str | None,
        Field(
            description="Search filter: class, interface, struct, enum, or delegate. Delegates are excluded from the class filter."
        ),
    ] = None,
) -> dict[str, Any]:
    action_lower = action.lower()
    if action_lower not in ALL_ACTIONS:
        return {
            "success": False,
            "message": f"Unknown action '{action}'. Valid actions: {', '.join(ALL_ACTIONS)}",
        }

    if action_lower != "search" and any(
        value is not None for value in (page_size, cursor, assembly, type_kind)
    ):
        return {
            "success": False,
            "message": "page_size, cursor, assembly and type_kind require search.",
        }

    # Validate required params per action
    if action_lower == "get_type":
        if not class_name:
            return {
                "success": False,
                "message": "get_type requires class_name.",
            }
    elif action_lower == "get_member":
        if not class_name or not member_name:
            return {
                "success": False,
                "message": "get_member requires class_name and member_name.",
            }
    elif action_lower == "search":
        if not isinstance(query, str) or not query.strip():
            return {
                "success": False,
                "message": "search requires query.",
            }
        if len(query) > 256:
            return {"success": False, "message": "query exceeds 256 characters."}
        if page_size is not None and (type(page_size) is not int or not 1 <= page_size <= 100):
            return {"success": False, "message": "page_size must be an integer from 1 to 100."}
        if assembly is not None and (
            not isinstance(assembly, str) or not assembly.strip() or len(assembly) > 256
        ):
            return {
                "success": False,
                "message": "assembly must be a nonempty string of at most 256 characters.",
            }
        if type_kind is not None and type_kind not in VALID_TYPE_KINDS:
            return {"success": False, "message": f"type_kind must be one of {VALID_TYPE_KINDS}."}
        if cursor is not None and (
            not isinstance(cursor, str) or not cursor or len(cursor) > 1024 or not cursor.isascii()
        ):
            return {
                "success": False,
                "message": "cursor must be a nonempty ASCII string of at most 1024 characters.",
            }

    params_dict: dict[str, Any] = {"action": action_lower}

    if class_name is not None:
        params_dict["class_name"] = class_name
    if member_name is not None:
        params_dict["member_name"] = member_name
    if query is not None:
        params_dict["query"] = query
    if action_lower == "search" and scope is not None:
        if scope not in VALID_SCOPES:
            return {
                "success": False,
                "message": f"Invalid scope '{scope}'. Valid scopes: {', '.join(VALID_SCOPES)}",
            }
        params_dict["scope"] = scope

    if action_lower == "search":
        params_dict.update(
            {
                key: value
                for key, value in {
                    "page_size": page_size,
                    "cursor": cursor,
                    "assembly": assembly,
                    "type_kind": type_kind,
                }.items()
                if value is not None
            }
        )

    return await _send_reflect_command(ctx, params_dict)
