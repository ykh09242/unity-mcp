import base64
from contextvars import copy_context
from functools import partial
import logging
import re
from typing import Annotated, Any

from fastmcp import Context
from mcp.types import ToolAnnotations

from services.registry import mcp_for_unity_tool
from services.tools import get_unity_instance_from_context
from services.tools import bounded_regex
from services.tools.manage_script import _split_uri, _validate_script_name
from services.tools.utils import coerce_bool, coerce_int
from transport.unity_transport import send_with_unity_instance
from transport.legacy.unity_connection import async_send_command_with_retry
from utils.source_lines import source_line_locations

logger = logging.getLogger("mcp-for-unity-server")


@mcp_for_unity_tool(
    unity_target="manage_script",
    description="Searches a C# script with a regex pattern and returns line numbers, excerpts and identities of the searched snapshot. bytes_sha256 identifies raw file bytes when supplied by the Editor; sha256 identifies decoded UTF-8 content.",
    annotations=ToolAnnotations(
        title="Find in File",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    ),
)
async def find_in_file(
    ctx: Context,
    uri: Annotated[str, "The C# script URI or path under Assets/ (.cs or extensionless)"],
    pattern: Annotated[str, "The regex pattern to search for"],
    project_root: Annotated[str | None, "Optional project root path"] = None,
    max_results: Annotated[int, "Cap results to avoid huge payloads"] = 200,
    ignore_case: Annotated[bool | str | None, "Case insensitive search"] = True,
) -> dict[str, Any]:
    # project_root is currently unused but kept for interface consistency
    try:
        name, directory = _split_uri(uri)
        name_error = _validate_script_name(name)
        if name_error:
            return {"success": False, "message": name_error}
        flags = re.MULTILINE
        ic = ignore_case
        if isinstance(ic, str):
            ic = {"1": True, "yes": True, "0": False, "no": False}.get(ic.lower(), ic)
        ic = coerce_bool(ic, default=False)
        if ic:
            flags |= re.IGNORECASE
        max_results = max(1, min(coerce_int(max_results, default=200), 1000))
    except (ValueError, TypeError) as exc:
        return {"success": False, "message": str(exc)}
    try:
        # Compile against an empty buffer: syntax and pattern limits need no file read.
        bounded_regex._compile(pattern, "", flags)
    except (ValueError, TypeError, bounded_regex.regex.error) as exc:
        return {"success": False, "message": f"Regex search rejected: {exc}"}

    unity_instance = await get_unity_instance_from_context(ctx)
    logger.info("Processing find_in_file")

    # 1. Read file content via Unity
    read_resp = await send_with_unity_instance(
        async_send_command_with_retry,
        unity_instance,
        "manage_script",
        {
            "action": "read",
            "name": name,
            "path": directory,
        },
    )

    if not isinstance(read_resp, dict) or not read_resp.get("success"):
        return (
            read_resp
            if isinstance(read_resp, dict)
            else {"success": False, "message": str(read_resp)}
        )

    data = read_resp.get("data", {})
    contents = data.get("contents")
    if not contents and data.get("contentsEncoded") and data.get("encodedContents"):
        try:
            contents = base64.b64decode(data.get("encodedContents", "").encode("utf-8")).decode(
                "utf-8", "replace"
            )
        except (ValueError, TypeError, base64.binascii.Error):
            contents = contents or ""

    if contents is None:
        return {"success": False, "message": "Could not read file content."}

    # 2. Perform regex search
    try:
        budget = bounded_regex.WorkBudget()
        found = await bounded_regex.run_work(
            budget,
            partial(
                copy_context().run,
                bounded_regex.find_matches,
                pattern,
                contents,
                flags,
                budget=budget,
            ),
        )
    except (ValueError, TimeoutError, bounded_regex.regex.error) as e:
        return {"success": False, "message": f"Regex search rejected: {e}"}

    selected = found[:max_results]
    locations = source_line_locations(contents, (match.start() for match in selected))
    excerpts = {
        line.start: contents[line.start : line.end].strip()[:2000]
        for line in set(locations.values())
    }

    results = []
    for m in selected:
        start_idx = m.start()
        line = locations[start_idx]
        results.append(
            {
                "line": line.number,
                "content": excerpts[line.start],
                "match": m.group(0)[:2000],
                "start": start_idx,
                "end": m.end(),
            }
        )

    return {
        "success": True,
        "data": {
            "matches": results,
            "count": len(results),
            "total_matches": len(found),
            "sha256": data.get("sha256"),
            "bytes_sha256": data.get("bytes_sha256"),
        },
    }
