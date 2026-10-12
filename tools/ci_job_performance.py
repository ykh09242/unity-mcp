"""Observe complete CI workload cohorts, independently of reporting jobs and billing."""

from dataclasses import asdict, dataclass
from datetime import datetime
import hashlib
import json
import math
import os
from pathlib import Path
import re
from statistics import median
from typing import TypedDict


MAX_JOBS = 300
MAX_SAMPLES = 5
MIN_SAMPLES = 3


class WorkloadError(ValueError):
    """A workload observation is incomplete, incompatible or malformed."""


def text(value: str) -> str:
    """Escape API-derived names in Markdown tables."""
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace("|", "&#124;")
        .replace("`", "&#96;")
        .replace("\r", " ")
        .replace("\n", " ")
    )


def timestamp(value: str) -> float:
    """Accept timezone-aware ISO API timestamps, never numeric or naive dates."""
    if not isinstance(value, str):
        raise WorkloadError("Workload timestamps require ISO strings")
    instant = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if instant.tzinfo is None:
        raise WorkloadError("Workload timestamps require timezone offsets")
    result = instant.timestamp()
    if not math.isfinite(result):
        raise WorkloadError("Workload timestamps must be finite")
    return result


def identity(value: int) -> int:
    if type(value) is not int or value <= 0:
        raise WorkloadError("Workload IDs and attempts must be positive integers")
    return value


@dataclass(frozen=True, slots=True)
class JobInterval:
    """A single latest completed job; timestamps retain API provenance."""

    name: str
    job_id: int
    attempt: int
    started_at: str
    completed_at: str
    conclusion: str


@dataclass(frozen=True, slots=True)
class WorkloadContext:
    """Stable selection and platform identity used for matching history."""

    workflow_id: int
    event: str
    branch: str
    scope: str
    jobs: tuple[str, ...]
    platforms: tuple[tuple[str, tuple[str, ...]], ...]
    selection: str
    configured_concurrency: int | None = None


class WorkloadRecord(TypedDict):
    schema: int
    runId: int
    runAttempt: int
    context: dict
    jobs: list[dict]
    excludedJobs: list[str]
    successful: bool
    elapsedSpanSeconds: float
    summedJobSeconds: float
    averageConcurrency: float
    peakConcurrency: int


@dataclass(frozen=True, slots=True)
class WorkloadHistory:
    """One shared run response and the bounded history collection it scopes."""

    scope: str
    current_run: dict
    samples: list["Workload"]


@dataclass(frozen=True, slots=True)
class Workload:
    """Complete selected job intervals and their measured scheduling tradeoff."""

    run_id: int
    context: WorkloadContext
    jobs: tuple[JobInterval, ...]
    excluded: tuple[str, ...]
    run_attempt: int = 1

    def record(self) -> WorkloadRecord:
        intervals = [(timestamp(job.started_at), timestamp(job.completed_at)) for job in self.jobs]
        span = max(end for _, end in intervals) - min(start for start, _ in intervals)
        total = sum(end - start for start, end in intervals)
        # Half-open intervals: a job ending at t releases its slot before a start at t.
        events = sorted(
            event for start, end in intervals if end > start for event in ((start, 1), (end, -1))
        )
        active, peak = 0, 0
        for _, change in events:
            active += change
            peak = max(peak, active)
        return {
            "schema": 1,
            "runId": self.run_id,
            "runAttempt": self.run_attempt,
            "context": asdict(self.context),
            "jobs": [asdict(job) for job in self.jobs],
            "excludedJobs": list(self.excluded),
            "successful": all(job.conclusion == "success" for job in self.jobs),
            "elapsedSpanSeconds": span,
            "summedJobSeconds": total,
            "averageConcurrency": total / span if span else 0.0,
            "peakConcurrency": peak,
        }


