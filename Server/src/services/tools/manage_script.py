import base64
import logging
import os
import re
from typing import Annotated, Any, Literal
from urllib.parse import urlparse, unquote

from fastmcp import FastMCP, Context
from mcp.types import ToolAnnotations

from services.registry import mcp_for_unity_tool
from services.tools import get_unity_instance_from_context
from services.tools.refresh_unity import send_mutation, verify_edit_by_sha
from services.tools.utils import coerce_bool, coerce_int
from transport.unity_transport import send_with_unity_instance
import transport.legacy.unity_connection

logger = logging.getLogger("mcp-for-unity-server")

# Strong references to fire-and-forget tasks to prevent GC before completion
_background_tasks: set = set()


def _is_missing_script_read(response: dict[str, Any]) -> bool:
    """Recognize Unity ReadScript's explicit missing-file response."""
    error = response.get("error")
    return (
        response.get("success") is False
        and isinstance(error, str)
        and error.startswith("Script not found at '")
        and error.endswith("'.")
    )


def _script_lines_and_starts(text: str) -> tuple[list[str], list[int]]:
    """Match Unity's CR, LF and CRLF lines with codepoint start offsets."""
    lines: list[str] = []
    starts = [0]
    for newline in re.finditer(r"\r\n|\r|\n", text):
        lines.append(text[starts[-1] : newline.start()])
        starts.append(newline.end())
    lines.append(text[starts[-1] :])
    return lines, starts


def _normalize_script_options(options: dict[str, Any] | None) -> dict[str, Any]:
    """Validate option shapes before routing; preserve legacy selector values."""
    if options is not None and not isinstance(options, dict):
        raise ValueError("Script options must be an object or null")
    parsed = dict(options or {})
    for field in ("refresh", "validate", "applyMode", "apply_mode"):
        value = parsed.get(field)
        if value is not None and not isinstance(value, str):
            raise ValueError(f"Script option '{field}' must be a string or null")
    for field in ("preview", "debug_preview", "force_sentinel_reload"):
        if field in parsed:
            parsed[field] = coerce_bool(parsed[field], default=False)
    return parsed


def _validate_script_name(name: str) -> str | None:
    """Mirror the Unity handler's content-independent name requirement."""
    if not name or not re.match(r"^[a-zA-Z_][a-zA-Z0-9_]*$", name):
        return "Script name must use letters, numbers, underscores, and cannot start with a number."
    return None


class _EditCoordinateError(ValueError):
    """A supplied edit coordinate could not be parsed at the JSON boundary."""

    def __init__(self, field: str, message: str):
        self.field = field
        super().__init__(message)


def _edit_integer(value: Any, field: str) -> int:
    """Parse a required coordinate without truncating floats or accepting bools."""
    parsed = coerce_int(value)
    if parsed is None:
        raise _EditCoordinateError(field, f"{field} must be an integer")
    return parsed


def _normalize_edit_coordinates(edit: dict[str, Any]) -> dict[str, Any]:
    """Parse all supplied coordinate representations before document reads."""
    if not isinstance(edit, dict):
        raise _EditCoordinateError("edit", "Each edit must be an object")
    parsed = dict(edit)
    for field in ("startLine", "startCol", "endLine", "endCol"):
        if field in parsed:
            parsed[field] = _edit_integer(parsed[field], field)
    rng = parsed.get("range")
    if isinstance(rng, dict):
        rng = dict(rng)
        for end in ("start", "end"):
            position = rng.get(end, {})
            if not isinstance(position, dict):
                raise _EditCoordinateError(
                    end, "LSP position must contain line and character offsets"
                )
            position = dict(position)
            for field in ("line", "character"):
                position[field] = _edit_integer(position.get(field, 0), field)
                if position[field] < 0:
                    raise _EditCoordinateError(field, "LSP offsets must be nonnegative")
            rng[end] = position
        parsed["range"] = rng
    elif isinstance(rng, (list, tuple)) and len(rng) == 2:
        parsed["range"] = [_edit_integer(value, "range index") for value in rng]
        if any(value < 0 for value in parsed["range"]):
            raise _EditCoordinateError("range", "Text edit indices must be nonnegative")
    return parsed


