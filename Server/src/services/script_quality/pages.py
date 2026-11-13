"""Bounded native-page assembly and source-bound public result views."""

from dataclasses import dataclass
import hashlib
import hmac
import json
from struct import Struct

from models.script_quality import ScriptSelection
from services.script_quality.diagnostic_delta import compare_diagnostics


class InconsistentResult(ValueError):
    """A page cannot be attributed to the same completed native observation."""


def encoded_size(value):
    return len(
        json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()
    )


async def collect_result(first, job_id, fetch, bind_observation):
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
    generation = data.get("editor_generation")
    header_bytes = encoded_size(header)
    if (
        type(total) is not int
        or not 0 <= total <= 10000
        or header_bytes > 512 * 1024
        or not isinstance(generation, str)
        or not 0 < len(generation) <= 128
    ):
        raise InconsistentResult()
    bind_observation(
        hashlib.sha256(
            json.dumps([generation, header], sort_keys=True, separators=(",", ":")).encode()
        ).digest()
    )
    primary, context, offset, seen = [], [], 0, set()
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
        if isinstance(response, dict) and response.get("success") is False:
            detail = response.get("data")
            reason = detail.get("reason") if isinstance(detail, dict) else None
            if reason in (
                "outcome_unknown",
                "command_deadline_exceeded",
                "command_not_dispatched",
                "stale_connection",
                "read_invalidated",
                "operation_target_changed",
                "no_unity_session",
            ) or (detail is None and isinstance(response.get("error"), str)):
                # Legacy stdio can return a connection exception as a reasonless MCP error.
                raise ConnectionError()
    raise InconsistentResult()


_CATEGORIES = ("introduced", "existing", "resolved", "unclassified")
_ROW_REFERENCE = Struct("<i")


@dataclass(frozen=True)
class DiagnosticView:
    revision: str
    complete: bool
    reasons: tuple[str, ...]
    counts: tuple[int, ...]
    references: bytes

    @property
    def byte_size(self):
        return len(self.references) + encoded_size(
            [self.revision, self.complete, self.reasons, self.counts]
        )

    def row(self, offset, record, baseline):
        index = offset
        for kind, count in zip(_CATEGORIES, self.counts):
            if index < count:
                # Without a baseline the sole category has native selected-row order.
                reference = (
                    _ROW_REFERENCE.unpack_from(self.references, offset * _ROW_REFERENCE.size)[0]
                    if self.references
                    else offset
                )
                source = baseline if reference < 0 else record
                row = {
                    "classification": kind,
                    "scope": "selected",
                    "diagnostic": source.read_row(~reference if reference < 0 else reference),
                }
                if kind == "unclassified":
                    row["observation"] = "baseline" if reference < 0 else "current"
                return row
            index -= count
        return {
            "classification": "context",
            "scope": "context",
            "diagnostic": record.read_row(record.selected_count + index),
        }


def classify_view(identity, result, baseline):
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
    references = bytearray()
    if baseline is not None:
        # Nonnegative offsets reference current rows. Signed offsets -(index + 1)
        # reference baseline rows without keeping its payload alive past eviction.
        current = {id(row): i for i, row in enumerate(result["diagnostics"])}
        previous = {id(row): ~i for i, row in enumerate(baseline["diagnostics"])}
        for kind in _CATEGORIES:
            for item in delta[kind]:
                if kind == "unclassified":
                    source = previous if item["observation"] == "baseline" else current
                    row = item["diagnostic"]
                else:
                    source = previous if kind == "resolved" else current
                    row = item
                references.extend(_ROW_REFERENCE.pack(source[id(row)]))
    return DiagnosticView(
        revision,
        delta["complete"],
        tuple(delta["reasons"]),
        tuple(len(delta[kind]) for kind in _CATEGORIES),
        bytes(references),
    )


def result_page(record, baseline, view, header, *, identity, owner, secret, size, cursor=None):
    def signature(offset):
        return hmac.new(
            secret,
            json.dumps([owner, identity, view.revision, offset], separators=(",", ":")).encode(),
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
    total = sum(view.counts) + header["total_rows"] - record.selected_count
    if start > total:
        raise InconsistentResult("invalid_analysis_cursor")
    header = dict(
        header,
        page_revision=view.revision,
        classification_complete=view.complete,
        classification_reasons=list(view.reasons),
        counts=dict(zip(_CATEGORIES, view.counts)),
        total_rows=total,
        offset=start,
    )
    budget = encoded_size(header) + 4096
    page, offset = [], start
    while offset < total and len(page) < size:
        row = view.row(offset, record, baseline)
        length = encoded_size(row)
        if budget + length > 512 * 1024:
            break
        budget += length
        page.append(row)
        offset += 1
    if budget > 512 * 1024 or offset == start and offset < total:
        raise InconsistentResult("analysis_page_limit")
    return {
        **header,
        "rows": page,
        "next_cursor": f"{offset}.{signature(offset)}" if offset < total else None,
    }
