"""Adjudicate final answer claims independently of route selection."""

from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path

GAME_VIEW = "manage_editor.get_game_view_size"
GAME_VIEW_RESOURCE = "mcpforunity://editor/game-view-size"
SIZE_ROUTES = {GAME_VIEW, GAME_VIEW_RESOURCE, "get_game_view_size"}
RAYCAST = "manage_ugui.diagnose_raycast"

FIELDS = {
    "game-view": ("width", "height", "preset"),
    "graph": ("data_state", "nodes", "edges", "node_types"),
    "graph-saved": ("data_state", "nodes", "edges", "node_types"),
    "graph-open": ("data_state", "nodes", "edges", "node_types"),
    "raycast": ("center", "hit_status", "first_hit"),
    "editor": ("playing", "compiling", "scene"),
    "context": ("project_root", "unity_version", "render_pipeline"),
    "compile-current": ("compiling",),
    "active-scene": ("scene",),
    "compile-outcome": ("outcome",),
    "pointer-order": ("hit_status", "first_hit"),
}

CONVENTIONS = {
    "data_state": 'Use "saved" for serialized file contents and "open" for observed in-memory contents.',
    "node_types": "Use an array of two distinct observed type identifiers, or null.",
    "center": "Use an ordered [x, y] array in pixels.",
    "hit_status": 'Use "hit", "empty" or null for the ordered pointer query. first_hit is the exact first name.',
    "scene": "Use the exact active scene name.",
    "preset": "Use the selected preset label.",
    "outcome": 'Use "success", "failed" or null for the specific request.',
}


def answer_instruction(task: str, protocol: str = "v1") -> str:
    if protocol == "v3":
        from tools.experiments.blind_tool_claims import claim_instruction

        return claim_instruction(task)
    if protocol == "v2":
        keys = ("channel", "source", *FIELDS[task])
        return (
            "Return one JSON object, with exactly these keys: "
            + ", ".join(keys)
            + '. For channel use "editor" when the Editor returned the response or "file" when '
            'you directly read a file. For source use "editor_live" for live in-memory state or '
            '"asset_file" for serialized asset contents, regardless of channel. Use JSON null for '
            "each unknown or unavailable field. Use JSON numbers and booleans. "
            + " ".join(CONVENTIONS[k] for k in keys if k in CONVENTIONS)
        )
    keys = ("source", *FIELDS[task])
    notes = " ".join(CONVENTIONS[k] for k in keys if k in CONVENTIONS)
    return (
        "For scoring, return one JSON object, with exactly these keys: "
        + ", ".join(keys)
        + '. Use "editor" or "file" for the source you actually observed, and JSON null '
        "for each unknown or unavailable value. Use JSON numbers and booleans. " + notes
    )


def current_view(wire: list[dict]) -> tuple[dict, dict]:
    """Use the last size receipt and the last center query following that receipt."""
    size, hit = {}, {}
    center = None
    for row in wire:
        if row.get("route") in SIZE_ROUTES or "render_size" in row or "preset_reported" in row:
            size, hit = row, {}
            center = [n / 2 for n in row["render_size"]] if row.get("render_size") else None
        elif (row.get("route") == RAYCAST or "point" in row) and (
            row.get("point") is None or row.get("point") == center
        ):
            hit = row
    return size, hit


def observed_center_hit(hit: dict) -> bool:
    return bool(
        hit.get("status") == "observed"
        and hit.get("policy_safe")
        and hit.get("native_query_attempted")
        and hit.get("ordered_hits")
        and hit.get("point")
    )