def _validate_text_edit_shape(edit: dict[str, Any]) -> dict[str, Any]:
    """Validate content-independent requirements and normalize the text alias."""
    parsed = dict(edit)
    if "newText" not in parsed and "text" in parsed:
        parsed["newText"] = parsed.pop("text")
    if not isinstance(parsed.get("newText"), str):
        raise ValueError("Each text edit requires a string newText or text")
    if not all(field in parsed for field in ("startLine", "startCol", "endLine", "endCol")):
        rng = parsed.get("range")
        if not isinstance(rng, dict) and not (isinstance(rng, (list, tuple)) and len(rng) == 2):
            raise ValueError(
                "Text edits require startLine/startCol/endLine/endCol or a normalizable range"
            )
    return parsed


def _lsp_position_to_line_col(lines: list[str], position: dict[str, int]) -> tuple[int, int]:
    """Convert default LSP UTF-16 offsets to Unity's 1-based codepoint columns."""
    if not isinstance(position, dict):
        raise ValueError("LSP position must contain line and character offsets")
    line = _edit_integer(position.get("line", 0), "line")
    character = _edit_integer(position.get("character", 0), "character")
    if line < 0 or line >= len(lines) or character < 0:
        raise ValueError("LSP position is outside the document")
    text = lines[line].removesuffix("\r")
    units = 0
    for column, char in enumerate(text):
        if units == character:
            return line + 1, column + 1
        units += 2 if ord(char) > 0xFFFF else 1
        if units > character:
            raise ValueError("LSP position splits a UTF-16 surrogate pair")
    # LSP offsets beyond the line end are clamped to that end.
    return line + 1, len(text) + 1


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

    name = os.path.splitext(os.path.basename(effective_path))[0]
    directory = os.path.dirname(effective_path)
    return name, directory


