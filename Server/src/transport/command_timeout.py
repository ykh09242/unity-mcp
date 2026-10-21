"""Finite explicit operation budgets shared by HTTP and stdio."""

import math
from typing import Any

MAX_COMMAND_TIMEOUT = 24 * 60 * 60


def requested_command_timeout(params: dict[str, Any]) -> float | None:
    """Read either public spelling without letting invalid values disable a deadline."""
    requested = params.get("timeout_seconds")
    if requested is None:
        requested = params.get("timeoutSeconds")
    if isinstance(requested, bool):
        return None
    try:
        seconds = float(requested)
    except (TypeError, ValueError, OverflowError):
        return None
    return min(seconds, MAX_COMMAND_TIMEOUT) if math.isfinite(seconds) and seconds > 0 else None
