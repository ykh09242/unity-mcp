"""Read reference graph phase receipts from exact GitHub Actions attempts."""

import argparse
import json
import re
import sys
from collections.abc import Callable
from pathlib import Path
from typing import TypedDict

if __package__:
    from tools import ci_archive_attempts, ci_archive_evidence
    from tools.ci_test_performance import github_api
else:
    import ci_archive_attempts
    import ci_archive_evidence
    from ci_test_performance import github_api


JOB = re.compile(r"Reference graph \(([\w.-]+), Python ([0-9]+\.[0-9]+)\)")
RECEIPT = "reference-graph-checkout"
PHASES = {
    "Checkout reference graph candidate": "checkout",
    "Record checked candidate": "checked_revision",
    "Preserve reference graph checkout receipt": "checkout_receipt",
    "Set up reference graph toolchain": "toolchain",
    "Install locked reference graph dependencies": "install",
    "Collect reference graph tests": "collection",
    "Execute reference graph core tests": "execution_core",
    "Execute reference graph service tests": "execution_service",
    "Verify Windows event-loop shutdown and bounded launch output": "windows_launch",
    "Verify interrupted private-store initialization": "initialization",
    "Install and exercise isolated reference graph": "legacy_combined",
}


class Phase(TypedDict):
    phase: str
    status: str
    conclusion: str | None
    started_at: str | None
    completed_at: str | None


class Attempt(TypedDict):
    job: str
    job_id: int
    attempt: int
    status: str
    conclusion: str | None
    last_confirmed_phase: str | None
    phases: list[Phase]


def summarize(job: dict, name: str) -> Attempt:
    phases: list[Phase] = []
    last_confirmed = None
    seen: set[str] = set()
    for step in sorted(job.get("steps") or [], key=lambda item: item["number"]):
        label = step["name"]
        phase = PHASES.get(label)
        if label.startswith("Run actions/checkout@"):
            phase = "checkout"
        if label.startswith("Run astral-sh/setup-uv@"):
            phase = "toolchain"
        if phase is None:
            continue
        if phase in seen:
            raise ValueError("Duplicate reference graph phase")
        seen.add(phase)
        row = Phase(
            phase=phase,
            status=step["status"],
            conclusion=step.get("conclusion"),
            started_at=step.get("started_at"),
            completed_at=step.get("completed_at"),
        )
        # Artifact upload is advisory. Its durable proof is the artifact itself.
        if (
            phase != "checkout_receipt"
            and row["status"] == "completed"
            and row["conclusion"] == "success"
            and row["started_at"]
            and row["completed_at"]
        ):
            last_confirmed = phase
        phases.append(row)
    return Attempt(
        job=name,
        job_id=job["id"],
        attempt=job["run_attempt"],
        status=job["status"],
        conclusion=job.get("conclusion"),
        last_confirmed_phase=last_confirmed,
        phases=phases,
    )


def attempt_rows(
    run: ci_archive_evidence.Run, attempt: int, fetch: Callable[[str], bytes]
) -> list[tuple[dict, Attempt]]:
    endpoint = f"repos/{run.repository}/actions/runs/{run.run_id}/attempts/{attempt}/jobs"
    rows: list[tuple[dict, Attempt]] = []
    seen: set[str] = set()
    for job in ci_archive_attempts.attempt_job_inventory(fetch, endpoint):
        name = job["name"].rsplit(" / ", 1)[-1]
        if JOB.fullmatch(name) is None:
            continue
        recorded_attempt = ci_archive_evidence.positive_int(job.get("run_attempt"))
        if recorded_attempt is not None and recorded_attempt < attempt:
            continue  # A partial retry can contain earlier carried-forward jobs.
        ci_archive_evidence.require_fields(
            job, run_id=run.run_id, run_attempt=attempt, head_sha=run.head_sha
        )
        if name in seen:
            raise ValueError("Duplicate reference graph matrix row")
        seen.add(name)
        rows.append((job, summarize(job, name)))
    return rows