@mcp_for_unity_tool(
    unity_target="manage_script",
    description=(
        """Apply small text edits to a C# script identified by URI.
    IMPORTANT: This tool replaces EXACT character positions. Always verify content at target lines/columns BEFORE editing!
    RECOMMENDED WORKFLOW:
        1. First call resources/read with start_line/line_count to verify exact content
        2. Count columns carefully (or use find_in_file to locate patterns)
        3. Apply your edit with precise coordinates
        4. Consider script_apply_edits with anchors for safer pattern-based replacements
    Notes:
        - For method/class operations, use script_apply_edits (safer, structured edits)
        - For pattern-based replacements, consider anchor operations in script_apply_edits
        - newText is literal: whitespace and line endings are preserved exactly, with no indentation or newline padding
        - options.preview=true prepares a complete read-only proposal with target paths and hashes; older Unity packages reject the safe preview action and require an upgrade
        - Lines, columns are 1-indexed
        - Tabs count as 1 column"""
    ),
    annotations=ToolAnnotations(
        title="Apply Text Edits",
        destructiveHint=True,
    ),
)
async def apply_text_edits(
    ctx: Context,
    uri: Annotated[
        str,
        "URI of the script to edit under Assets/ directory, mcpforunity://path/Assets/... or file://... or Assets/...",
    ],
    edits: Annotated[
        list[dict[str, Any]],
        "List of edits to apply to the script, i.e. a list of {startLine,startCol,endLine,endCol,newText} (1-indexed!)",
    ],
    precondition_sha256: Annotated[
        str, "Optional SHA256 of the script to edit, used to prevent concurrent edits"
    ]
    | None = None,
    strict: Annotated[bool, "Optional strict flag, used to enforce strict mode"] | None = None,
    options: Annotated[
        dict[str, Any], "Optional options, used to pass additional options to the script editor"
    ]
    | None = None,
) -> dict[str, Any]:
    try:
        opts = _normalize_script_options(options)
    except ValueError as exc:
        return {"success": False, "code": "invalid_options", "message": str(exc)}
    try:
        edits = [_normalize_edit_coordinates(edit) for edit in edits]
    except ValueError as exc:
        return {"success": False, "code": "invalid_range", "message": str(exc)}
    try:
        if not edits:
            raise ValueError("At least one text edit is required")
        edits = [_validate_text_edit_shape(edit) for edit in edits]
    except ValueError as exc:
        return {"success": False, "code": "missing_field", "message": str(exc)}
    logger.info("Processing apply_text_edits")
    name, directory = _split_uri(uri)
    name_error = _validate_script_name(name)
    if name_error:
        return {"success": False, "code": "bad_name", "message": name_error}
    unity_instance = None

    # Normalize common aliases/misuses for resilience:
    # - Accept LSP-style range objects: {range:{start:{line,character}, end:{...}}, newText|text}
    # - Accept index ranges as a 2-int array: {range:[startIndex,endIndex], text}
    # If normalization is required, read current contents to map indices -> 1-based line/col.
    def _needs_normalization(arr: list[dict[str, Any]]) -> bool:
        for e in arr or []:
            if (
                ("startLine" not in e)
                or ("startCol" not in e)
                or ("endLine" not in e)
                or ("endCol" not in e)
                or ("newText" not in e and "text" in e)
            ):
                return True
        return False

    normalized_edits: list[dict[str, Any]] = []
    warnings: list[str] = []
    if _needs_normalization(edits):
        # Read file to support index->line/col conversion when needed
        unity_instance = await get_unity_instance_from_context(ctx)
        read_resp = await send_with_unity_instance(
            transport.legacy.unity_connection.async_send_command_with_retry,
            unity_instance,
            "manage_script",
            {
                "action": "read",
                "name": name,
                "path": directory,
            },
        )
        if not (isinstance(read_resp, dict) and read_resp.get("success")):
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
            except Exception:
                contents = contents or ""
        source_lines, line_starts = (
            _script_lines_and_starts(contents) if isinstance(contents, str) else ([], [])
        )

        # Helper to map 0-based character index to 1-based line/col
        def line_col_from_index(idx: int) -> tuple[int, int]:
            if not isinstance(contents, str) or idx < 0 or idx > len(contents):
                raise ValueError("Text edit index is outside the document")
            for line in range(len(line_starts) - 1, -1, -1):
                if idx >= line_starts[line]:
                    column = idx - line_starts[line]
                    if column > len(source_lines[line]):
                        raise ValueError("Text edit index is inside a CRLF newline")
                    return line + 1, column + 1
            raise ValueError("Text edit index is outside the document")

        for e in edits or []:
            e2 = dict(e)
            # Map text->newText if needed
            if "newText" not in e2 and "text" in e2:
                e2["newText"] = e2.pop("text")

            if "startLine" in e2 and "startCol" in e2 and "endLine" in e2 and "endCol" in e2:
                # Guard: explicit fields must be 1-based.
                zero_based = False
                for k in ("startLine", "startCol", "endLine", "endCol"):
                    if e2[k] < 1:
                        zero_based = True
                if zero_based:
                    if strict:
                        return {
                            "success": False,
                            "code": "zero_based_explicit_fields",
                            "message": "Explicit line/col fields are 1-based; received zero-based.",
                            "data": {"normalizedEdits": normalized_edits},
                        }
                    # Normalize by clamping to 1 and warn
                    for k in ("startLine", "startCol", "endLine", "endCol"):
                        if e2[k] < 1:
                            e2[k] = 1
                    warnings.append("zero_based_explicit_fields_normalized")
                normalized_edits.append(e2)
                continue

            rng = e2.get("range")
            if isinstance(rng, dict):
                # LSP defaults to UTF-16 offsets; explicit coordinates use codepoints.
                s = rng.get("start", {})
                t = rng.get("end", {})
                try:
                    e2["startLine"], e2["startCol"] = _lsp_position_to_line_col(source_lines, s)
                    e2["endLine"], e2["endCol"] = _lsp_position_to_line_col(source_lines, t)
                except (ValueError, TypeError, OverflowError) as exc:
                    return {"success": False, "code": "invalid_range", "message": str(exc)}
                e2.pop("range", None)
                normalized_edits.append(e2)
                continue
            if isinstance(rng, (list, tuple)) and len(rng) == 2:
                try:
                    a, b = rng
                    if b < a:
                        a, b = b, a
                    sl, sc = line_col_from_index(a)
                    el, ec = line_col_from_index(b)
                    e2["startLine"] = sl
                    e2["startCol"] = sc
                    e2["endLine"] = el
                    e2["endCol"] = ec
                    e2.pop("range", None)
                    normalized_edits.append(e2)
                    continue
                except (ValueError, TypeError, OverflowError) as exc:
                    return {"success": False, "code": "invalid_range", "message": str(exc)}
            # Could not normalize this edit
            return {
                "success": False,
                "code": "missing_field",
                "message": "apply_text_edits requires startLine/startCol/endLine/endCol/newText or a normalizable 'range'",
                "data": {
                    "expected": ["startLine", "startCol", "endLine", "endCol", "newText"],
                    "got": e,
                },
            }
    else:
        # Even when edits appear already in explicit form, validate 1-based coordinates.
        normalized_edits = []
        for e in edits or []:
            e2 = dict(e)
            has_all = all(k in e2 for k in ("startLine", "startCol", "endLine", "endCol"))
            if has_all:
                zero_based = False
                for k in ("startLine", "startCol", "endLine", "endCol"):
                    if e2[k] < 1:
                        zero_based = True
                if zero_based:
                    if strict:
                        return {
                            "success": False,
                            "code": "zero_based_explicit_fields",
                            "message": "Explicit line/col fields are 1-based; received zero-based.",
                            "data": {"normalizedEdits": [e2]},
                        }
                    for k in ("startLine", "startCol", "endLine", "endCol"):
                        if e2[k] < 1:
                            e2[k] = 1
                    if "zero_based_explicit_fields_normalized" not in warnings:
                        warnings.append("zero_based_explicit_fields_normalized")
            normalized_edits.append(e2)

    # Preflight: detect overlapping ranges among normalized line/col spans
    def _pos_tuple(e: dict[str, Any], key_start: bool) -> tuple[int, int]:
        return (
            e["startLine"] if key_start else e["endLine"],
            e["startCol"] if key_start else e["endCol"],
        )

    def _le(a: tuple[int, int], b: tuple[int, int]) -> bool:
        return a[0] < b[0] or (a[0] == b[0] and a[1] <= b[1])

    # Consider only true replace ranges (non-zero length). Pure insertions (zero-width) don't overlap.
    spans = []
    for e in normalized_edits or []:
        try:
            s = _pos_tuple(e, True)
            t = _pos_tuple(e, False)
            if s != t:
                spans.append((s, t))
        except Exception:
            # If coordinates missing or invalid, let the server validate later
            pass

    if spans:
        spans_sorted = sorted(spans, key=lambda p: (p[0][0], p[0][1]))
        for i in range(1, len(spans_sorted)):
            prev_end = spans_sorted[i - 1][1]
            curr_start = spans_sorted[i][0]
            # Overlap if prev_end > curr_start (strict), i.e., not prev_end <= curr_start
            if not _le(prev_end, curr_start):
                conflicts = [
                    {
                        "startA": {
                            "line": spans_sorted[i - 1][0][0],
                            "col": spans_sorted[i - 1][0][1],
                        },
                        "endA": {
                            "line": spans_sorted[i - 1][1][0],
                            "col": spans_sorted[i - 1][1][1],
                        },
                        "startB": {"line": spans_sorted[i][0][0], "col": spans_sorted[i][0][1]},
                        "endB": {"line": spans_sorted[i][1][0], "col": spans_sorted[i][1][1]},
                    }
                ]
                return {
                    "success": False,
                    "code": "overlap",
                    "data": {"status": "overlap", "conflicts": conflicts},
                }

    # Note: Do not auto-compute precondition if missing; callers should supply it
    # via mcp__unity__get_sha or a prior read. This avoids hidden extra calls and
    # preserves existing call-count expectations in clients/tests.

    # Default options: for multi-span batches, prefer atomic to avoid mid-apply imbalance
    try:
        if len(normalized_edits) > 1 and "applyMode" not in opts:
            opts["applyMode"] = "atomic"
    except Exception:
        pass
    # Support optional debug preview for span-by-span simulation without write
    if opts.get("debug_preview") and not opts.get("preview"):
        try:
            import difflib

            # Apply locally to preview final result
            lines = []
            # Build an indexable original from a read if we normalized from read; otherwise skip
            prev = ""
            # We cannot guarantee file contents here without a read; return normalized spans only
            return {
                "success": True,
                "message": "Preview only (no write)",
                "data": {"normalizedEdits": normalized_edits, "preview": True},
            }
        except Exception as e:
            return {
                "success": False,
                "code": "preview_failed",
                "message": f"debug_preview failed: {e}",
                "data": {"normalizedEdits": normalized_edits},
            }

    params = {
        "action": "apply_text_edits",
        "name": name,
        "path": directory,
        "edits": normalized_edits,
        "precondition_sha256": precondition_sha256,
        "options": opts,
    }
    params = {k: v for k, v in params.items() if v is not None}

    if not _needs_normalization(edits):
        unity_instance = await get_unity_instance_from_context(ctx)

    if opts.get("preview"):
        from services.tools.script_apply_edits import _prepared_handoff

        params["action"] = "preview_text_edits"
        response = await send_with_unity_instance(
            transport.legacy.unity_connection.async_send_command_with_retry,
            unity_instance,
            "manage_script",
            params,
        )
        prepared = _prepared_handoff(response, unity_instance, f"{directory}/{name}.cs")
        if prepared.get("success"):
            prepared["data"]["normalizedEdits"] = normalized_edits
        return prepared

    async def _verify_edit():
        if await verify_edit_by_sha(unity_instance, name, directory, precondition_sha256):
            return {
                "success": True,
                "message": "Edit applied (verified after domain reload).",
                "data": {"normalizedEdits": normalized_edits},
            }
        return None

    resp = await send_mutation(
        ctx, unity_instance, "manage_script", params, verify_after_disconnect=_verify_edit
    )
    if isinstance(resp, dict):
        data = resp.setdefault("data", {})
        data.setdefault("normalizedEdits", normalized_edits)
        if warnings:
            data.setdefault("warnings", warnings)
        if resp.get("success") and opts.get("force_sentinel_reload"):
            # Optional: flip sentinel via menu if explicitly requested
            try:
                import asyncio
                import json
                import glob
                import os

                def _latest_status() -> dict | None:
                    try:
                        files = sorted(
                            glob.glob(os.path.expanduser("~/.unity-mcp/unity-mcp-status-*.json")),
                            key=os.path.getmtime,
                            reverse=True,
                        )
                        if not files:
                            return None
                        with open(files[0], "r") as f:
                            return json.loads(f.read())
                    except Exception:
                        return None

                async def _flip_async():
                    try:
                        await asyncio.sleep(0.1)
                        st = _latest_status()
                        if st and st.get("reloading"):
                            return
                        await transport.legacy.unity_connection.async_send_command_with_retry(
                            "execute_menu_item",
                            {"menuPath": "MCP/Flip Reload Sentinel"},
                            max_retries=0,
                            retry_ms=0,
                            instance_id=unity_instance,
                        )
                    except Exception:
                        pass

                task = asyncio.create_task(_flip_async())
                _background_tasks.add(task)
                task.add_done_callback(_background_tasks.discard)
            except Exception:
                pass
            return resp
        return resp
    return {"success": False, "message": str(resp)}


