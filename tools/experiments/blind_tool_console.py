"""Observe an error-filtered Console read and check it against an independent mixed fixture."""

from collections import Counter
from hashlib import sha256
import json
import re


TASK = "console-errors"
ROUTES = {"read_console", "read_console.get"}
ERROR_TYPES = {"Error", "Exception", "Assert"}


def snapshot(state):
    fixtures = state.get("observation_fixtures") if isinstance(state, dict) else None
    data = fixtures.get(TASK) if isinstance(fixtures, dict) else None
    if not isinstance(data, dict):
        return None
    for name in ("errors", "warnings"):
        rows = data.get(name)
        if not isinstance(rows, list) or not all(isinstance(v, str) and v for v in rows):
            return None
    return {name: data[name] for name in ("errors", "warnings")}


def mixed(data):
    return bool(
        data
        and data["errors"]
        and data["warnings"]
        and set(data["errors"]).isdisjoint(data["warnings"])
    )


def permitted(args):
    # Only successful get responses can later contribute evidence. The relay pins the selector.
    return args.get("action") in (None, "get") and set(args) <= {
        "action",
        "types",
        "count",
        "cursor",
        "page_size",
        "format",
        "fields",
        "filter_text",
        "include_stacktrace",
        "include_mcp_logs",
        "unity_instance",
        "tail_cursor",
    }


def error_filter(args):
    types = args.get("types")
    if isinstance(types, str):
        try:
            types = json.loads(types)
        except ValueError:
            return False
    return (
        isinstance(types, list)
        and bool(types)
        and all(isinstance(value, str) and value.strip().lower() == "error" for value in types)
    )


def request_integer(value, default):
    if value is None:
        return default
    if isinstance(value, str) and re.fullmatch(r"[+-]?[0-9]{1,10}", value.strip()):
        value = int(value.strip())
    return value if type(value) is int and -(2**31) <= value < 2**31 else None


def requested_offset(args):
    value = request_integer(args.get("cursor"), 0)
    return max(0, value) if value is not None else -1


def page_bounds(args, data):
    """The native total includes at most one lookahead row in the current Editor."""
    cursor = data.get("cursor")
    size = data.get("pageSize")
    total = data.get("total")
    more = data.get("truncated")
    wanted_size = request_integer(args.get("page_size"), 50)
    count = len(data["items"])
    if type(cursor) is not int or cursor < 0 or cursor != requested_offset(args):
        return {}
    if type(size) is not int or wanted_size is None or size != min(500, max(1, wanted_size)):
        return {}
    if type(total) is not int or total < 0 or type(more) is not bool or "nextCursor" not in data:
        return {}
    if count != min(size, max(0, total - cursor)) or more != (total > cursor + count):
        return {}
    if data["nextCursor"] != (str(cursor + count) if more else None):
        return {}
    return {"layout": "page", "offset": cursor, "more": more, "reported_total": total}


def read_facts(args, data):
    filtered = error_filter(args)
    result = {"error_filter": filtered}
    if not filtered or args.get("tail_cursor") or args.get("filter_text"):
        return result
    paged = args.get("cursor") is not None or args.get("page_size") is not None
    if isinstance(data, list) and not paged:
        rows = data
        bounds = {"layout": "list", "offset": 0, "more": False}
    elif isinstance(data, dict) and isinstance(data.get("items"), list) and paged:
        rows = data["items"]
        bounds = page_bounds(args, data)
    else:
        return result
    if not bounds:
        return result
    messages = []
    for row in rows:
        if isinstance(row, dict):
            if not isinstance(row.get("type"), str) or row["type"] not in ERROR_TYPES:
                return result
            row = row.get("message")
        if not isinstance(row, str) or not row:
            return result
        messages.append(row)
    return {**result, **bounds, "errors": messages}