def expected_facts(task: str, wire: list[dict], saved: list[dict]) -> tuple[dict, set[str]]:
    """Only derive claims that the specific observation established."""
    expected = {}
    sources = {"editor"} if wire else set()
    if task.startswith("graph"):
        if saved and task != "graph":
            sources.add("file")
        if task != "graph-open":
            rows = [*wire, *(saved if task == "graph-saved" else [])]
            totals = {(r["nodes"], r["edges"]) for r in rows if "nodes" in r and "edges" in r}
            if len(totals) == 1:
                expected["data_state"] = "saved"
                expected["nodes"], expected["edges"] = totals.pop()
                types = sorted({t for r in rows for t in r.get("node_types", [])})
                if len(types) >= 2:
                    expected["node_types"] = types
        return expected, sources
    if task in {"game-view", "raycast"}:
        size, hit = current_view(wire)
        if task == "game-view":
            if "render_size" in size:
                expected["width"], expected["height"] = size["render_size"]
            if "preset_label" in size:
                expected["preset"] = size["preset_label"]
        elif "render_size" in size:
            expected["center"] = [n / 2 for n in size["render_size"]]
            if observed_center_hit(hit) and hit.get("hits_total") == 0:
                expected["hit_status"] = "empty"
                expected["first_hit"] = None
            elif observed_center_hit(hit) and hit.get("first_hit"):
                expected["hit_status"] = "hit"
                expected["first_hit"] = hit["first_hit"]
        return expected, sources
    for row in wire:
        if task in {"compile-current", "active-scene"}:
            expected.clear()
            if (
                row.get("fresh") is True
                and type(row.get("compiling")) is bool
                and task == "compile-current"
            ):
                expected["compiling"] = row["compiling"]
            if task == "active-scene" and row.get("fresh", True) is True and row.get("scene_name"):
                expected["scene"] = row["scene_name"]
        if task == "compile-outcome":
            expected.clear()
            if row.get("outcome") in ("success", "failed"):
                expected["outcome"] = row["outcome"]
        if task == "pointer-order":
            expected.clear()
            if observed_center_hit(row):
                if row.get("hits_total") == 0:
                    expected.update(hit_status="empty", first_hit=None)
                elif row.get("first_hit"):
                    expected.update(hit_status="hit", first_hit=row["first_hit"])
        if task == "editor":
            if "fresh" in row:
                expected.clear()
            if row.get("fresh"):
                expected.update({k: row[k] for k in ("playing", "compiling") if k in row})
            if (row.get("fresh") or "fresh" not in row) and row.get("scene_name"):
                expected["scene"] = row["scene_name"]
        if task == "context":
            expected.clear()
            expected.update({k: row[k] for k in FIELDS[task] if k in row})
    return expected, sources


def matches(key: str, answer: object, expected: object) -> bool:
    if key == "node_types":
        return (
            isinstance(answer, list)
            and len(answer) == 2
            and all(isinstance(x, str) for x in answer)
            and len(set(answer)) == 2
            and set(answer).issubset(expected)
        )
    if key == "center":
        return (
            isinstance(answer, list)
            and len(answer) == 2
            and all(type(x) in (int, float) for x in answer)
            and answer == expected
        )
    if key == "project_root" and isinstance(answer, str):
        return answer.replace("\\", "/").rstrip("/") == expected.replace("\\", "/").rstrip("/")
    return type(answer) is type(expected) and answer == expected


