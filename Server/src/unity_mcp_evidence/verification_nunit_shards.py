"""Reconcile retained NUnit shards against the case inventory planned for one run."""

from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from .play_scenario_analysis_io import read_json, safe_local_path
from .play_scenario_report_json import JsonValue
from .verification_nunit_xml import DECISIVE_RESULTS, NUnitRun, read_test_run

_PLAN_SCHEMA = "verification_nunit_shards@1"
_MAX_SPLITS = 128
_MAX_SUBMISSIONS = 256
_MAX_CASES = 65_536
_MAX_INPUT_BYTES = 128 * 1024 * 1024


@dataclass(slots=True)
class Submission:
    attempt: int
    execution_id: str
    paths: set[Path] = field(default_factory=set)


@dataclass(slots=True)
class Split:
    split_id: str
    cases: set[str]
    submissions: dict[int, Submission] = field(default_factory=dict)


def _required_name(value: JsonValue, field_name: str, limit: int) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError(f"Expected a nonempty bounded {field_name}")
    return value


def _archive_path(directory: Path, value: JsonValue) -> Path:
    relative = _required_name(value, "relative NUnit path", 4096)
    parts = relative.split("/")
    if "\\" in relative or any(
        not part or part in {".", ".."} or ":" in part or part != part.rstrip(" .")
        for part in parts
    ):
        raise ValueError("NUnit paths must stay inside the selected manifest directory")
    return directory.joinpath(*parts)


def _read_plan(path: Path) -> tuple[str, dict[str, Split]]:
    manifest = read_json(path)
    if manifest.get("schema") != _PLAN_SCHEMA:
        raise ValueError(f"Expected a {_PLAN_SCHEMA} manifest")
    run_id = _required_name(manifest.get("run_id"), "run ID", 256)
    plan = manifest.get("splits")
    if not isinstance(plan, list) or not 1 <= len(plan) <= _MAX_SPLITS:
        raise ValueError(f"Expected one to {_MAX_SPLITS} declared NUnit splits")

    splits: dict[str, Split] = {}
    planned_cases: set[str] = set()
    for row in plan:
        if not isinstance(row, dict):
            raise ValueError("Expected a declared NUnit split")
        split_id = _required_name(row.get("split_id"), "split ID", 256)
        raw_cases = row.get("cases")
        if not isinstance(raw_cases, list) or not raw_cases:
            raise ValueError("A planned split needs its NUnit case inventory")
        if len(planned_cases) + len(raw_cases) > _MAX_CASES:
            raise ValueError(f"At most {_MAX_CASES} NUnit cases can be planned")
        cases = {_required_name(name, "NUnit full name", 4096) for name in raw_cases}
        if split_id in splits or len(raw_cases) != len(cases) or planned_cases & cases:
            raise ValueError("Declare each split and each NUnit full name exactly once")
        planned_cases.update(cases)
        splits[split_id] = Split(split_id, cases)

    submissions = manifest.get("submissions")
    if not isinstance(submissions, list) or len(submissions) > _MAX_SUBMISSIONS:
        raise ValueError(f"Expected at most {_MAX_SUBMISSIONS} NUnit submissions")
    executions: dict[str, tuple[str, int]] = {}
    for row in submissions:
        if not isinstance(row, dict) or row.get("run_id") != run_id:
            raise ValueError("Each NUnit submission must belong to the declared run ID")
        split_id = _required_name(row.get("split_id"), "split ID", 256)
        if split_id not in splits:
            raise ValueError("NUnit submission belongs to an undeclared split")
        execution_id = _required_name(row.get("execution_id"), "execution ID", 256)
        attempt = row.get("attempt")
        if not isinstance(attempt, int) or isinstance(attempt, bool) or not 1 <= attempt <= 65535:
            raise ValueError("Expected a positive integer attempt number (at most 65535)")
        slot = split_id, attempt
        if executions.setdefault(execution_id, slot) != slot:
            raise ValueError("An NUnit execution ID was reused across split attempts")
        split = splits[split_id]
        submission = split.submissions.setdefault(attempt, Submission(attempt, execution_id))
        if submission.execution_id != execution_id:
            raise ValueError("Conflicting execution IDs for one NUnit split attempt")
        submission.paths.add(_archive_path(path.parent, row.get("xml")))
    return run_id, splits