@mcp_for_unity_tool(
    unity_target="manage_script",
    description="Create a new C# script at the given project path.",
    annotations=ToolAnnotations(
        title="Create Script",
        destructiveHint=True,
    ),
)
async def create_script(
    ctx: Context,
    path: Annotated[
        str, "Path under Assets/ to create the script at, e.g., 'Assets/Scripts/My.cs'"
    ],
    contents: Annotated[
        str,
        "Contents of the script to create (plain text C# code). The server handles Base64 encoding.",
    ],
    script_type: Annotated[str, "Script type (e.g., 'C#')"] | None = None,
    namespace: Annotated[str, "Namespace for the script"] | None = None,
) -> dict[str, Any]:
    logger.info("Processing create_script")
    name = os.path.splitext(os.path.basename(path))[0]
    directory = os.path.dirname(path)
    # Local validation to avoid round-trips on obviously bad input
    raw_path = (path or "").replace("\\", "/")
    norm_path = os.path.normpath(raw_path).replace("\\", "/")
    if not directory or directory.split("/")[0].lower() != "assets":
        return {
            "success": False,
            "code": "path_outside_assets",
            "message": f"path must be under 'Assets/'; got '{path}'.",
        }
    if ".." in raw_path.split("/") or norm_path.startswith("/"):
        return {
            "success": False,
            "code": "bad_path",
            "message": "path must not contain traversal or be absolute.",
        }
    if not name:
        return {
            "success": False,
            "code": "bad_path",
            "message": "path must include a script file name.",
        }
    if not norm_path.lower().endswith(".cs"):
        return {
            "success": False,
            "code": "bad_extension",
            "message": "script file must end with .cs.",
        }
    name_error = _validate_script_name(name)
    if name_error:
        return {"success": False, "code": "bad_name", "message": name_error}
    params: dict[str, Any] = {
        "action": "create",
        "name": name,
        "path": directory,
        "namespace": namespace,
        "scriptType": script_type,
    }
    if contents:
        params["encodedContents"] = base64.b64encode(contents.encode("utf-8")).decode("utf-8")
        params["contentsEncoded"] = True
    params = {k: v for k, v in params.items() if v is not None}
    unity_instance = await get_unity_instance_from_context(ctx)

    async def _verify_create():
        verify = await send_with_unity_instance(
            transport.legacy.unity_connection.async_send_command_with_retry,
            unity_instance,
            "manage_script",
            {"action": "read", "name": name, "path": directory},
        )
        if isinstance(verify, dict) and verify.get("success"):
            return {
                "success": True,
                "message": "Script created (verified after domain reload).",
                "data": verify.get("data"),
            }
        return None

    resp = await send_mutation(
        ctx, unity_instance, "manage_script", params, verify_after_disconnect=_verify_create
    )
    return resp if isinstance(resp, dict) else {"success": False, "message": str(resp)}


