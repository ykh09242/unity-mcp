"""Bounded admission for the independent SPEC10 profiler memory read actions."""

MEMORY_ANALYSIS_ACTIONS = [
    "memory_aggregate",
    "memory_analyze_snapshots",
    "memory_snapshot_reader_status",
]


def validate_memory_analysis(
    action,
    *,
    targets=None,
    max_objects=None,
    include_references=None,
    include_objects=None,
    object_limit=None,
    name_prefix=None,
    snapshot_a=None,
    snapshot_b=None,
):
    """Reject malformed scopes before transport; native identity/path checks remain authoritative."""
    if action == "memory_snapshot_reader_status":
        return None
    if action == "memory_aggregate":
        if not isinstance(targets, list) or not 1 <= len(targets) <= 256:
            return "targets must contain 1–256 loaded hierarchy GameObject session IDs."
        for identity in targets:
            if not isinstance(identity, str):
                return "Each target must be a GameObject session ID string."
            try:
                value = int(identity)
            except ValueError:
                return "Each target must be a GameObject session ID string."
            if not -2147483648 <= value <= 2147483647 or value == 0:
                return "Target ID is outside the nonzero session-handle range."
        maximum = 1000000
    else:
        for name, path in (("snapshot_a", snapshot_a), ("snapshot_b", snapshot_b)):
            if not isinstance(path, str) or not path.strip():
                return f"{name} must be a nonempty snapshot path."
        if name_prefix is not None and (not isinstance(name_prefix, str) or len(name_prefix) > 128):
            return "name_prefix must be a string of at most 128 characters."
        maximum = 1000000
    for name, value, bound in (
        ("max_objects", max_objects, maximum),
        ("object_limit", object_limit, 256),
    ):
        if value is not None and (type(value) is not int or not 1 <= value <= bound):
            return f"{name} must be an integer between 1 and {bound}."
    for name, value in (
        ("include_references", include_references),
        ("include_objects", include_objects),
    ):
        if value is not None and type(value) is not bool:
            return f"{name} must be a boolean."
    return None