@dataclass(slots=True)
class Archive:
    remaining_bytes: int = _MAX_INPUT_BYTES
    artifact_executions: dict[str, str] = field(default_factory=dict)

    def read(self, submission: Submission) -> NUnitRun | None:
        run = None
        for path in sorted(submission.paths):
            try:
                alternate = read_test_run(path, maximum_bytes=self.remaining_bytes)
            except FileNotFoundError:
                continue
            self.remaining_bytes -= alternate.size
            if run is not None and run.digest != alternate.digest:
                raise ValueError("Conflicting NUnit artifacts for one split attempt")
            owner = self.artifact_executions.setdefault(alternate.digest, submission.execution_id)
            if owner != submission.execution_id:
                raise ValueError("NUnit artifact was reused for different executions")
            run = alternate
        return run


def _observe_split(split: Split, archive: Archive) -> dict:
    cases = {name: [] for name in sorted(split.cases)}
    assemblies = {}
    attempts = []
    for submission in sorted(split.submissions.values(), key=lambda row: row.attempt):
        run = archive.read(submission)
        attempt = {"attempt": submission.attempt, "execution_id": submission.execution_id}
        attempts.append(
            {
                **attempt,
                "artifact_sha256": run.digest if run is not None else None,
                "artifact_state": "read" if run is not None else "missing",
                "duration_seconds": run.seconds if run is not None else None,
                "reported_cases": len(run.cases) if run is not None else 0,
            }
        )
        if run is None:
            continue
        if run.cases.keys() - split.cases:
            raise ValueError("NUnit submission contains a case outside its declared split")
        for name, case in run.cases.items():
            if case.assembly and assemblies.setdefault(name, case.assembly) != case.assembly:
                raise ValueError("NUnit assembly for one case changed across attempts")
            cases[name].append(
                {**attempt, "result": case.result, "duration_seconds": case.duration}
            )

    final = {
        name: {
            "result": history[-1]["result"] if history else "missing",
            "assembly": assemblies.get(name),
            "attempts": history,
        }
        for name, history in cases.items()
    }
    counts = Counter(row["result"] for row in final.values())
    coverage = {
        "planned_cases": len(cases),
        "reported_cases": len(cases) - counts["missing"],
        "executed_cases": sum(counts[result] for result in DECISIVE_RESULTS),
        "passed_cases": counts["Passed"],
        "failed_cases": counts["Failed"],
        "skipped_cases": counts["Skipped"],
        "inconclusive_cases": counts["Inconclusive"],
        "missing_cases": counts["missing"],
    }
    if not coverage["reported_cases"]:
        state = "missing"
    elif coverage["executed_cases"] != len(cases) or any(
        row["artifact_state"] == "missing" for row in attempts
    ):
        state = "incomplete"
    else:
        state = "failed" if counts["Failed"] else "passed"
    return {
        "split_id": split.split_id,
        "state": state,
        "coverage": coverage,
        "submissions": attempts,
        "cases": final,
    }


def merge_shards(manifest: Path) -> dict:
    """Read the declared split attempts and use the latest observation per planned case."""
    run_id, splits = _read_plan(safe_local_path(manifest))
    archive = Archive()
    reports = [_observe_split(splits[split_id], archive) for split_id in sorted(splits)]
    totals: Counter[str] = Counter()
    for report in reports:
        totals.update(report["coverage"])
    missing = [row["split_id"] for row in reports if row["state"] == "missing"]
    incomplete = [row["split_id"] for row in reports if row["state"] == "incomplete"]
    totals.update(
        planned_splits=len(reports),
        reported_splits=len(reports) - len(missing),
        completed_splits=len(reports) - len(missing) - len(incomplete),
    )
    state = (
        "incomplete" if missing or incomplete else "failed" if totals["failed_cases"] else "passed"
    )
    return {
        "schema": "verification_nunit_shard_result@1",
        "state": state,
        "run_id": run_id,
        "coverage": dict(totals),
        "missing_splits": missing,
        "incomplete_splits": incomplete,
        "splits": reports,
    }