@mcp_for_unity_tool(
    unity_target="manage_script",
    description="Delete a C# script by URI or Assets-relative path.",
    annotations=ToolAnnotations(
        title="Delete Script",
        destructiveHint=True,
    ),
)
async def delete_script(
    ctx: Context,
    uri: Annotated[
        str,
        "URI of the script to delete under Assets/ directory, mcpforunity://path/Assets/... or file://... or Assets/...",
    ],
) -> dict[str, Any]:
    """Delete a C# script by URI."""
    logger.info("Processing delete_script")
    name, directory = _split_uri(uri)
    if not directory or directory.split("/")[0].lower() != "assets":
        return {
            "success": False,
            "code": "path_outside_assets",
            "message": "URI must resolve under 'Assets/'.",
        }
    name_error = _validate_script_name(name)
    if name_error:
        return {"success": False, "code": "bad_name", "message": name_error}
    params = {"action": "delete", "name": name, "path": directory}
    unity_instance = await get_unity_instance_from_context(ctx)

    async def _verify_delete():
        verify = await send_with_unity_instance(
            transport.legacy.unity_connection.async_send_command_with_retry,
            unity_instance,
            "manage_script",
            {"action": "read", "name": name, "path": directory},
        )
        if isinstance(verify, dict) and _is_missing_script_read(verify):
            return {"success": True, "message": "Script deleted (verified after domain reload)."}
        return None

    resp = await send_mutation(
        ctx, unity_instance, "manage_script", params, verify_after_disconnect=_verify_delete
    )
    return resp if isinstance(resp, dict) else {"success": False, "message": str(resp)}


