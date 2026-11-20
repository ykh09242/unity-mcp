"""Compatibility imports for play scenario report json."""

from unity_mcp_evidence.play_scenario_report_json import (
    REPORT_LIMIT,
    JsonValue,
    QueryComparisonError,
    _check_depth,
    _finite_float,
    _object,
    _reject_constant,
    read_report,
)

# Keep the session journal decoder's validation imports available at the old path.
__all__ = [
    "JsonValue",
    "QueryComparisonError",
    "REPORT_LIMIT",
    "_check_depth",
    "_finite_float",
    "_object",
    "_reject_constant",
    "read_report",
]
