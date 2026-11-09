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

logger = logging.getLogger("mcp-for-unity-server")


@mcp_for_unity_tool(
    unity_target="manage_script",
    description="Searches a C# script with a regex pattern and returns line numbers and excerpts.",
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
    line_metadata = {}
    line_num = 1
    line_start = 0
    # Same boundaries as ManageScript.SourceLineWindow; never normalize source
    # text because character offsets must continue to address the original text.
    boundaries = re.finditer(r"\r\n|[\n\r\v\f\x1c-\x1e\x85\u2028\u2029]", contents)
    boundary = next(boundaries, None)
    excerpt_start = -1
    excerpt = ""
    # Advance once through source boundaries, including for reverse regex results,
    # then emit matches in the regex's original order.
    for start_idx in sorted({m.start() for m in selected}):
        while boundary is not None and boundary.end() <= start_idx:
            line_num += 1
            line_start = boundary.end()
            boundary = next(boundaries, None)
        if line_start != excerpt_start:
            line_end = boundary.start() if boundary is not None else len(contents)
            excerpt = contents[line_start:line_end].strip()[:2000]
            excerpt_start = line_start
        line_metadata[start_idx] = (line_num, excerpt)

    results = []
    for m in selected:
        start_idx = m.start()
        line_num, excerpt = line_metadata[start_idx]
        results.append(
            {
                "line": line_num,
                "content": excerpt,
                "match": m.group(0)[:2000],
                "start": start_idx,
                "end": m.end(),
            }
        )

    return {
        "success": True,
        "data": {"matches": results, "count": len(results), "total_matches": len(found)},
    }