@mcp_for_unity_tool(
    unity_target="manage_script",
    description="Validate a C# script and return diagnostics.",
    annotations=ToolAnnotations(
        title="Validate Script",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    ),
)
async def validate_script(
    ctx: Context,
    uri: Annotated[
        str,
        "URI of the script to validate under Assets/ directory, mcpforunity://path/Assets/... or file://... or Assets/...",
    ],
    level: Annotated[Literal["basic", "standard"], "Validation level"] = "basic",
    include_diagnostics: Annotated[bool, "Include full diagnostics and summary"] = False,
) -> dict[str, Any]:
    logger.info("Processing validate_script")
    name, directory = _split_uri(uri)
    if not directory or directory.split("/")[0].lower() != "assets":
        return {
            "success": False,
            "code": "path_outside_assets",
            "message": "URI must resolve under 'Assets/'.",
        }
    if level not in ("basic", "standard"):
        return {
            "success": False,
            "code": "bad_level",
            "message": "level must be 'basic' or 'standard'.",
        }
    name_error = _validate_script_name(name)
    if name_error:
        return {"success": False, "code": "bad_name", "message": name_error}
    unity_instance = await get_unity_instance_from_context(ctx)
    params = {
        "action": "validate",
        "name": name,
        "path": directory,
        "level": level,
    }
    resp = await send_with_unity_instance(
        transport.legacy.unity_connection.async_send_command_with_retry,
        unity_instance,
        "manage_script",
        params,
    )
    if isinstance(resp, dict) and resp.get("success"):
        diags = resp.get("data", {}).get("diagnostics", []) or []
        warnings = sum(1 for d in diags if str(d.get("severity", "")).lower() == "warning")
        errors = sum(1 for d in diags if str(d.get("severity", "")).lower() in ("error", "fatal"))
        if include_diagnostics:
            return {
                "success": True,
                "data": {"diagnostics": diags, "summary": {"warnings": warnings, "errors": errors}},
            }
        return {"success": True, "data": {"warnings": warnings, "errors": errors}}
    return resp if isinstance(resp, dict) else {"success": False, "message": str(resp)}


