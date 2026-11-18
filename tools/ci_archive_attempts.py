"""Summarize installer attempts from hosted job metadata, even when a runner lost its log."""

import argparse
import json
import os
from pathlib import Path
import re
import subprocess

if __package__:
    from tools.ci_test_performance import github_api
else:
    from ci_test_performance import github_api


JOBS = {
    "candidate": re.compile(
        r"(?:Installed candidate archive \([\w.-]+, Python [0-9.]+\)|Server Bootstrap \([\w.-]+\))"
    ),
    "published": re.compile(r"Pinned launcher archive \([\w.-]+, Python [0-9.]+\)"),
}
PHASES = {
    "Set up job",
    "Checkout source metadata",
    "Verify beta push commit",
    "Record checked candidate",
    "Select committed launcher source",
    "Install uv",
    "Preserve installer checkpoint",
    "Verify cold and offline server bootstrap",
    "Install candidate archive without runtime lock",
    "Resolve and install the pinned archive without a lock or shared cache",
    "Exercise installed registration, MCP, HTTP and CLI behavior",
    "Exercise installed MCP, HTTP and CLI behavior",
    "Retain source, resolution and probe evidence",
    "Retain candidate source, resolution and probe evidence",
    "Compare archive installation",
}


def phase_name(name):
    if name in PHASES:
        return name
    if name.startswith("Run astral-sh/setup-uv@"):
        return "Install uv"
    if name.startswith("Run actions/checkout@"):
        return "Checkout source metadata"
    return None


def summarize(job, kind, attempt):
    name = job.get("name", "").rsplit(" / ", 1)[-1]
    if job.get("run_attempt") != attempt or not JOBS[kind].fullmatch(name):
        return None
    row = {
        "job": name,
        "job_id": int(job["id"]),
        "attempt": attempt,
        "conclusion": job.get("conclusion"),
        "last_started": None,
        "last_succeeded": None,
        "first_unsuccessful": None,
    }
    for step in job.get("steps", []):
        phase = phase_name(step["name"])
        status, outcome = step.get("status"), step.get("conclusion")
        if (
            not phase
            or not step.get("started_at")
            or status in {"queued", "pending", "waiting"}
            or outcome == "skipped"
        ):
            continue
        row["last_started"] = phase
        if status == "completed" and outcome == "success":
            row["last_succeeded"] = phase
        elif row["first_unsuccessful"] is None and (
            outcome in {"failure", "cancelled", "timed_out"}
            or row["conclusion"] in {"cancelled", "timed_out"}
            and status == "in_progress"
        ):
            row["first_unsuccessful"] = phase
    return row


def job_page(fetch, prefix, page):
    # GitHub sometimes rejects large job responses that include every workflow step.
    try:
        return json.loads(fetch(f"{prefix}?per_page=10&page={page}"))
    except (ValueError, OSError, subprocess.SubprocessError):
        first = json.loads(fetch(f"{prefix}?per_page=5&page={page * 2 - 1}"))
        if len(first["jobs"]) == 5 and first["total_count"] > (page * 2 - 1) * 5:
            second = json.loads(fetch(f"{prefix}?per_page=5&page={page * 2}"))
            first["jobs"].extend(second["jobs"])
        return first


def collect(repository, run_id, attempt, kind, fetch=github_api):
    if not re.fullmatch(r"[\w.-]+/[\w.-]+", repository) or min(run_id, attempt) < 1:
        raise ValueError("Invalid workflow identity")
    rows, warnings = [], []
    for number in range(1, attempt + 1):
        prefix = f"repos/{repository}/actions/runs/{run_id}/attempts/{number}/jobs"
        try:
            for page in range(1, 51):
                inventory = job_page(fetch, prefix, page)
                for job in inventory["jobs"]:
                    if row := summarize(job, kind, number):
                        rows.append(row)
                if page * 10 >= inventory["total_count"]:
                    break
            else:
                warnings.append(f"Attempt {number}: job inventory exceeded 500 entries.")
        except (ValueError, KeyError, OSError, subprocess.SubprocessError):
            warnings.append(f"Attempt {number}: job metadata unavailable or incomplete.")
    return rows, warnings


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kind", choices=JOBS, required=True)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--run-id", type=int, required=True)
    parser.add_argument("--attempt", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    rows, warnings = collect(args.repository, args.run_id, args.attempt, args.kind)
    report = {
        "repository": args.repository,
        "run_id": args.run_id,
        "through_attempt": args.attempt,
        "kind": args.kind,
        "jobs": rows,
        "warnings": warnings,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    heading = "## Archive installation attempts"
    lines = [
        heading,
        "",
        "| Attempt | Job | Result | Last phase started | Last phase passed | First unsuccessful phase |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for row in rows:
        url = f"{os.environ.get('GITHUB_SERVER_URL', 'https://github.com')}/{args.repository}/actions/runs/{args.run_id}/job/{row['job_id']}"
        lines.append(
            f"| {row['attempt']} | [{row['job']}]({url}) | {row['conclusion'] or 'pending'} | "
            f"{row['last_started'] or 'unknown'} | {row['last_succeeded'] or 'unknown'} | {row['first_unsuccessful'] or '—'} |"
        )
    lines.extend(["", *warnings])
    if not rows:
        lines.append("No matching installer job was observed.")
    summary = "\n".join(lines) + "\n"
    if destination := os.environ.get("GITHUB_STEP_SUMMARY"):
        with Path(destination).open("a", encoding="utf-8") as stream:
            stream.write(summary)
    else:
        print(summary, end="")


if __name__ == "__main__":
    main()
