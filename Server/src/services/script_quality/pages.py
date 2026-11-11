"""Bounded native-page assembly and source-bound public result views."""

import hashlib
import hmac
import json

from models.script_quality import ScriptSelection
from services.script_quality.diagnostic_delta import compare_diagnostics


class InconsistentResult(ValueError):
    """A page cannot be attributed to the same completed native observation."""


def encoded_size(value):
    return len(
        json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()
    )


async def collect_result(first, job_id, fetch):
    data = first.get("data")
    if not isinstance(data, dict) or data.get("job_id") != job_id:
        raise InconsistentResult()
    result = data.get("result")
    if not isinstance(result, dict) or result.get("protocol") != 1:
        raise InconsistentResult()
    token = result.get("source_token")
    if (
        not isinstance(token, str)
        or len(token) != 64
        or any(c not in "0123456789abcdef" for c in token)
    ):
        raise InconsistentResult()
    if type(result.get("complete")) is not bool or type(result.get("stale")) is not bool:
        raise InconsistentResult()
    if any(
        not isinstance(result.get(key), str) or not result[key] or len(result[key]) > 128
        for key in ("project_id", "environment_signature", "context_digest")
    ):
        raise InconsistentResult()
    files = result.get("selected_files")
    if not isinstance(files, list):
        raise InconsistentResult()
    try:
        selected = set(ScriptSelection(paths=[file["path"] for file in files]).paths)
    except (ValueError, KeyError, TypeError) as error:
        raise InconsistentResult() from error
    for file in files:
        if file.get("guid") is not None and (
            not isinstance(file["guid"], str)
            or len(file["guid"]) != 32
            or any(c not in "0123456789abcdef" for c in file["guid"])
        ):
            raise InconsistentResult()
        if not isinstance(file.get("bytes_sha256"), str) or len(file["bytes_sha256"]) != 64:
            raise InconsistentResult()
    header = {
        key: value for key, value in result.items() if key not in {"rows", "offset", "next_cursor"}
    }
    total = header.get("total_rows")
    if type(total) is not int or not 0 <= total <= 10000 or encoded_size(header) > 512 * 1024:
        raise InconsistentResult()
    primary, context, offset, seen = [], [], 0, set()
    header_bytes = encoded_size(header)
    bytes_seen = header_bytes
    response = first
    for _ in range(64):
        page_data = response.get("data")
        if not isinstance(page_data, dict) or response.get("success") is not True:
            raise InconsistentResult()
        page = page_data.get("result")
        if (
            page_data.get("job_id") != job_id
            or page_data.get("state") != "completed"
            or page_data.get("terminal") is not True
            or page_data.get("editor_generation") != data.get("editor_generation")
            or not isinstance(page, dict)
            or page.get("offset") != offset
            or {
                key: value
                for key, value in page.items()
                if key not in {"rows", "offset", "next_cursor"}
            }
            != header
        ):
            raise InconsistentResult()
        rows = page.get("rows")
        if not isinstance(rows, list) or len(rows) > 500 or encoded_size(response) > 512 * 1024:
            raise InconsistentResult()
        for item in rows:
            if not isinstance(item, dict) or item.get("scope") not in {"selected", "context"}:
                raise InconsistentResult()
            row = item.get("diagnostic")
            if not isinstance(row, dict) or any(
                not isinstance(row.get(key), str)
                for key in ("id", "provider", "provider_version", "severity", "message", "anchor")
            ):
                raise InconsistentResult()
            if row.get("symbol") is not None and not isinstance(row["symbol"], str):
                raise InconsistentResult()
            if row.get("syntax_anchor") is not None and not isinstance(row["syntax_anchor"], str):
                raise InconsistentResult()
            if row.get("path") is not None and not isinstance(row["path"], str):
                raise InconsistentResult()
            if type(row.get("suppressed", False)) is not bool:
                raise InconsistentResult()
            if item["scope"] == "selected" and row.get("path") not in selected:
                raise InconsistentResult()
            bytes_seen += encoded_size(row)
            if bytes_seen > 9 * 1024 * 1024 or bytes_seen - header_bytes > 8 * 1024 * 1024:
                raise InconsistentResult()
            (primary if item["scope"] == "selected" else context).append(row)
        offset += len(rows)
        if offset > total:
            raise InconsistentResult()
        cursor = page.get("next_cursor")
        if cursor is None:
            if offset != total:
                raise InconsistentResult()
            return {**header, "diagnostics": primary, "context_diagnostics": context}
        if not rows or not isinstance(cursor, str) or len(cursor) > 256 or cursor in seen:
            raise InconsistentResult()
        seen.add(cursor)
        response = await fetch(cursor)
    raise InconsistentResult()


def result_page(result, baseline, *, identity, owner, secret, size, cursor=None):
    delta = compare_diagnostics(baseline, result)
    revision = hashlib.sha256(
        json.dumps(
            [
                identity,
                result["source_token"],
                baseline.get("source_token") if baseline else None,
                result.get("environment_signature"),
                delta["reasons"],
            ],
            separators=(",", ":"),
        ).encode()
    ).hexdigest()

    def signature(offset):
        return hmac.new(
            secret,
            json.dumps([owner, identity, revision, offset], separators=(",", ":")).encode(),
            hashlib.sha256,
        ).hexdigest()

    start = 0
    if cursor:
        parts = cursor.split(".")
        if len(parts) != 2 or not parts[0].isdigit() or len(parts[0]) > 8:
            raise InconsistentResult("invalid_analysis_cursor")
        start = int(parts[0])
        if not hmac.compare_digest(parts[1], signature(start)):
            raise InconsistentResult("invalid_analysis_cursor")
    rows = [
        {"classification": kind, "scope": "selected", "diagnostic": row}
        for kind in ("introduced", "existing", "resolved")
        for row in delta[kind]
    ]
    rows += [
        {"classification": "unclassified", "scope": "selected", **item}
        for item in delta["unclassified"]
    ]
    rows += [
        {"classification": "context", "scope": "context", "diagnostic": row}
        for row in result.get("context_diagnostics", [])
    ]
    if start > len(rows):
        raise InconsistentResult("invalid_analysis_cursor")
    header = {
        key: value
        for key, value in result.items()
        if key not in {"diagnostics", "context_diagnostics", "total_rows"}
    }
    header.update(
        page_revision=revision,
        classification_complete=delta["complete"],
        classification_reasons=delta["reasons"],
        counts={
            name: len(delta[name])
            for name in ("introduced", "existing", "resolved", "unclassified")
        },
        total_rows=len(rows),
        offset=start,
    )
    budget = encoded_size(header) + 4096
    page, offset = [], start
    while offset < len(rows) and len(page) < size:
        length = encoded_size(rows[offset])
        if budget + length > 512 * 1024:
            break
        budget += length
        page.append(rows[offset])
        offset += 1
    if budget > 512 * 1024 or offset == start and offset < len(rows):
        raise InconsistentResult("analysis_page_limit")
    return {
        **header,
        "rows": page,
        "next_cursor": f"{offset}.{signature(offset)}" if offset < len(rows) else None,
    }
