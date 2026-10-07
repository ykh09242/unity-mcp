import asyncio
import base64
import logging
import os
import re
from typing import Annotated, Any
from urllib.parse import unquote, urlparse

from fastmcp import Context
from mcp.types import ToolAnnotations

from services.registry import mcp_for_unity_tool
from services.tools import get_unity_instance_from_context
from services.tools import bounded_regex
from services.tools.manage_script import _validate_script_name
from services.tools.utils import coerce_int
from transport.unity_transport import send_with_unity_instance
from transport.legacy.unity_connection import async_send_command_with_retry

logger = logging.getLogger("mcp-for-unity-server")


def _split_uri(uri: str) -> tuple[str, str]:
    """Split an incoming URI or path into (name, directory) suitable for Unity.

    Rules:
    - mcpforunity://path/Assets/... → keep as Assets-relative (after decode/normalize)
    - file://... → percent-decode, normalize, strip host and leading slashes,
        then, if any 'Assets' segment exists, return path relative to that 'Assets' root.
        Otherwise, fall back to original name/dir behavior.
    - plain paths → decode/normalize separators; if they contain an 'Assets' segment,
        return relative to 'Assets'.
    """
    raw_path: str
    if uri.startswith("mcpforunity://path/"):
        raw_path = uri[len("mcpforunity://path/") :]
    elif uri.startswith("file://"):
        parsed = urlparse(uri)
        host = (parsed.netloc or "").strip()
        p = parsed.path or ""
        # UNC: file://server/share/... -> //server/share/...
        if host and host.lower() != "localhost":
            p = f"//{host}{p}"
        # Preserve leading slashes until the common decoding step.
        raw_path = p
    else:
        raw_path = uri

    # Decode once so escaped percent sequences remain literal path characters.
    raw_path = unquote(raw_path).replace("\\", "/")
    # Strip leading slash only for Windows drive-letter forms like "/C:/..."
    if os.name == "nt" and len(raw_path) >= 3 and raw_path[0] == "/" and raw_path[2] == ":":
        raw_path = raw_path[1:]

    # Normalize path (collapse ../, ./)
    norm = os.path.normpath(raw_path).replace("\\", "/")

    # If an 'Assets' segment exists, compute path relative to it (case-insensitive)
    parts = [p for p in norm.split("/") if p not in ("", ".")]
    idx = next((i for i, seg in enumerate(parts) if seg.lower() == "assets"), None)
    assets_rel = "/".join(parts[idx:]) if idx is not None else None

    effective_path = assets_rel if assets_rel else norm
    # For POSIX absolute paths outside Assets, drop the leading '/'
    # to return a clean relative-like directory (e.g., '/tmp' -> 'tmp').
    if effective_path.startswith("/"):
        effective_path = effective_path[1:]

    name, extension = os.path.splitext(os.path.basename(effective_path))
    if extension and extension.lower() != ".cs":
        raise ValueError(
            "find_in_file supports C# scripts (.cs) or extensionless script paths only."
        )
    directory = os.path.dirname(effective_path)
    return name, directory


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
            ic = ic.lower() in ("true", "1", "yes")
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
        found = await asyncio.to_thread(bounded_regex.find_matches, pattern, contents, flags)
    except (ValueError, TimeoutError, bounded_regex.regex.error) as e:
        return {"success": False, "message": f"Regex search rejected: {e}"}

    selected = found[:max_results]
    line_metadata = {}
    line_num = 1
    previous_start = 0
    line_end = -1
    excerpt = None
    # Reverse regex searches return descending offsets. Count disjoint ranges
    # in sorted order, then emit matches in the regex's original order.
    for start_idx in sorted({m.start() for m in selected}):
        line_num += contents.count("\n", previous_start, start_idx)
        previous_start = start_idx
        if excerpt is None or (line_end != -1 and line_end < start_idx):
            line_start = contents.rfind("\n", 0, start_idx) + 1
            line_end = contents.find("\n", start_idx)
            end = line_end if line_end != -1 else len(contents)
            excerpt = contents[line_start:end].strip()[:2000]
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
