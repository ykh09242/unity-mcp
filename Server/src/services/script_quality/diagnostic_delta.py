"""Multiset diagnostic comparison using proven file and semantic identities."""

from collections import Counter, defaultdict, deque


def _identities(result):
    identities = {}
    counts = Counter(
        file.get("guid") for file in result.get("selected_files", []) if file.get("guid")
    )
    if any(count > 1 for count in counts.values()):
        return {}, "ambiguous_file_identity"
    for file in result.get("selected_files", []):
        path = file.get("path")
        if not isinstance(path, str) or path in identities:
            return {}, "ambiguous_file_identity"
        identities[path] = "guid:" + file["guid"] if file.get("guid") else "path:" + path
    return identities, None


def _key(row, identities, *, relocation=False):
    identity = identities.get(row.get("path"))
    anchor = row.get("syntax_anchor" if relocation else "anchor")
    if not identity or not anchor:
        return None
    return (
        row.get("provider"),
        row.get("provider_version"),
        row.get("id"),
        identity,
        None if relocation else row.get("symbol"),
        anchor,
        row.get("message"),
        row.get("severity"),
        row.get("suppressed", False),
    )


def compare_diagnostics(before, after):
    """Compare selected-file diagnostics only; contextual diagnostics keep their own scope."""
    delta = {
        "complete": False,
        "introduced": [],
        "existing": [],
        "resolved": [],
        "unclassified": [],
        "reasons": [],
    }
    current = after.get("diagnostics", [])
    previous = before.get("diagnostics", []) if before else []
    reasons = delta["reasons"]
    if before is None:
        reasons.append("baseline_missing")
    else:
        if before.get("project_id") != after.get("project_id"):
            reasons.append("project_changed")
        if not before.get("environment_signature") or before.get(
            "environment_signature"
        ) != after.get("environment_signature"):
            reasons.append("environment_changed")
        if any(
            item.get("complete") is not True
            or item.get("coverage_gaps")
            or item.get("analyzer_errors")
            for item in (before, after)
        ):
            reasons.append("analysis_incomplete")
        if before.get("stale") is not False or after.get("stale") is not False:
            reasons.append("source_stale")
    left, left_error = _identities(before or {})
    right, right_error = _identities(after)
    if left_error or right_error:
        reasons.append(left_error or right_error)
    elif before is not None and set(left.values()) != set(right.values()):
        reasons.append("selection_identity_changed")
    if reasons:
        delta["unclassified"] = [
            {"observation": "current", "diagnostic": row} for row in current
        ] + [{"observation": "baseline", "diagnostic": row} for row in previous]
        return delta
    available = defaultdict(deque)
    for row in previous:
        available[_key(row, left)].append(row)
    added = []
    for row in current:
        key = _key(row, right)
        if key is not None and available.get(key):
            available[key].popleft()
            delta["existing"].append(row)
        else:
            added.append(row)
    removed = [row for rows in available.values() for row in rows]
    old_relocations = {_key(row, left, relocation=True) for row in removed} - {None}
    new_relocations = {_key(row, right, relocation=True) for row in added} - {None}
    ambiguous = old_relocations & new_relocations
    for observation, rows, identities, group in (
        ("current", added, right, "introduced"),
        ("baseline", removed, left, "resolved"),
    ):
        for row in rows:
            if _key(row, identities) is None or _key(row, identities, relocation=True) in ambiguous:
                delta["unclassified"].append({"observation": observation, "diagnostic": row})
            else:
                delta[group].append(row)
    if delta["unclassified"]:
        reasons.append("ambiguous_relocation" if ambiguous else "diagnostic_identity_unavailable")
    delta["complete"] = not reasons
    return delta
