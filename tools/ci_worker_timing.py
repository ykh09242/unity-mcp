"""Validate and render compact observations from the optional pytest timing plugin."""

from __future__ import annotations

import json
import math
import re
from pathlib import Path
from typing import TypedDict


class WorkerTimingError(ValueError):
    """The timing input does not describe bounded, consistent worker observations."""


class WorkerTiming(TypedDict):
    id: str
    testCount: int
    elapsedSeconds: float
    collectionSeconds: float
    protocolSeconds: float
    setupSeconds: float
    callSeconds: float
    teardownSeconds: float
    firstTestSeconds: float | None
    lastTestSeconds: float | None
    finishedSeconds: float


class WorkerReport(TypedDict):
    schema: int
    distribution: str
    ordering: str
    expectedWorkers: int
    complete: bool
    elapsedSeconds: float
    workers: list[WorkerTiming]


def validate_worker_timings(value: dict) -> WorkerReport:
    """Admit bounded same-host measurements and discard raw per-file details."""
    if (
        not isinstance(value, dict)
        or type(value.get("schema")) is not int
        or value.get("schema") != 1
        or not isinstance(value.get("distribution"), str)
        or value.get("distribution") not in {"no", "loadfile"}
        or type(value.get("expectedWorkers")) is not int
        or not 1 <= value["expectedWorkers"] <= 16
        or type(value.get("complete")) is not bool
        or not isinstance(value.get("workers"), list)
        or len(value["workers"]) > value["expectedWorkers"]
    ):
        raise WorkerTimingError("Invalid worker timing report")
    ordering = value.get("ordering", "unrecorded")
    if not isinstance(ordering, str) or (
        ordering not in {"unrecorded", "case-count", "collection"}
        and not re.fullmatch(r"first-file:[A-Za-z0-9_./-]{1,200}", ordering)
    ):
        raise WorkerTimingError("Invalid worker file ordering")
    elapsed = _seconds(value.get("elapsedSeconds"))
    workers = []
    for entry in value["workers"]:
        if (
            not isinstance(entry, dict)
            or not isinstance(entry.get("id"), str)
            or not re.fullmatch(r"main|gw[0-9]{1,2}", entry["id"])
            or type(entry.get("testCount")) is not int
            or not 0 <= entry["testCount"] <= 100_000
        ):
            raise WorkerTimingError("Invalid worker timing identity")
        worker: WorkerTiming = {
            "id": entry["id"],
            "testCount": entry["testCount"],
            **{
                field: _seconds(entry.get(field))
                for field in (
                    "elapsedSeconds",
                    "collectionSeconds",
                    "protocolSeconds",
                    "setupSeconds",
                    "callSeconds",
                    "teardownSeconds",
                    "finishedSeconds",
                )
            },
            "firstTestSeconds": None,
            "lastTestSeconds": None,
        }
        for field in ("firstTestSeconds", "lastTestSeconds"):
            if entry.get(field) is not None:
                worker[field] = _seconds(entry[field])
        first, last = worker["firstTestSeconds"], worker["lastTestSeconds"]
        if (
            worker["elapsedSeconds"] > elapsed + 0.01
            or worker["elapsedSeconds"] > worker["finishedSeconds"] + 0.01
            or worker["finishedSeconds"] > elapsed + 0.01
            or worker["collectionSeconds"] + worker["protocolSeconds"]
            > worker["elapsedSeconds"] + 0.01
            or sum(worker[field] for field in ("setupSeconds", "callSeconds", "teardownSeconds"))
            > worker["protocolSeconds"] + 0.01
            or (worker["testCount"] == 0 and (first is not None or last is not None))
            or (worker["testCount"] > 0 and (first is None or last is None))
            or (
                first is not None
                and last is not None
                and not first <= last <= worker["finishedSeconds"] + 0.01
            )
        ):
            raise WorkerTimingError("Inconsistent worker timing intervals")
        workers.append(worker)
    if len({worker["id"] for worker in workers}) != len(workers) or (
        value["complete"] and len(workers) != value["expectedWorkers"]
    ):
        raise WorkerTimingError("Incomplete or duplicate worker timing inventory")
    return {
        "schema": 1,
        "distribution": value["distribution"],
        "ordering": ordering,
        "expectedWorkers": value["expectedWorkers"],
        "complete": value["complete"],
        "elapsedSeconds": elapsed,
        "workers": workers,
    }


def _seconds(value: float) -> float:
    if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
        raise WorkerTimingError("Invalid worker timing duration")
    return float(value)


def read_worker_timings(path: Path) -> WorkerReport:
    if path.stat().st_size > 2_000_000:
        raise WorkerTimingError("Worker timing output exceeds size limit")
    return validate_worker_timings(json.loads(path.read_text(encoding="utf-8")))


def render_worker_timings(value: WorkerReport) -> str:
    """Keep wait/framework overhead separate from measured testcase protocol time."""
    lines = [
        "",
        "### Pytest worker phases",
        "",
        f"Distribution: `{value['distribution']}`; ordering: `{value.get('ordering', 'unrecorded')}`; "
        f"complete: `{value['complete']}`. "
        "Collection includes imports. Other time includes scheduling waits and framework overhead; "
        "it is not a pure CPU-idle measurement. Finish gap is measured against the last testcase "
        "on any worker, using the same host's monotonic clock. Nested subprocesses are already "
        "included in their parent testcase.",
        "",
        "| Worker | Cases | Collection | Setup | Call | Teardown | Other / wait | Finish gap |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    last = max((worker["lastTestSeconds"] or 0 for worker in value["workers"]), default=0)
    for worker in value["workers"]:
        other = max(
            0, worker["elapsedSeconds"] - worker["collectionSeconds"] - worker["protocolSeconds"]
        )
        gap = last - worker["lastTestSeconds"] if worker["lastTestSeconds"] is not None else None
        gap_text = f"{gap:.2f}s" if gap is not None else "-"
        lines.append(
            f"| {worker['id']} | {worker['testCount']} | {worker['collectionSeconds']:.2f}s | "
            f"{worker['setupSeconds']:.2f}s | {worker['callSeconds']:.2f}s | "
            f"{worker['teardownSeconds']:.2f}s | {other:.2f}s | {gap_text} |"
        )
    return "\n".join([*lines, ""])