@mcp_for_unity_tool(
    description="Compatibility router for legacy script operations. Prefer apply_text_edits (ranges) or script_apply_edits (structured) for edits. Read-only action: read. Modifying actions: create, delete.",
    annotations=ToolAnnotations(
        title="Manage Script",
        destructiveHint=True,
    ),
)
async def manage_script(
    ctx: Context,
    action: Annotated[
        Literal["create", "read", "delete"], "Perform CRUD operations on C# scripts."
    ],
    name: Annotated[str, "Script name (no .cs extension)", "Name of the script to create"],
    path: Annotated[
        str,
        "Asset path (default: 'Assets/')",
        "Path under Assets/ to create the script at, e.g., 'Assets/Scripts/My.cs'",
    ],
    contents: Annotated[str, "Contents of the script to create", "C# code for 'create' action"]
    | None = None,
    script_type: Annotated[str, "Script type (e.g., 'C#')", "Type hint (e.g., 'MonoBehaviour')"]
    | None = None,
    namespace: Annotated[str, "Namespace for the script"] | None = None,
) -> dict[str, Any]:
    logger.info("Processing manage_script")
    try:
        name_error = _validate_script_name(name)
        if name_error:
            return {"success": False, "code": "bad_name", "message": name_error}
        # Prepare parameters for Unity
        params = {
            "action": action,
            "name": name,
            "path": path,
            "namespace": namespace,
            "scriptType": script_type,
        }

        # Base64 encode the contents if they exist to avoid JSON escaping issues
        if contents:
            if action == "create":
                params["encodedContents"] = base64.b64encode(contents.encode("utf-8")).decode(
                    "utf-8"
                )
                params["contentsEncoded"] = True
            else:
                params["contents"] = contents

        params = {k: v for k, v in params.items() if v is not None}
        unity_instance = await get_unity_instance_from_context(ctx)

        if action == "read":
            response = await send_with_unity_instance(
                transport.legacy.unity_connection.async_send_command_with_retry,
                unity_instance,
                "manage_script",
                params,
                retry_on_reload=True,
            )
        else:

            async def _verify_mutation():
                verify = await send_with_unity_instance(
                    transport.legacy.unity_connection.async_send_command_with_retry,
                    unity_instance,
                    "manage_script",
                    {"action": "read", "name": name, "path": path},
                )
                if action == "create" and isinstance(verify, dict) and verify.get("success"):
                    return {
                        "success": True,
                        "message": "Script created (verified after domain reload).",
                        "data": verify.get("data"),
                    }
                elif (
                    action == "delete"
                    and isinstance(verify, dict)
                    and _is_missing_script_read(verify)
                ):
                    return {
                        "success": True,
                        "message": "Script deleted (verified after domain reload).",
                    }
                return None

            response = await send_mutation(
                ctx,
                unity_instance,
                "manage_script",
                params,
                verify_after_disconnect=_verify_mutation,
            )

        if isinstance(response, dict):
            if response.get("success"):
                if response.get("data", {}).get("contentsEncoded"):
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
            return response

        return {"success": False, "message": str(response)}

    except Exception as e:
        return {
            "success": False,
            "message": f"Python error managing script: {str(e)}",
        }


