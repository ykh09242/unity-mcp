"""Pure input normalization and preflight for script edit requests.

This module prepares client input before Unity selection, reads or writes.
Native structured snippet and .NET regex validation remain with the Editor.
"""

import base64
import re
from typing import Any

from services.tools import bounded_regex
from services.tools.manage_script import _normalize_edit_coordinates, _validate_text_edit_shape
from services.tools.utils import coerce_bool, coerce_int

MAX_REPLACEMENT_BYTES = 2 * 1024 * 1024


def _err(
    code: str,
    message: str,
    *,
    expected: dict[str, Any] | None = None,
    rewrite: dict[str, Any] | None = None,
    normalized: list[dict[str, Any]] | None = None,
    routing: str | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {"success": False, "code": code, "message": message}
    data: dict[str, Any] = {}
    if expected:
        data["expected"] = expected
    if rewrite:
        data["rewrite_suggestion"] = rewrite
    if normalized is not None:
        data["normalizedEdits"] = normalized
    if routing:
        data["routing"] = routing
    if extra:
        data.update(extra)
    if data:
        payload["data"] = data
    return payload


def _normalize_edit_scalars(edit: dict[str, Any]) -> dict[str, Any]:
    """Parse nested edit flags and numbers before they influence edit selection."""
    parsed = _normalize_edit_coordinates(edit)
    for field, default in (("ignore_case", False), ("allow_noop", True), ("prefer_last", True)):
        if field in parsed:
            parsed[field] = coerce_bool(parsed[field], default=default)
    if "count" in parsed:
        parsed["count"] = coerce_int(parsed["count"], default=0)
    return parsed


def normalize_script_edits(
    edits: list[dict[str, Any]], name: str, ordered: bool
) -> list[dict[str, Any]] | dict[str, Any]:
    """Canonicalize aliases and strictly decode bounded replacement payloads."""

    def _unwrap_and_alias(edit: dict[str, Any]) -> dict[str, Any]:
        # Unwrap single-key wrappers like {"replace_method": {...}}
        for wrapper_key in (
            "replace_method",
            "insert_method",
            "delete_method",
            "replace_class",
            "delete_class",
            "anchor_insert",
            "anchor_replace",
            "anchor_delete",
        ):
            if wrapper_key in edit and isinstance(edit[wrapper_key], dict):
                inner = dict(edit[wrapper_key])
                inner["op"] = wrapper_key
                edit = inner
                break

        e = dict(edit)
        op = (
            (e.get("op") or e.get("operation") or e.get("type") or e.get("mode") or "")
            .strip()
            .lower()
        )
        if op:
            e["op"] = op
        # Unity accepts mode as an op alias too; forward only the canonical op
        # so preflight and application cannot disagree about the operation.
        for alias in ("operation", "type", "mode"):
            e.pop(alias, None)

        # Common field aliases
        if "class_name" in e and "className" not in e:
            e["className"] = e.pop("class_name")
        if "class" in e and "className" not in e:
            e["className"] = e.pop("class")
        if "method_name" in e and "methodName" not in e:
            e["methodName"] = e.pop("method_name")
        # Some clients use a generic 'target' for method name
        if "target" in e and "methodName" not in e:
            e["methodName"] = e.pop("target")
        if "method" in e and "methodName" not in e:
            e["methodName"] = e.pop("method")
        if "new_content" in e and "replacement" not in e:
            e["replacement"] = e.pop("new_content")
        if "newMethod" in e and "replacement" not in e:
            e["replacement"] = e.pop("newMethod")
        if "new_method" in e and "replacement" not in e:
            e["replacement"] = e.pop("new_method")
        if "content" in e and "replacement" not in e:
            e["replacement"] = e.pop("content")
        if "after" in e and "afterMethodName" not in e:
            e["afterMethodName"] = e.pop("after")
        if "after_method" in e and "afterMethodName" not in e:
            e["afterMethodName"] = e.pop("after_method")
        if "before" in e and "beforeMethodName" not in e:
            e["beforeMethodName"] = e.pop("before")
        if "before_method" in e and "beforeMethodName" not in e:
            e["beforeMethodName"] = e.pop("before_method")
        # anchor_method → before/after based on position (default after)
        if "anchor_method" in e:
            anchor = e.pop("anchor_method")
            pos = (e.get("position") or "after").strip().lower()
            if pos == "before" and "beforeMethodName" not in e:
                e["beforeMethodName"] = anchor
            elif "afterMethodName" not in e:
                e["afterMethodName"] = anchor
        if "anchorText" in e and "anchor" not in e:
            e["anchor"] = e.pop("anchorText")
        if "pattern" in e and "anchor" not in e and e.get("op") and e["op"].startswith("anchor_"):
            e["anchor"] = e.pop("pattern")
        if "newText" in e and "text" not in e:
            e["text"] = e.pop("newText")

        # CI compatibility (T‑A/T‑E):
        # Accept method-anchored anchor_insert and upgrade to insert_method
        # Example incoming shape:
        #   {"op":"anchor_insert","afterMethodName":"GetCurrentTarget","text":"..."}
        if (
            e.get("op") == "anchor_insert"
            and not e.get("anchor")
            and (e.get("afterMethodName") or e.get("beforeMethodName"))
        ):
            e["op"] = "insert_method"
            if "replacement" not in e:
                e["replacement"] = e.get("text", "")

        # LSP-like range edit -> replace_range
        if "range" in e and isinstance(e["range"], dict):
            # Keep UTF-16 positions until file contents are available to convert.
            e["op"] = "replace_range"
            if "newText" in edit and "text" not in e:
                e["text"] = edit.get("newText", "")
        return e

    normalized_edits: list[dict[str, Any]] = []
    for raw in edits or []:
        try:
            e = _unwrap_and_alias(raw)
            e = _normalize_edit_scalars(e)
        except (ValueError, TypeError, AttributeError) as exc:
            return _err("invalid_range", str(exc))
        op = (
            (e.get("op") or e.get("operation") or e.get("type") or e.get("mode") or "")
            .strip()
            .lower()
        )

        # Default className to script name if missing on structured method/class ops
        if op in (
            "replace_class",
            "delete_class",
            "replace_method",
            "delete_method",
            "insert_method",
        ) and not e.get("className"):
            e["className"] = name

        # Map common aliases for text ops
        if op in ("text_replace",):
            e["op"] = "replace_range"
            normalized_edits.append(e)
            continue
        if op in ("regex_delete",):
            e["op"] = "regex_replace"
            e.setdefault("text", "")
            normalized_edits.append(e)
            continue
        if op == "regex_replace" and ("replacement" not in e):
            if "text" in e:
                e["replacement"] = e.get("text", "")
            elif "insert" in e or "content" in e:
                e["replacement"] = e.get("insert") or e.get("content") or ""
        if (
            not ordered
            and op == "anchor_insert"
            and not (e.get("text") or e.get("insert") or e.get("content") or e.get("replacement"))
        ):
            e["op"] = "anchor_delete"
            normalized_edits.append(e)
            continue
        normalized_edits.append(e)

    for edit in normalized_edits:
        if edit.get("replacementBase64") is None:
            continue
        value = edit["replacementBase64"]
        if not isinstance(value, str):
            return _err("invalid_edit", "replacementBase64 must be a string")
        if len(value) > ((MAX_REPLACEMENT_BYTES + 2) // 3) * 4:
            return _err("payload_too_large", "Encoded replacement exceeds 2 MiB")
        try:
            payload = base64.b64decode(value, validate=True)
            if len(payload) > MAX_REPLACEMENT_BYTES:
                return _err("payload_too_large", "Decoded replacement exceeds 2 MiB")
            decoded = payload.decode("utf-8", errors="strict")
        except (ValueError, TypeError, UnicodeError) as exc:
            return _err("invalid_edit", str(exc))
        if edit.get("replacement") is None or (
            edit.get("op") != "anchor_replace" and not edit.get("replacement")
        ):
            edit["replacement"] = decoded
    return normalized_edits


def validate_script_edits(
    edits: list[dict[str, Any]], name: str, ordered: bool
) -> dict[str, Any] | None:
    """Return the established field/regex preflight error without external work."""

    def error_with_hint(
        message: str, expected: dict[str, Any], suggestion: dict[str, Any]
    ) -> dict[str, Any]:
        return _err(
            "missing_field",
            message,
            expected=expected,
            rewrite=suggestion,
            normalized=edits,
        )

    for e in edits or []:
        op = e.get("op", "")
        # Text edits are evaluated in Python. Validate their shape and regex
        # syntax before reading the document; structured anchors use .NET regex.
        if op in ("prepend", "append", "replace_range", "regex_replace"):
            payload = next(
                (
                    e[field]
                    for field in ("text", "insert", "content", "replacement")
                    if e.get(field) is not None
                ),
                "",
            )
            if not isinstance(payload, str):
                return _err("missing_field", "Text edit payload must be a string")
            if op == "replace_range":
                try:
                    _validate_text_edit_shape({**e, "newText": payload})
                except ValueError as exc:
                    return _err("missing_field", str(exc))
            if op == "regex_replace" and not ordered:
                try:
                    flags = re.MULTILINE | (re.IGNORECASE if e.get("ignore_case") else 0)
                    bounded_regex._compile(e.get("pattern") or "", "", flags)
                except (ValueError, TypeError, bounded_regex.regex.error) as exc:
                    return _err("bad_regex", f"Invalid regex pattern: {exc}")
        if op == "replace_class" and not (e.get("replacement") or e.get("replacementBase64")):
            return error_with_hint(
                "replace_class requires 'replacement' (inline or base64).",
                {"op": "replace_class", "required": ["className", "replacement"]},
                {"edits[0].replacement": f"class {name} {{}}"},
            )
        if op == "replace_method":
            if not e.get("methodName"):
                return error_with_hint(
                    "replace_method requires 'methodName'.",
                    {
                        "op": "replace_method",
                        "required": ["className", "methodName", "replacement"],
                    },
                    {"edits[0].methodName": "HasTarget"},
                )
            if not (e.get("replacement") or e.get("text")):
                return error_with_hint(
                    "replace_method requires 'replacement' (inline or base64).",
                    {
                        "op": "replace_method",
                        "required": ["className", "methodName", "replacement"],
                    },
                    {"edits[0].replacement": "public bool X(){ return true; }"},
                )
        elif op == "insert_method":
            if not (e.get("replacement") or e.get("text")):
                return error_with_hint(
                    "insert_method requires a non-empty 'replacement'.",
                    {
                        "op": "insert_method",
                        "required": ["className", "replacement"],
                        "position": {
                            "after_requires": "afterMethodName",
                            "before_requires": "beforeMethodName",
                        },
                    },
                    {"edits[0].replacement": 'public void PrintSeries(){ Debug.Log("1,2,3"); }'},
                )
            pos = (e.get("position") or "").lower()
            if pos == "after" and not e.get("afterMethodName"):
                return error_with_hint(
                    "insert_method with position='after' requires 'afterMethodName'.",
                    {"op": "insert_method", "position": {"after_requires": "afterMethodName"}},
                    {"edits[0].afterMethodName": "GetCurrentTarget"},
                )
            if pos == "before" and not e.get("beforeMethodName"):
                return error_with_hint(
                    "insert_method with position='before' requires 'beforeMethodName'.",
                    {"op": "insert_method", "position": {"before_requires": "beforeMethodName"}},
                    {"edits[0].beforeMethodName": "GetCurrentTarget"},
                )
        elif op == "delete_method":
            if not e.get("methodName"):
                return error_with_hint(
                    "delete_method requires 'methodName'.",
                    {"op": "delete_method", "required": ["className", "methodName"]},
                    {"edits[0].methodName": "PrintSeries"},
                )
        elif op in ("anchor_insert", "anchor_replace", "anchor_delete"):
            if not e.get("anchor"):
                return error_with_hint(
                    f"{op} requires 'anchor' (regex).",
                    {"op": op, "required": ["anchor"]},
                    {"edits[0].anchor": "(?m)^\\s*public\\s+bool\\s+HasTarget\\s*\\("},
                )
            if (
                op in ("anchor_insert", "anchor_replace")
                and not (e.get("text") or e.get("replacement"))
                and not (
                    ordered
                    and op == "anchor_replace"
                    and any(e.get(field) is not None for field in ("text", "replacement"))
                )
            ):
                return error_with_hint(
                    f"{op} requires 'text'.",
                    {"op": op, "required": ["anchor", "text"]},
                    {"edits[0].text": "/* comment */\n"},
                )

    return None
