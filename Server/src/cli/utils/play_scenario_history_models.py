"""Bounded diagnostic exports for complete journals and unavailable history evidence."""

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue


class HistoryOutcome(BaseModel):
    """One original compact journal outcome; session units may contain several iterations."""

    model_config = ConfigDict(frozen=True)
    job_id: str
    sequence: int
    first_iteration: int
    last_iteration: int
    status: str
    counts: dict[str, int]
    iteration_results_source: str = "native_journal"
    first_failure_iteration: int | None = None
    failure_code: str | None = None
    failure: dict[str, JsonValue] | None = None
    client_error: str | None = None
    started_unix_ms: int | None = None
    finished_unix_ms: int | None = None


@dataclass(frozen=True, slots=True)
class HistoryEvidence:
    """One source-local compatible journal partition with immutable original outcomes."""

    source: Path
    identity: str
    kind: Literal["session", "soak"]
    mode: str
    parent_status: str
    compatibility: dict[str, JsonValue]
    compatibility_known: bool
    complete: bool
    counts: dict[str, int]
    outcomes: tuple[HistoryOutcome, ...]
    notes: tuple[str, ...] = ()


class HistoryGroup(BaseModel):
    """Statistics never claim iteration ordering absent from a compact session batch."""

    model_config = ConfigDict(frozen=True)
    source: str
    history_id: str
    kind: str
    mode: str
    parent_status: str
    evidence_state: Literal["complete", "incomplete"]
    compatibility: dict[str, JsonValue]
    compatible_history: bool
    counts: dict[str, int]
    outcomes: list[HistoryOutcome]
    first_failure_iteration: int | None
    failure_rate_denominator: int
    failure_rate: float | None
    intermittent: bool
    longest_failure_streak: int
    trailing_failure_streak: int
    streak_unit: Literal["iteration", "process_outcome"]
    failure_frequencies: dict[str, int]
    trend: dict[str, JsonValue]
    evidence_notes: list[str] = Field(default_factory=list)


class HistoryAnalysis(BaseModel):
    model_config = ConfigDict(frozen=True)
    schema_version: Literal[1] = 1
    status: Literal["analyzed", "incomplete"]
    diagnostic_only: Literal[True] = True
    interpretation: str = (
        "Counts come from every admitted journal row, including evicted reports. "
        "Unknown and unavailable evidence never imply success. Failure rate excludes "
        "skipped and unknown iterations. Session streaks use process outcomes; compact "
        "batch counters do not establish individual iteration order. Trends are source-local."
    )
    groups: list[HistoryGroup]
    unsupported_inputs: list[dict[str, str]]

    def to_dict(self) -> dict[str, JsonValue]:
        return self.model_dump(mode="json")
