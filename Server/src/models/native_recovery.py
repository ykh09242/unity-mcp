"""Bounded native ownership evidence; diagnostics never authorize command replay."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, ValidationError

Code = Annotated[str, Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_.:-]+$")]
Message = Annotated[str, Field(min_length=1, max_length=512, pattern=r"^[^\x00-\x1f\x7f]+$")]
RecoveryAction = Literal[
    "inspect_native_work",
    "inspect_native_work_then_restart_editor",
    "wait_for_native_work",
    "wait_for_test_cleanup",
    "wait_for_compilation_start",
    "wait_for_pipeline_finish",
    "wait_for_editor_quiescence",
    "wait_for_compile_completion",
]
WAIT_ACTIONS = frozenset(
    {
        "wait_for_native_work",
        "wait_for_test_cleanup",
        "wait_for_compilation_start",
        "wait_for_pipeline_finish",
        "wait_for_editor_quiescence",
        "wait_for_compile_completion",
    }
)


class NativeRecovery(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore", strict=True)
    source: Code
    phase: Code
    reason: Code
    mode: Literal["recoverable", "restart_required", "unknown"]
    message: Message
    job_id: Code | None = Field(default=None, exclude_if=lambda value: value is None)
    generation: Code | None = Field(default=None, exclude_if=lambda value: value is None)


class NativeCoordination(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore", strict=True)
    blocked: bool | None = None
    reason: (
        Literal[
            "recovery_required",
            "native_job_active",
            "editor_compiling",
            "editor_importing",
            "unknown",
        ]
        | None
    ) = None
    retryable: bool | None = None
    recovery_action: RecoveryAction | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    recovery: NativeRecovery | None = Field(default=None, exclude_if=lambda value: value is None)


def parse_recovery(value: JsonValue) -> NativeRecovery | None:
    """Discard invalid optional diagnostics without erasing native admission evidence."""
    try:
        return NativeRecovery.model_validate(value)
    except ValidationError:
        return None


def recovery_action(
    reason: str | None, recovery: NativeRecovery | None, proposed: JsonValue = None
) -> RecoveryAction | None:
    """Require affirmative restart evidence; legacy action strings are insufficient."""
    if reason == "recovery_required":
        if recovery is not None and recovery.mode == "restart_required":
            return "inspect_native_work_then_restart_editor"
        return "inspect_native_work"
    if recovery is not None and recovery.mode == "recoverable":
        if isinstance(proposed, str) and proposed in WAIT_ACTIONS:
            return proposed
        return "wait_for_native_work"
    return None


def parse_coordination(value: JsonValue) -> NativeCoordination | None:
    """Normalize optional owner diagnostics without granting readiness from detail fields."""
    if not isinstance(value, dict):
        return None
    detail = parse_recovery(value.get("recovery"))
    reason = value.get("reason")
    try:
        result = NativeCoordination.model_validate(
            {
                "blocked": value.get("blocked"),
                "reason": reason,
                "retryable": value.get("retryable"),
                "recovery": detail,
                "recovery_action": recovery_action(reason, detail, value.get("recovery_action")),
            }
        )
    except ValidationError:
        return None
    return result
