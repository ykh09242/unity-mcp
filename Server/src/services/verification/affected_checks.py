"""Select named project checks from explicit asset mappings for a change report."""

from services.script_quality.pages import encoded_size

SELECTION_BYTES = 64 * 1024


def bounded_selection(selection):
    if encoded_size(selection) <= SELECTION_BYTES:
        return selection

    checks = [
        {
            "kind": row["kind"],
            "id": row["id"],
            "reasons": [],
            "reason_count": len(row["reasons"]),
            "reasons_truncated": False,
        }
        for row in selection["checks"]
    ]
    result = {
        **selection,
        "checks": checks,
        "unmapped_paths": [],
        "unmapped_path_count": len(selection["unmapped_paths"]),
        "unmapped_paths_truncated": False,
        "check_reason_count": sum(row["reason_count"] for row in checks),
        "evidence_truncated": True,
    }
    # Reserve every executable identity and count before admitting path previews.
    # JSON 'false' is one byte longer than 'true', so the flags below fit that reservation.
    remaining = SELECTION_BYTES - encoded_size(result)
    if remaining < 0:
        raise ValueError("candidate_selected_check_ids_limit")

    def preview(values, target, count):
        nonlocal remaining
        for value in values[:count]:
            size = encoded_size(value) + int(bool(target))
            if size > remaining:
                break
            target.append(value)
            remaining -= size
        return len(target) < len(values)

    for source, row in zip(selection["checks"], checks, strict=True):
        row["reasons_truncated"] = preview(source["reasons"], row["reasons"], 4)
    result["unmapped_paths_truncated"] = preview(
        selection["unmapped_paths"], result["unmapped_paths"], 16
    )
    return result


def select_affected_checks(affected_paths, mappings, *, broader_check=None, impact_complete=True):
    """Return the checks and their evidence. Selection does not execute a check."""
    checks = {}
    by_path = {}
    for mapping in mappings:
        entries = by_path.setdefault(mapping["path"], set())
        for field, kind in (("tests", "test"), ("scenarios", "scenario")):
            entries.update((kind, name) for name in mapping.get(field, ()))

    unmapped = []
    for path in sorted(set(affected_paths)):
        # Unity metadata belongs to the same asset, including on the old side of a move.
        entries = by_path.get(path) or by_path.get(path.removesuffix(".meta"))
        if not entries:
            unmapped.append(path)
            continue
        for key in sorted(entries):
            checks.setdefault(key, []).append(path)

    reasons = []
    if unmapped:
        reasons.append("unmapped_affected_paths")
    if not impact_complete:
        reasons.append("impact_coverage_incomplete")
    if not checks and not reasons:
        reasons.append("no_affected_paths")
    if broader_check and reasons:
        checks[("profile", broader_check)] = reasons
    return {
        "checks": [
            {"kind": kind, "id": name, "reasons": paths}
            for (kind, name), paths in sorted(checks.items())
        ],
        "unmapped_paths": unmapped,
        "mapping_complete": not reasons,
        "outstanding": reasons if not broader_check else [],
    }
