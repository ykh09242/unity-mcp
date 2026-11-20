"""Select named project checks from explicit asset mappings for a change report."""


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