def selection(scope: str, snapshots: list[dict]) -> tuple[set[str], str]:
    """Build an expected inventory from the same inputs that define each CI matrix."""
    if scope == "python":
        suites = {entry["context"]["suite"] for entry in snapshots}
        if suites not in ({"tools"}, {"server", "tools"}):
            raise WorkloadError("Python workload requires complete timing snapshots")
        expected = {f"3.{minor}-{suite}" for minor in range(11, 16) for suite in suites}
        if {entry["id"] for entry in snapshots} != expected or len(snapshots) != len(expected):
            raise WorkloadError("Python workload requires every supported matrix timing snapshot")
        names = {
            f"Run Python {entry['context']['suite']} tests ({entry['context']['python']})"
            for entry in snapshots
        }
        if any(not entry["successful"] for entry in snapshots):
            raise WorkloadError("Python workload timing snapshots must be successful")
        selected = [
            {
                "id": entry["id"],
                "context": entry["context"],
                "inventory": entry["inventory"],
                "nestedInventory": entry.get("nestedHttp", {}).get("inventory"),
            }
            for entry in sorted(snapshots, key=lambda item: item["id"])
        ]
    elif scope == "compile":
        root = Path(__file__).resolve().parent
        manifest = json.loads((root / "unity-versions.json").read_bytes())
        names = {
            f"Compile {row['channel']} Unity {row['id']} (win/osx/linux)"
            for row in manifest["versions"]
        }
        selected = {
            "versions": manifest["versions"],
            "packages": json.loads((root / "unity-ci-packages.json").read_bytes()),
            "defines": (root / "compile-defines.txt").read_text(encoding="utf-8"),
        }
    else:
        raise WorkloadError("Unsupported workload scope")
    digest = hashlib.sha256(json.dumps(selected, sort_keys=True).encode()).hexdigest()
    return names, digest


def observe(jobs: list[dict], run: dict, cohort: tuple[str, list[dict]]) -> Workload:
    """Select one complete matrix, excluding all gates, report jobs and other workloads."""
    scope, snapshots = cohort
    expected, digest = selection(scope, snapshots)
    configured = os.environ.get("CI_WORKLOAD_MAX_PARALLEL") if scope == "compile" else None
    if configured is not None and not re.fullmatch(r"[1-9]\d{0,2}", configured):
        raise WorkloadError("Invalid configured workload concurrency")
    configured_concurrency = int(configured) if configured is not None else None
    if configured_concurrency is not None and configured_concurrency > MAX_JOBS:
        raise WorkloadError("Configured workload concurrency exceeds size limit")
    if not 0 < len(jobs) <= MAX_JOBS:
        raise WorkloadError("Workload inventory is empty or exceeds size limit")
    latest = {}
    for job in jobs:
        name = job["name"]
        if not isinstance(name, str) or not 0 < len(name) <= 500:
            raise WorkloadError("Invalid workload job name")
        suffix = name.rsplit(" / ", 1)[-1]
        if suffix not in expected:
            if (
                re.fullmatch(r"Run Python (server|tools) tests \(3\.\d+\)", suffix)
                and scope == "python"
                or suffix.startswith("Compile ")
                and " Unity " in suffix
                and scope == "compile"
            ):
                raise WorkloadError("Observed workload has an unexpected matrix job")
            continue
        key = (identity(job["run_attempt"]), identity(job["id"]))
        if name in latest and key == latest[name][0] and job != latest[name][1]:
            raise WorkloadError("Conflicting duplicate workload job records")
        if name not in latest or key > latest[name][0]:
            latest[name] = (key, job)
    prefixes = {name[: -len(name.rsplit(" / ", 1)[-1])] for name in latest}
    if len(prefixes) != 1 or len(latest) != len(expected):
        raise WorkloadError("Workload job inventory is missing or ambiguous")
    prefix = next(iter(prefixes))
    if set(latest) != {prefix + name for name in expected}:
        raise WorkloadError("Workload job inventory differs from the selected matrix")
    intervals, platforms = [], []
    for name, (key, job) in sorted(latest.items()):
        if job["status"] != "completed" or job["conclusion"] not in {
            "success",
            "failure",
            "cancelled",
            "timed_out",
            "action_required",
            "neutral",
        }:
            raise WorkloadError("Selected workload job is incomplete or skipped")
        start, end = timestamp(job["started_at"]), timestamp(job["completed_at"])
        if end < start:
            raise WorkloadError("Workload job completed before it started")
        labels = job["labels"]
        if (
            not isinstance(labels, list)
            or not 0 < len(labels) <= 32
            or any(not isinstance(label, str) or not 0 < len(label) <= 200 for label in labels)
        ):
            raise WorkloadError("Workload platform labels are unavailable")
        platforms.append((name, tuple(sorted(set(labels)))))
        intervals.append(
            JobInterval(
                name, key[1], key[0], job["started_at"], job["completed_at"], job["conclusion"]
            )
        )
    context = WorkloadContext(
        identity(run["workflow_id"]),
        run["event"],
        run["head_branch"],
        scope,
        tuple(sorted(latest)),
        tuple(platforms),
        digest,
        configured_concurrency,
    )
    if any(
        not isinstance(value, str) or not 0 < len(value) <= 500
        for value in (context.event, context.branch)
    ):
        raise WorkloadError("Invalid workload workflow context")
    if len({job.job_id for job in intervals}) != len(intervals):
        raise WorkloadError("Duplicate workload job ID")
    if len({job.attempt for job in intervals}) != 1:
        raise WorkloadError("Workload matrix mixes attempts; retry gaps are not comparable")
    run_attempt = identity(run["run_attempt"])
    if intervals[0].attempt > run_attempt:
        raise WorkloadError("Workload job attempt exceeds observed run attempt")
    return Workload(
        identity(run["id"]),
        context,
        tuple(intervals),
        tuple(sorted({job["name"] for job in jobs} - latest.keys())),
        run_attempt,
    )


