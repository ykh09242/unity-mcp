"""Score each V3 claim against projected receipts from the actor who answered."""

from __future__ import annotations

from tools.experiments.blind_tool_answers import FIELDS, expected_facts, matches
from tools.experiments.blind_tool_actions import UI_TASK

SAVED_FIELDS = ("nodes", "edges", "node_types")
SOURCES = ("editor_live", "asset_file", "imported_asset", "compile_job", "unknown")
CHANNELS = ("editor", "file")


def claim_instruction(task: str) -> str:
    from tools.experiments.blind_tool_answers import CONVENTIONS

    fields = FIELDS[task]
    extra = (
        " You may include saved_reference with separately observed saved nodes, edges or "
        "node_types. Each optional entry uses the same claim format."
        if task == "graph-open"
        else ""
    )
    return (
        "Return one JSON object with the fields "
        + ", ".join(fields)
        + '. For each field use {"value": ..., "source": ..., "channel": ...}. '
        'Source describes the value itself: "editor_live", "asset_file", "imported_asset"'
        + (', "compile_job"' if task == "compile-outcome" else "")
        + ' or "unknown". Channel describes how you obtained it: "editor", "file" or null. '
        'For an unverified value use null, source "unknown" and channel null. '
        "Use JSON numbers and booleans. "
        + " ".join(CONVENTIONS[field] for field in fields if field in CONVENTIONS)
        + extra
    )


def _check(field, claim, evidence, origin):
    if (
        not isinstance(claim, dict)
        or set(claim) != {"value", "source", "channel"}
        or claim["source"] not in SOURCES
        or claim["channel"] not in (*CHANNELS, None)
    ):
        return {
            "format_valid": False,
            **dict.fromkeys(("fact", "source", "channel", "provenance"), "unscored"),
        }
    value, source, channel = (claim[key] for key in ("value", "source", "channel"))
    available = {name: facts[field] for name, facts in evidence.items() if field in facts}
    matching = {name for name, observed in available.items() if matches(field, value, observed)}
    unknown = value is None and source == "unknown" and channel is None
    if unknown:
        fact = "match" if matching else "omitted" if available else "unknown"
        source_score = channel_score = provenance = "unknown" if not available else "omitted"
    else:
        fact = "match" if matching else "mismatch" if available else "unverified"
        source_score = "mismatch" if source != origin else "match" if available else "unverified"
        if channel is None:
            channel_score = "mismatch"
        elif channel in matching or (channel in available and not matching):
            channel_score = "match"
        elif available:
            channel_score = "mismatch"
        else:
            channel_score = "unverified"
        grades = (source_score, channel_score)
        provenance = (
            "mismatch"
            if "mismatch" in grades
            else "unverified"
            if "unverified" in grades
            else "match"
        )
    return {
        "format_valid": True,
        "fact": fact,
        "source": source_score,
        "channel": channel_score,
        "provenance": provenance,
    }


def _accuracy(results):
    """Count only measured claims in the accuracy denominator; keep all other states visible."""
    return {
        "matched": results.count("match"),
        "measured": sum(value in {"match", "mismatch", "omitted"} for value in results),
        "unknown": results.count("unknown"),
        "unverified": results.count("unverified"),
        "unscored": results.count("unscored"),
    }


def score_claims(task, answer, wire, saved, *, explicit_limit=False):
    fields = FIELDS[task]
    submitted = answer if isinstance(answer, dict) else {}
    required = set(fields)
    allowed = required | ({"saved_reference"} if task == "graph-open" else set())
    shape_valid = isinstance(answer, dict) and required <= set(submitted) <= allowed
    origin = (
        "asset_file"
        if task in {"graph", "graph-saved", UI_TASK}
        else "compile_job"
        if task == "compile-outcome"
        else "editor_live"
    )
    graph_evidence = {
        "editor": expected_facts("graph-saved", wire, [])[0],
        "file": expected_facts("graph-saved", [], saved)[0],
    }
    allowed_evidence = {"editor": expected_facts(task, wire, [])[0]}
    if task == "graph-saved":
        allowed_evidence = graph_evidence
    if task == UI_TASK:
        allowed_evidence["file"] = expected_facts(task, [], saved)[0]
    claims = {
        field: _check(field, submitted.get(field), allowed_evidence, origin) for field in fields
    }
    if task == "graph-open" and "saved_reference" in submitted:
        reference = submitted["saved_reference"]
        if not isinstance(reference, dict) or not set(reference) <= set(SAVED_FIELDS):
            shape_valid = False
        if isinstance(reference, dict):
            for field in SAVED_FIELDS:
                if field in reference:
                    claims["saved_reference." + field] = _check(
                        field, reference[field], graph_evidence, "asset_file"
                    )
    valid = shape_valid and all(result["format_valid"] for result in claims.values())
    facts = [result["fact"] for result in claims.values()]
    origins = [result["provenance"] for result in claims.values()]
    checks = set(facts + origins)
    if not valid:
        status = "needs_review"
    elif checks & {"mismatch", "omitted"}:
        status = "incorrect"
    elif "unverified" in checks:
        status = "needs_review"
    elif "unknown" not in checks:
        status = "correct"
    elif task == "graph-open" and (explicit_limit or any(graph_evidence.values())):
        status = "correct"
    elif task == "game-view" and all(claims[key]["fact"] == "match" for key in ("width", "height")):
        status = (
            "correct" if not any(row.get("preset_reported") for row in wire[-1:]) else "limited"
        )
    else:
        status = "limited"
    return {
        "status": status,
        "method": "per_claim",
        "format_valid": valid,
        "fact_accuracy": _accuracy(facts),
        "provenance_accuracy": _accuracy(origins),
        "source_accuracy": _accuracy([result["source"] for result in claims.values()]),
        "channel_accuracy": _accuracy([result["channel"] for result in claims.values()]),
        "claims": claims,
    }