def witnessed(observations, errors):
    """A single bounded reply or contiguous, non-overlapping pages must cover the oracle."""
    pages = {}
    for row in observations:
        if row.get("error_filter") is not True or not isinstance(row.get("errors"), list):
            continue
        messages = row["errors"]
        if not all(isinstance(v, str) for v in messages):
            continue
        if row.get("layout") == "list":
            if Counter(messages) == Counter(errors):
                return True
            continue
        if (
            row.get("layout") != "page"
            or type(row.get("reported_total")) is not int
            or row["reported_total"] > len(errors)
        ):
            continue
        offset = row.get("offset")
        if type(offset) is not int or offset < 0:
            continue
        more = row.get("more")
        if type(more) is not bool:
            continue
        if offset == 0:
            pages.clear()
        pages[offset] = (messages, more)
        seen = []
        while len(seen) in pages:
            batch, more = pages[len(seen)]
            seen.extend(batch)
            if not more:
                if Counter(seen) == Counter(errors):
                    return True
                break
            if not batch:
                break
    return False


def answer_match(text, expected):
    """Avoid credit for merely mentioning the messages in arbitrary or contradictory prose."""
    if not text:
        return "missing"
    lines = text.strip().splitlines()
    if len(lines) >= 2 and lines[0] in ("```", "```text", "```json") and lines[-1] == "```":
        lines = lines[1:-1]
    try:
        answer = json.loads("\n".join(lines))
    except ValueError:
        answer = None
    if isinstance(answer, dict) and set(answer) == {"errors"}:
        answer = answer["errors"]
    if isinstance(answer, list) and all(isinstance(msg, str) for msg in answer):
        return "correct" if Counter(answer) == Counter(expected["errors"]) else "incorrect"
    messages = []
    for line in lines:
        if not line.strip():
            continue
        stripped = re.sub(r"^(?:[-*+] |\d+[.)] )", "", line.strip())
        if stripped.startswith("`") and stripped.endswith("`"):
            stripped = stripped[1:-1]
        messages.append(stripped)
    if Counter(messages) == Counter(expected["errors"]):
        return "correct"
    if set(messages).intersection(expected["warnings"]):
        return "incorrect"
    return "needs_review"


def outcome(case, metadata, observations, actor_text, *, actor_verified, project_verified, scoped):
    before = snapshot(metadata.get("editor_start_state"))
    after = snapshot(metadata.get("editor_end_state"))
    available = mixed(before)
    retained = (
        all(Counter(before[name]) <= Counter(after[name]) for name in before)
        if available and after is not None
        else None
    )
    observed = witnessed(observations, before["errors"]) if available and actor_verified else None
    answer = answer_match(actor_text, before) if available and actor_verified else "unverified"
    digest = sha256(
        json.dumps([actor_text, before, after, observations], sort_keys=True).encode()
    ).hexdigest()
    if answer == "needs_review":
        try:
            review = json.loads((case / "answer-review.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            review = None
        if (
            isinstance(review, dict)
            and review.get("review_digest") == digest
            and review.get("status") in ("correct", "incorrect", "limited")
            and isinstance(review.get("reason"), str)
            and review["reason"].strip()
        ):
            answer = review["status"]
    stable = (
        Counter(before["errors"]) == Counter(after["errors"])
        if available and after is not None
        else None
    )
    verified = bool(
        available
        and actor_verified
        and project_verified
        and observed
        and scoped
        and retained
        and stable
    )
    status = (
        "incorrect"
        if answer == "incorrect" or retained is False
        else answer
        if verified or answer == "missing"
        else "needs_review"
    )
    return {
        "status": status,
        "method": "mixed_console_oracle",
        "review_digest": digest,
        "answer_match": answer,
        "fixture_mixed": available,
        "retained": retained,
        "errors_stable": stable,
        "error_filtered_receipt_matches": observed,
        "project_verified": project_verified,
        "actor_verified": actor_verified,
        "scoped_observations": scoped,
    }