def read_workload(value: dict) -> Workload:
    """Parse persisted observations and recompute metrics to reject malformed histories."""
    if not isinstance(value, dict) or type(value.get("schema")) is not int or value["schema"] != 1:
        raise WorkloadError("Unsupported workload observation")
    context = value["context"]
    if not isinstance(context, dict) or context["scope"] not in {"python", "compile"}:
        raise WorkloadError("Invalid workload context")
    names = context["jobs"]
    if (
        not isinstance(names, list)
        or not 0 < len(names) <= MAX_JOBS
        or any(not isinstance(name, str) or not 0 < len(name) <= 500 for name in names)
        or names != sorted(set(names))
    ):
        raise WorkloadError("Invalid workload inventory")
    if not isinstance(context["selection"], str) or not re.fullmatch(
        r"[a-f0-9]{64}", context["selection"]
    ):
        raise WorkloadError("Invalid workload selection")
    platform_rows = context["platforms"]
    if not isinstance(platform_rows, list) or len(platform_rows) != len(names):
        raise WorkloadError("Invalid workload platforms")
    platforms = []
    for row in platform_rows:
        if (
            not isinstance(row, list)
            or len(row) != 2
            or row[0] not in names
            or not isinstance(row[1], list)
            or not 0 < len(row[1]) <= 32
            or any(not isinstance(label, str) or not 0 < len(label) <= 200 for label in row[1])
            or row[1] != sorted(set(row[1]))
        ):
            raise WorkloadError("Invalid workload platforms")
        platforms.append((row[0], tuple(row[1])))
    if [name for name, _ in platforms] != names:
        raise WorkloadError("Workload platforms differ from inventory")
    rows = value["jobs"]
    if not isinstance(rows, list) or len(rows) != len(names):
        raise WorkloadError("Invalid workload intervals")
    jobs = []
    for row in rows:
        if (
            not isinstance(row, dict)
            or row["name"] not in names
            or row["conclusion"]
            not in {"success", "failure", "cancelled", "timed_out", "action_required", "neutral"}
        ):
            raise WorkloadError("Invalid workload interval")
        if timestamp(row["completed_at"]) < timestamp(row["started_at"]):
            raise WorkloadError("Workload job completed before it started")
        jobs.append(
            JobInterval(
                row["name"],
                identity(row["job_id"]),
                identity(row["attempt"]),
                row["started_at"],
                row["completed_at"],
                row["conclusion"],
            )
        )
    if [job.name for job in jobs] != names or len({job.job_id for job in jobs}) != len(jobs):
        raise WorkloadError("Workload intervals differ from inventory")
    run_attempt = identity(value["runAttempt"])
    if len({job.attempt for job in jobs}) != 1 or jobs[0].attempt > run_attempt:
        raise WorkloadError("Workload matrix attempt provenance is inconsistent")
    excluded = value["excludedJobs"]
    if (
        not isinstance(excluded, list)
        or len(excluded) > MAX_JOBS
        or any(
            not isinstance(name, str) or not 0 < len(name) <= 500 or name in names
            for name in excluded
        )
    ):
        raise WorkloadError("Invalid workload exclusions")
    for field in ("event", "branch"):
        if not isinstance(context[field], str) or not 0 < len(context[field]) <= 500:
            raise WorkloadError("Invalid workload workflow context")
    configured = context.get("configured_concurrency")
    if configured is not None and (type(configured) is not int or not 1 <= configured <= MAX_JOBS):
        raise WorkloadError("Invalid configured workload concurrency")
    result = Workload(
        identity(value["runId"]),
        WorkloadContext(
            identity(context["workflow_id"]),
            context["event"],
            context["branch"],
            context["scope"],
            tuple(names),
            tuple(platforms),
            context["selection"],
            configured,
        ),
        tuple(jobs),
        tuple(excluded),
        run_attempt,
    )
    metrics = result.record()
    for field in (
        "elapsedSpanSeconds",
        "summedJobSeconds",
        "averageConcurrency",
        "peakConcurrency",
    ):
        number = value[field]
        if type(number) not in (int, float) or number < 0 or number != metrics[field]:
            raise WorkloadError("Invalid or inconsistent workload metric")
    if (
        type(value["peakConcurrency"]) is not int
        or type(value["successful"]) is not bool
        or value["successful"] != metrics["successful"]
    ):
        raise WorkloadError("Invalid workload outcome or concurrency")
    return result