@mcp_for_unity_tool(
    unity_target=None,
    group=None,
    description=(
        """Get manage_script capabilities (supported ops, limits, and guards).
    Returns:
        - ops: list of supported structured ops
        - text_ops: list of supported text ops
        - max_edit_payload_bytes: server edit payload cap
        - guards: header/using guard enabled flag"""
    ),
    annotations=ToolAnnotations(
        title="Manage Script Capabilities",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    ),
)
async def manage_script_capabilities(ctx: Context) -> dict[str, Any]:
    logger.info("Processing manage_script_capabilities")
    try:
        # Keep in sync with server/Editor ManageScript implementation
        ops = [
            "replace_class",
            "delete_class",
            "replace_method",
            "delete_method",
            "insert_method",
            "anchor_insert",
            "anchor_delete",
            "anchor_replace",
        ]
        text_ops = ["replace_range", "regex_replace", "prepend", "append"]
        # Match the Editor's ManageScript.MaxEditPayloadBytes text-edit budget.
        max_edit_payload_bytes = 64 * 1024
        guards = {"using_guard": True}
        extras = {"get_sha": True}
        return {
            "success": True,
            "data": {
                "ops": ops,
                "text_ops": text_ops,
                "max_edit_payload_bytes": max_edit_payload_bytes,
                "guards": guards,
                "extras": extras,
            },
        }
    except Exception as e:
        return {"success": False, "error": f"capabilities error: {e}"}


@mcp_for_unity_tool(
    unity_target="manage_script",
    description="Get SHA256 and basic metadata for a Unity C# script without returning file contents. Requires uri (script path under Assets/ or mcpforunity://path/Assets/... or file://...).",
    annotations=ToolAnnotations(
        title="Get SHA",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    ),
)
async def get_sha(
    ctx: Context,
    uri: Annotated[
        str,
        "URI of the script to edit under Assets/ directory, mcpforunity://path/Assets/... or file://... or Assets/...",
    ],
) -> dict[str, Any]:
    logger.info("Processing get_sha")
    try:
        name, directory = _split_uri(uri)
        name_error = _validate_script_name(name)
        if name_error:
            return {"success": False, "code": "bad_name", "message": name_error}
        unity_instance = await get_unity_instance_from_context(ctx)
        params = {"action": "get_sha", "name": name, "path": directory}
        resp = await send_with_unity_instance(
            transport.legacy.unity_connection.async_send_command_with_retry,
            unity_instance,
            "manage_script",
            params,
        )
        if isinstance(resp, dict) and resp.get("success"):
            data = resp.get("data", {})
            minimal = {"sha256": data.get("sha256"), "lengthBytes": data.get("lengthBytes")}
            return {"success": True, "data": minimal}
        return resp if isinstance(resp, dict) else {"success": False, "message": str(resp)}
    except Exception as e:
        return {"success": False, "message": f"get_sha error: {e}"}
