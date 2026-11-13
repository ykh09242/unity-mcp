"""Read an oversized MCP response without repeating the originating operation."""

from typing import Annotated

from fastmcp import Context
from mcp_types import ToolAnnotations
from pydantic import Field, JsonValue

from models.response_limits import MAX_RESPONSE_BYTES
from services.registry import mcp_for_unity_tool
from transport.response_delivery import capture_response_delivery, response_lookup_selector


@mcp_for_unity_tool(
    group=None,
    unity_target=None,
    description="Reads one fixed 256 KiB base64 page of a retained oversized MCP response. "
    "Use the returned next_offset until null, concatenate decoded bytes, verify sha256, then "
    "decode UTF-8 JSON. Handles expire after 10 minutes; release=true discards the result. "
    "Requires the original server, principal, project and legacy session. Never repeats an operation.",
    annotations=ToolAnnotations(
        title="Read Response",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    ),
)
async def read_response(
    ctx: Context,
    response_id: Annotated[str, Field(pattern=r"^[0-9a-f]{32}$", max_length=32)],
    offset: Annotated[int, Field(strict=True, ge=0, le=MAX_RESPONSE_BYTES)] = 0,
    release: Annotated[bool, Field(strict=True)] = False,
) -> dict[str, JsonValue]:
    """Authorize before looking up bytes; absent, expired and foreign handles agree."""
    selector = response_lookup_selector.get()
    delivery = await capture_response_delivery(ctx, lookup_selector=selector)
    expected_instance = selector if selector is not None and "@" in selector else None
    if delivery is not None:
        if release:
            if delivery.store.release(
                response_id, delivery.authority, expected_instance=expected_instance
            ):
                return {"success": True, "data": {"state": "released", "response_id": response_id}}
        else:
            try:
                page = delivery.store.read(
                    response_id,
                    delivery.authority,
                    offset=offset,
                    expected_instance=expected_instance,
                )
            except ValueError:
                return {"success": False, "error": "response_offset_invalid"}
            if page is not None:
                return {"success": True, "data": page}
    return {"success": False, "error": "response_not_found"}
