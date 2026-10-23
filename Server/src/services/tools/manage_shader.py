import base64
from typing import Annotated, Any, Literal

from fastmcp import Context
from pydantic import StrictInt
from mcp.types import ToolAnnotations

from services.registry import mcp_for_unity_tool
from services.tools import get_unity_instance_from_context
from transport.unity_transport import send_with_unity_instance
from transport.legacy.unity_connection import async_send_command_with_retry
from services.tools.pagination import page_integer


@mcp_for_unity_tool(
    group="vfx",
    description=(
        "Manages shader scripts in Unity (create, read, update, delete). Read-only: read, inspect_graph. "
        "read returns a bounded source page (64 KiB by default); follow data.nextOffset with "
        "read_offset while hasMore is true. Offsets and limits count source bytes after the BOM. "
        "inspect_graph takes a full Assets/*.shadergraph path and returns bounded, paged serialized "
        "nodes, edges, properties and keywords (modern MultiJSON GraphData v0-3; 4 MiB files; "
        "at most 100 results per collection per page). This is structural inspection, not package "
        "validation or graph editing. Modifying actions: create, update, delete."
    ),
    annotations=ToolAnnotations(
        title="Manage Shader",
        # Note: 'read' action is non-destructive; 'create', 'update', 'delete' are destructive
        destructiveHint=True,
    ),
)
async def manage_shader(
    ctx: Context,
    action: Annotated[
        Literal["create", "read", "update", "delete", "inspect_graph"],
        "Shader CRUD or read-only serialized Shader Graph inspection.",
    ],
    name: Annotated[str, "Shader name without extension; omitted for inspect_graph."] | None = None,
    path: Annotated[
        str, "Shader directory for CRUD; full .shadergraph asset path for inspect_graph."
    ] = "Assets/",
    contents: Annotated[str, "Shader code for 'create'/'update'"] | None = None,
    page_size: Annotated[int | str, "inspect_graph page size, 1-100 (default 50)."] | None = None,
    page_number: Annotated[int | str, "inspect_graph page number, 1-based."] | None = None,
    read_offset: Annotated[StrictInt | str, "read source byte offset after the BOM (default 0)."]
    | None = None,
    read_limit: Annotated[StrictInt | str, "read page byte limit, 4-1048576 (default 65536)."]
    | None = None,
    read_encoding: Annotated[
        Literal["text", "base64"], "read output encoding; Base64 represents UTF-8 text."
    ]
    | None = None,
) -> dict[str, Any]:
    try:
        # Prepare parameters for Unity
        params = {
            "action": action,
            "name": name,
            "path": path,
        }
        if action == "inspect_graph":
            try:
                params["pageSize"] = page_integer(page_size, 50, 1, 100, "page_size")
                params["pageNumber"] = page_integer(page_number, 1, 1, 2_147_483_647, "page_number")
            except ValueError as exc:
                return {"success": False, "message": str(exc)}
        elif not name:
            return {"success": False, "message": f"Action '{action}' requires parameter 'name'."}

        if action == "read":
            try:
                params["readOffset"] = page_integer(
                    read_offset, 0, 0, 9_223_372_036_854_775_807, "read_offset"
                )
                params["readLimit"] = page_integer(read_limit, 65536, 4, 1048576, "read_limit")
                if read_encoding not in {None, "text", "base64"}:
                    raise ValueError("read_encoding must be text or base64")
                params["readEncoding"] = read_encoding or "text"
            except (ValueError, TypeError) as exc:
                return {"success": False, "message": str(exc)}

        # Base64 encode the contents if they exist to avoid JSON escaping issues
        if contents is not None:
            if action in ["create", "update"]:
                # Encode content for safer transmission
                params["encodedContents"] = base64.b64encode(contents.encode("utf-8")).decode(
                    "utf-8"
                )
                params["contentsEncoded"] = True
            else:
                params["contents"] = contents

        # Remove None values so they don't get sent as null
        params = {k: v for k, v in params.items() if v is not None}

        # Resolve the instance only after local validation and payload preparation.
        unity_instance = await get_unity_instance_from_context(ctx)

        # Send command via centralized retry helper with instance routing
        response = await send_with_unity_instance(
            async_send_command_with_retry, unity_instance, "manage_shader", params
        )

        # Process response from Unity
        if isinstance(response, dict) and response.get("success"):
            # If the response contains base64 encoded content, decode it
            data = response.get("data")
            if isinstance(data, dict) and data.get("contentsEncoded") and read_encoding != "base64":
                decoded_contents = base64.b64decode(response["data"]["encodedContents"]).decode(
                    "utf-8"
                )
                response["data"]["contents"] = decoded_contents
                del response["data"]["encodedContents"]
                del response["data"]["contentsEncoded"]

            return {
                "success": True,
                "message": response.get("message", "Operation successful."),
                "data": response.get("data"),
            }
        return (
            response if isinstance(response, dict) else {"success": False, "message": str(response)}
        )

    except Exception as e:
        # Handle Python-side errors (e.g., connection issues)
        return {"success": False, "message": f"Python error managing shader: {str(e)}"}