def render_workload(current: Workload, history: list[Workload]) -> tuple[str, list[str]]:
    """Warn only after two consecutive elevations against at least three earlier samples."""
    record = current.record()
    # One sample per prior run. Local history order cannot change chronological matching.
    prior = sorted(
        {
            sample.run_id: sample
            for sample in history
            if sample.run_id < current.run_id
            and sample.context == current.context
            and sample.record()["successful"]
        }.values(),
        key=lambda item: item.run_id,
        reverse=True,
    )[:MAX_SAMPLES]
    configured = current.context.configured_concurrency
    if current.context.scope == "compile" and configured is None:
        # A measured peak cannot identify an intentional max-parallel policy change.
        prior = []
    warnings = []
    lines = [
        "",
        "## Completed CI workload",
        "",
        f"Selected {current.context.scope} matrix: {len(current.jobs)} completed jobs; {len(current.excluded)} other jobs excluded. Reporting/gate jobs are outside this cohort.",
        "Workload span runs from the earliest selected job start to the latest selected completion, including gaps. It is not full workflow completion latency. Job-seconds include setup and post steps; they are not billed minutes.",
        "Concurrency is observed from half-open job intervals (ends before starts at ties), not configured max-parallel. GitHub timestamps have one-second precision.",
        "Comparisons require the same workflow, branch, event, job inventory, platform labels and workload selection. A warning requires current and previous elevations against at least three earlier matching successful samples (up to five per median).",
        "",
        "| Metric | Current | Baseline | Samples | Status |",
        "| --- | ---: | ---: | ---: | --- |",
    ]
    for field, label, threshold in (
        ("elapsedSpanSeconds", "Workload elapsed span", 10),
        ("summedJobSeconds", "Summed job-seconds", 30),
    ):
        samples = [sample.record()[field] for sample in prior]
        baseline = median(samples) if len(samples) >= MIN_SAMPLES else None
        elevated = (
            baseline is not None
            and record[field] >= baseline * 1.25
            and record[field] - baseline >= threshold
        )
        previous_base = median(samples[1:]) if len(samples) > MIN_SAMPLES else None
        repeated = (
            elevated
            and previous_base is not None
            and samples[0] >= previous_base * 1.25
            and samples[0] - previous_base >= threshold
        )
        status = (
            "Repeated increase"
            if repeated
            else "Elevated; awaiting repeat"
            if elevated
            else "Observed"
            if baseline is not None
            else "Collecting baseline"
        )
        if not record["successful"]:
            status = "Unsuccessful cohort; excluded from comparison"
        elif repeated:
            warnings.append(
                f"{current.context.scope} {label.lower()} increased at least 25% and {threshold}s in consecutive comparable observations."
            )
        base = f"{baseline:.2f}s" if baseline is not None else "-"
        lines.append(f"| {label} | {record[field]:.2f}s | {base} | {len(prior)} | {status} |")
    lines.extend(
        [
            f"| Observed average concurrency | {record['averageConcurrency']:.2f} | - | - | Observed |",
            f"| Observed peak concurrency | {record['peakConcurrency']} | - | - | Observed |",
            "",
            "| Selected job | Attempt | Duration | Result |",
            "| --- | ---: | ---: | --- |",
        ]
    )
    for job in current.jobs:
        lines.append(
            f"| {text(job.name)} | {job.attempt} | {timestamp(job.completed_at) - timestamp(job.started_at):.2f}s | {text(job.conclusion)} |"
        )
    lines.extend(
        [
            "",
            f"Provenance: GitHub Actions latest jobs API, run {current.run_id}, workflow {current.context.workflow_id}, event {text(current.context.event)}, branch {text(current.context.branch)}. Selected job IDs and platform labels are retained in timings.json.",
            "",
        ]
    )
    if current.context.scope == "compile":
        lines.insert(
            4,
            f"Configured max-parallel: {configured}."
            if configured is not None
            else "Configured max-parallel is unavailable; compile baseline comparison is disabled. Observed peak does not identify the configured scheduling policy.",
        )
    return "\n".join(lines), warnings