def checkout_receipt(store: ci_archive_evidence.Artifacts, row: Attempt) -> dict:
    match = JOB.fullmatch(row["job"])
    if match is None:
        raise ValueError("Invalid reference graph matrix row")
    runner, python = match.groups()
    run = store.run
    attempt = row["attempt"]
    name = f"{RECEIPT}-{runner}-python-{python}-{run.run_id}-attempt-{attempt}"
    expected = any(
        phase["phase"] == "checkout_receipt" and phase["conclusion"] == "success"
        for phase in row["phases"]
    )

    def validate(files: dict) -> None:
        ci_archive_evidence.require_fields(
            ci_archive_evidence.object_file(files, f"{RECEIPT}.json"),
            schema_version=1,
            repository=run.repository,
            run_id=run.run_id,
            run_attempt=attempt,
            job_key="reference_graph",
            checkout_sha=run.checked_sha,
            matrix_os=runner,
            matrix_python=python,
        )

    return store.inspect(name, RECEIPT, expected, validate)


def inspect(run: ci_archive_evidence.Run, fetch: Callable[[str], bytes] = github_api) -> dict:
    store = ci_archive_attempts.artifacts(run, fetch)
    attempts = []
    carried_forward = []
    unavailable = []
    effective = {}
    actual_jobs = {}
    for attempt in range(1, run.attempt + 1):
        try:
            rows = attempt_rows(run, attempt, fetch)
        except ci_archive_attempts.API_ERRORS:
            unavailable.append(attempt)
            continue
        for job, row in sorted(rows, key=lambda item: item[1]["job"]):
            result = {**row, "checkout_receipt": checkout_receipt(store, row)}
            name = row["job"]
            previous = effective.get(name)
            if (
                previous
                and result["checkout_receipt"]["status"] == "missing"
                and previous["checkout_receipt"]["status"] == "verified"
                and ci_archive_attempts.same_successful_execution(job, actual_jobs[name])
            ):
                carried_forward.append(
                    {
                        "job": name,
                        "attempt": attempt,
                        "job_id": job["id"],
                        "execution_attempt": previous["attempt"],
                        "execution_job_id": previous["job_id"],
                    }
                )
                continue
            attempts.append(result)
            effective[name] = result
            actual_jobs[name] = job

    return {
        "repository": run.repository,
        "run_id": run.run_id,
        "through_attempt": run.attempt,
        "run_head_sha": run.head_sha,
        "expected_checkout_sha": run.checked_sha,
        "unavailable_attempts": unavailable,
        "carried_forward": carried_forward,
        "jobs": attempts,
        "effective": {
            name: {
                "attempt": row["attempt"],
                "job_id": row["job_id"],
                "conclusion": row["conclusion"],
                "last_confirmed_phase": row["last_confirmed_phase"],
                "checkout_receipt_status": row["checkout_receipt"]["status"],
                "status": (
                    "inventory_unknown"
                    if any(number >= row["attempt"] for number in unavailable)
                    else row["status"]
                ),
            }
            for name, row in effective.items()
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--run-id", type=int, required=True)
    parser.add_argument("--through-attempt", type=int, required=True)
    parser.add_argument("--checked-sha", required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if not re.fullmatch(r"[0-9a-f]{40}", args.checked_sha):
        parser.error("--checked-sha requires the full expected checkout SHA")
    try:
        run = ci_archive_attempts.run_identity(
            args.repository, args.run_id, args.through_attempt, args.checked_sha, "", github_api
        )
        report = json.dumps(inspect(run), indent=2, sort_keys=True) + "\n"
        if args.output is None:
            print(report, end="")
        else:
            args.output.write_text(report, encoding="utf-8")
    except ci_archive_attempts.API_ERRORS as error:
        print(f"Reference graph attempt evidence unavailable: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