def final_answer(
    case: Path,
    task: str,
    wire: list[dict],
    saved: list[dict],
    host_answer: str = "",
    *,
    protocol: str = "v1",
    explicit_limit: bool = False,
    actor_answer: str | None = None,
) -> dict:
    """Bind optional human reviews to the actual answer and evidence."""
    answer_path = case / "answer.txt"
    if actor_answer is not None:
        text = actor_answer.strip()
    else:
        text = (
            answer_path.read_text(encoding="utf-8-sig") if answer_path.is_file() else host_answer
        ).strip()
    if not text:
        if protocol == "v3":
            from tools.experiments.blind_tool_claims import score_claims

            return {
                **score_claims(task, None, wire, saved),
                "status": "missing",
                "protocol": protocol,
            }
        return {"status": "missing", "protocol": protocol}
    expected, sources = expected_facts(task, wire, saved)
    signature = [task, text, wire, saved]
    if protocol != "v1":
        signature.append(protocol)
    if protocol == "v3":
        signature.append(explicit_limit)
    receipt = sha256(json.dumps(signature, sort_keys=True).encode()).hexdigest()
    result = {"status": "needs_review", "review_digest": receipt, "protocol": protocol}
    review_path = case / "answer-review.json"
    if review_path.exists():
        try:
            review = json.loads(review_path.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            review = None
        if (
            isinstance(review, dict)
            and review.get("review_digest") == receipt
            and isinstance(review.get("reason"), str)
            and review["reason"].strip()
            and review.get("status") in {"correct", "incorrect", "limited", "needs_review"}
        ):
            reviewed = {
                **result,
                "status": review["status"],
                "method": "review",
                "reason": review["reason"],
            }
            if protocol != "v3":
                return reviewed
            result["review"] = {"status": review["status"], "reason": review["reason"]}
    try:
        lines = text.splitlines()
        payload = (
            "\n".join(lines[1:-1])
            if len(lines) >= 3 and lines[0].lower() in {"```", "```json"} and lines[-1] == "```"
            else text
        )
        answer = json.loads(payload)
    except ValueError:
        if protocol == "v3":
            answer = None
        else:
            return result
    if protocol == "v3":
        from tools.experiments.blind_tool_claims import score_claims

        scored = score_claims(task, answer, wire, saved, explicit_limit=explicit_limit)
        return {**result, **scored}
    if not isinstance(answer, dict):
        return result
    fields = FIELDS[task]
    keys = {"source", *fields}
    if protocol == "v2":
        keys.add("channel")
    if protocol not in {"v1", "v2"} or not isinstance(answer, dict) or set(answer) != keys:
        return result
    if all(answer[k] is None for k in answer):
        return {
            **result,
            "status": "limited" if not expected else "incorrect",
            "method": "structured",
        }
    checks = {}
    channel = answer["channel"] if protocol == "v2" else answer["source"]
    if protocol == "v2":
        checks["channel"] = (
            "match" if isinstance(channel, str) and channel in sources else "unverified"
        )
        origin = "asset_file" if task.startswith("graph") else "editor_live"
        if sources and answer["source"] is not None:
            checks["source"] = "match" if answer["source"] == origin else "mismatch"
        elif answer["source"] is None and all(answer[field] is None for field in fields):
            checks["source"] = "unknown"
        else:
            checks["source"] = "unverified"
        if (
            task == "graph"
            and channel == "file"
            and any(answer[field] is not None for field in ("nodes", "edges", "node_types"))
        ):
            checks["channel"] = "unsupported"
    elif task.startswith("graph") or not isinstance(channel, str) or channel not in sources:
        # V1 used one "source" field for both transport and data origin. Preserve
        # the facts but require review before crediting graph provenance.
        checks["source"] = "unverified"
    if task == "graph-saved" and protocol == "v2":
        expected, _ = expected_facts(
            task, wire if channel == "editor" else [], saved if channel == "file" else []
        )
    current_size, current_ray = current_view(wire)
    for key in fields:
        value = answer[key]
        if task == "graph-open" and key == "data_state" and value == "saved" and (saved or wire):
            checks[key] = "match"
        elif task == "graph-open" and value is not None:
            checks[key] = "unsupported"
        elif (
            task == "game-view"
            and key == "preset"
            and value is not None
            and current_size.get("render_size")
            and "preset" not in expected
            and not current_size.get("preset_reported")
        ):
            checks[key] = "unsupported"
        elif (
            task == "raycast"
            and key in {"first_hit", "hit_status"}
            and value is not None
            and key not in expected
            and current_ray.get("status") == "unknown"
        ):
            checks[key] = "unsupported"
        elif key in expected:
            checks[key] = "match" if matches(key, value, expected[key]) else "mismatch"
        elif value is None:
            checks[key] = "unknown"
        else:
            checks[key] = "unverified"
    if {"mismatch", "unsupported"}.intersection(checks.values()):
        status = "incorrect"
    elif "unverified" in checks.values():
        status = "needs_review"
    elif "unknown" in checks.values():
        only_preset = task == "game-view" and [k for k, v in checks.items() if v == "unknown"] == [
            "preset"
        ]
        status = "correct" if only_preset and not current_size.get("preset_reported") else "limited"
    else:
        status = "correct"
    return {**result, "status": status, "method": "structured", "claims": checks}
