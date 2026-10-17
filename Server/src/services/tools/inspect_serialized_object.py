"""Bounded native serialized-property projections; no fallback to reflection readers."""

import re
from typing import Annotated, Any, Literal

from fastmcp import Context
from mcp.types import ToolAnnotations
from pydantic import Field, StrictBool, StrictInt, StrictStr
from services.registry import mcp_for_unity_tool
from services.tools import get_unity_instance_from_context
from services.tools.preflight import preflight
from transport.legacy.unity_connection import async_send_command_with_retry
from transport.unity_transport import send_with_unity_instance

_OMITTED = object()
_DEFAULT = Field(default_factory=lambda: _OMITTED)


def _omitted(value: Any) -> bool:
    return value is _OMITTED or value is _DEFAULT


def _path(value: Any, *, empty: bool = False) -> bool:
    if type(value) is not str or (not empty and not value):
        return False
    try:
        if len(value.encode("utf-16-le")) > 1024:
            return False
        if not value:
            return empty
        parts = value.split(".")
        hops = index = 0
        while index < len(parts):
            part = parts[index]
            if index > 0 and part == "Array" and index + 1 < len(parts):
                suffix = parts[index + 1]
                if suffix != "size":
                    match = re.fullmatch(r"data\[(0|[1-9][0-9]*)\]", suffix)
                    if match is None or int(match[1]) > 2147483647:
                        return False
                index += 1
            elif not part or any(
                char.isspace() or ord(char) < 32 or 127 <= ord(char) <= 159 or char in "[]"
                for char in part
            ):
                return False
            index += 1
            hops += 1
        return hops <= 16
    except UnicodeEncodeError:
        return False


@mcp_for_unity_tool(
    unity_target="inspect_serialized_object_v1",
    group="scripting_ext",
    description=(
        "Inspect one exact Component or ScriptableObject instance ID through SerializedObject. "
        "The initial Unity ID lookup MAY load a previously unloaded asset and invoke OnEnable or "
        "serialization callbacks before type/context admission. No explicit asset search/import/save "
        "is performed. Fields returns one immediate level; value reads one canonical property; array "
        "reads only the requested slice. Use canonical paths such as groups.Array.data[2].values. "
        "Managed and object references remain terminal descriptors. Pages are live, not snapshots: "
        "restart after edits/reload; IDs, field paths and array indices are not persistent identity. "
        "Maximum 64 records, 16 path hops, 2048 charged managed operations and 65536 response bytes. "
        "These limits do not bound synchronous Unity serialization or native lookup complexity. "
        "Native editability is separate from setter-specific supported input shapes; enum names are "
        "recommended for component writes. Requires a compatible Editor; no legacy fallback or reload retry."
    ),
    annotations=ToolAnnotations(
        title="Inspect Serialized Object",
        # Initial ID lookup can load an asset and invoke user callbacks.
        # Those callbacks may mutate the environment, so keep conservative hints.
        readOnlyHint=False,
        destructiveHint=True,
        openWorldHint=False,
    ),
)
async def inspect_serialized_object(
    ctx: Context,
    instance_id: Annotated[StrictInt, Field(ge=-2147483648, le=2147483647)],
    mode: Literal["fields", "value", "array"] = "fields",
    property_path: Annotated[StrictStr, Field(max_length=512)] = "",
    page_size: Annotated[StrictInt, Field(ge=1, le=64)] = _DEFAULT,
    after_path: Annotated[StrictStr, Field(max_length=512)] = _DEFAULT,
    start_index: Annotated[StrictInt, Field(ge=0, le=2147483647)] = _DEFAULT,
    include_values: StrictBool = True,
) -> dict[str, Any]:
    def invalid(message):
        return {
            "success": False,
            "code": "invalid_parameters",
            "error": "invalid_parameters",
            "data": {"reason": "invalid_parameters", "message": message},
        }

    if (
        type(instance_id) is not int
        or instance_id == 0
        or not -2147483648 <= instance_id <= 2147483647
    ):
        return invalid("instance_id must be a nonzero Int32 integer.")
    if mode not in ("fields", "value", "array") or not _path(property_path, empty=mode == "fields"):
        return invalid("Use fields/value/array and a canonical path of at most 512 UTF-16 units.")
    if type(include_values) is not bool:
        return invalid("include_values must be a boolean.")
    if mode != "fields" and not _omitted(after_path):
        return invalid("after_path applies only to fields.")
    if mode != "array" and not _omitted(start_index):
        return invalid("start_index applies only to array.")
    if mode == "value" and not _omitted(page_size):
        return invalid("page_size does not apply to value.")
    params = {
        "instance_id": instance_id,
        "mode": mode,
        "property_path": property_path,
        "include_values": include_values,
    }
    if mode != "value":
        size = 32 if _omitted(page_size) else page_size
        if type(size) is not int or not 1 <= size <= 64:
            return invalid("page_size must be an integer from 1 through 64.")
        params["page_size"] = size
    if mode == "fields" and not _omitted(after_path):
        if not _path(after_path):
            return invalid(
                "after_path must be a nonempty canonical path of at most 512 UTF-16 units."
            )
        params["after_path"] = after_path
    if mode == "array":
        start = 0 if _omitted(start_index) else start_index
        if type(start) is not int or not 0 <= start <= 2147483647:
            return invalid("start_index must be a nonnegative Int32 integer.")
        params["start_index"] = start
    instance = await get_unity_instance_from_context(ctx)
    gate = await preflight(ctx, wait_for_no_compile=True, refresh_if_dirty=False)
    if gate is not None:
        return gate.model_dump()
    return await send_with_unity_instance(
        async_send_command_with_retry,
        instance,
        "inspect_serialized_object_v1",
        params,
        retry_on_reload=False,
    )
