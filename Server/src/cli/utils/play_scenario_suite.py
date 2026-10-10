"""Explicitly wait for one native suite with bounded cancellation finalization."""

import math
from time import monotonic, sleep
from collections.abc import Callable
from typing import Final, Literal

from cli.utils.connection import UnityCommandError, UnityConnectionError
from pydantic import BaseModel, ConfigDict, JsonValue

TERMINAL: Final = frozenset({"succeeded", "failed", "timed_out", "cancelled"})
SuiteRequest = Callable[[dict[str, JsonValue], int], dict[str, JsonValue]]


class SuiteObservation(BaseModel):
    """The response boundary proves suite identity and lifecycle status."""

    model_config = ConfigDict(frozen=True, extra="ignore")
    suite_id: str
    status: Literal["running", "succeeded", "failed", "timed_out", "cancelled"]


def _report(response: dict[str, JsonValue], suite_id: str) -> dict[str, JsonValue]:
    """Reject malformed or misrouted observations instead of treating them as completion."""
    data = response.get("data")
    if not isinstance(data, dict):
        message = "Suite response has no report"
        raise ValueError(message)
    observation = SuiteObservation.model_validate(data)
    if observation.suite_id != suite_id:
        message = "Suite response identity does not match the requested suite"
        raise ValueError(message)
    return data


def wait_for_suite(
    request: SuiteRequest,
    parameters: dict[str, JsonValue],
    *,
    timeout_seconds: int,
    cleanup_wait_seconds: int,
    poll_interval_seconds: float,
    request_timeout: int,
) -> dict[str, JsonValue]:
    """Start once, then observe; interruption or observation failure cancels exactly once."""
    suite_id = str(parameters["suite_id"])
    deadline = monotonic() + timeout_seconds
    last: dict[str, JsonValue] = {"suite_id": suite_id, "status": "running", "scenarios": []}
    client_error: str | None = None
    cancellation_sent = False
    # Deliberately mutable: this loop accumulates the latest receipt and cancellation state.
    try:
        last = _report(request(parameters, min(request_timeout, timeout_seconds)), suite_id)
    except UnityCommandError:
        # A definite admission rejection did not reserve the Editor or start a child.
        raise
    except (UnityConnectionError, ValueError, KeyboardInterrupt) as exc:
        client_error = "Interrupted" if isinstance(exc, KeyboardInterrupt) else str(exc)
        deadline = monotonic()
    while True:
        if last.get("status") in TERMINAL and client_error is None:
            return last
        remaining = deadline - monotonic()
        if remaining <= 0 and not cancellation_sent:
            client_error = client_error or "Client suite execution deadline exceeded"
            cancellation_sent = True
            deadline = monotonic() + cleanup_wait_seconds
            try:
                last = _report(
                    request(
                        {"action": "suite_cancel", "suite_id": suite_id},
                        min(request_timeout, cleanup_wait_seconds),
                    ),
                    suite_id,
                )
            except (UnityConnectionError, UnityCommandError, ValueError, KeyboardInterrupt) as exc:
                client_error += "; cancellation receipt unavailable: " + str(exc)
            continue
        if last.get("status") in TERMINAL:
            return {**last, "client_error": client_error}
        if remaining <= 0:
            return {
                **last,
                "client_error": client_error
                + "; finalization was not observed within cleanup grace",
            }
        try:
            sleep(min(poll_interval_seconds, remaining))
            remaining = deadline - monotonic()
            if remaining <= 0:
                continue
            last = _report(
                request(
                    {"action": "suite_status", "suite_id": suite_id},
                    max(1, min(request_timeout, math.ceil(remaining))),
                ),
                suite_id,
            )
        except (UnityConnectionError, UnityCommandError, ValueError, KeyboardInterrupt) as exc:
            detail = "Interrupted" if isinstance(exc, KeyboardInterrupt) else str(exc)
            client_error = client_error or detail
            if cancellation_sent:
                return {
                    **last,
                    "client_error": client_error + "; finalization receipt unavailable: " + detail,
                }
            deadline = monotonic()
