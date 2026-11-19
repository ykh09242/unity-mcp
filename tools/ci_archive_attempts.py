"""Summarize installer attempts from hosted job metadata, even when a runner lost its log."""

import argparse
from functools import partial
import json
import os
from pathlib import Path
import re
import subprocess

if __package__:
    from tools import ci_archive_evidence as evidence
    from tools.ci_test_performance import github_api
else:
    import ci_archive_evidence as evidence
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
            if first["total_count"] != second["total_count"]:
                raise ValueError("Inconsistent split job inventory")
            first["jobs"].extend(second["jobs"])
        return first


def inventory(fetch, prefix, field, size):
    result, seen, total = [], set(), None
    for page in range(1, 500 // size + 1):
        data = (
            job_page(fetch, prefix, page)
            if field == "jobs"
            else json.loads(fetch(f"{prefix}?per_page={size}&page={page}"))
        )
        count, batch = data["total_count"], data[field]
        if (
            type(count) is not int
            or not 0 <= count <= 500
            or total not in (None, count)
            or not isinstance(batch, list)
            or len(batch) != min(size, count - len(result))
        ):
            raise ValueError("Incomplete Actions inventory")
        total = count
        for item in batch:
            if (
                not isinstance(item, dict)
                or not evidence.positive_int(item.get("id"))
                or item["id"] in seen
                or not isinstance(item.get("name"), str)
            ):
                raise ValueError("Invalid Actions inventory identity")
            seen.add(item["id"])
            result.append(item)
        if len(result) == total:
            return result
    raise ValueError("Actions inventory exceeds limit")


API_ERRORS = (ValueError, KeyError, TypeError, OSError, subprocess.SubprocessError)


def collect(repository, run_id, attempt, kind, fetch, head_sha):
    jobs, history, unavailable = [], {}, {}
    for number in range(1, attempt + 1):
        prefix = f"repos/{repository}/actions/runs/{run_id}/attempts/{number}/jobs"
        try:
            for job in inventory(fetch, prefix, "jobs", 10):
                if job.get("run_attempt") != number:
                    # Some attempt pages also show jobs carried forward from an earlier attempt.
                    if type(job.get("run_attempt")) is int and 0 < job["run_attempt"] < number:
                        continue
                    raise ValueError("Invalid attempt identity")
                evidence.require_fields(job, run_id=run_id, head_sha=head_sha)
                name = job["name"].rsplit(" / ", 1)[-1]
                if JOBS[kind].fullmatch(name):
                    jobs.append(job)
                elif name == f"Installer attempts ({kind})":
                    if number in history:
                        raise ValueError("Ambiguous history job")
                    history[number] = job
        except API_ERRORS:
            unavailable[number] = f"Attempt {number}: job metadata unavailable or incomplete."
    return jobs, history, unavailable


def artifacts(run, fetch):
    try:
        records = inventory(
            fetch, f"repos/{run.repository}/actions/runs/{run.run_id}/artifacts", "artifacts", 100
        )
    except API_ERRORS:
        records = None
    return evidence.Artifacts(run, records, fetch)


def history_artifact(store, kind, number, jobs, exact=None):
    run = store.run
    name = f"archive-history-{kind}-{run.run_id}-attempt-{number}"

    def validate(files):
        if exact is not None:
            if files.get("archive-attempts.json") != exact:
                raise ValueError("Retained history differs from generated report")
            return
        data = evidence.object_file(files, "archive-attempts.json")
        evidence.require_fields(
            data,
            kind=kind,
            repository=run.repository,
            run_id=run.run_id,
            through_attempt=number,
            warnings=[],
        )
        if "checked_sha" in data:
            evidence.require_fields(data, checked_sha=run.checked_sha)
        expected = [row for row in jobs if row["attempt"] <= number]
        if sorted(data["jobs"], key=lambda r: r["job_id"]) != sorted(
            expected, key=lambda r: r["job_id"]
        ):
            raise ValueError("Retained installer attempts differ from Actions jobs")

    return store.inspect(name, "history", True, validate)


def verify_upload(run, kind, artifact_id, exact, fetch=github_api):
    try:
        item = json.loads(fetch(f"repos/{run.repository}/actions/artifacts/{artifact_id}"))
        evidence.require_fields(item, id=artifact_id)
        records = [item]
    except API_ERRORS:
        records = None
    store = evidence.Artifacts(run, records, fetch)
    return history_artifact(store, kind, run.attempt, [], exact)


def run_identity(repository, run_id, attempt, sha, pin, fetch):
    if not re.fullmatch(r"[\w.-]+/[\w.-]+", repository) or min(run_id, attempt) < 1:
        raise ValueError("Invalid workflow identity")
    meta = json.loads(fetch(f"repos/{repository}/actions/runs/{run_id}/attempts/{attempt}"))
    evidence.require_fields(meta, id=run_id, run_attempt=attempt)
    evidence.require_fields(meta["repository"], full_name=repository)
    repo_id = evidence.positive_int(meta["repository"].get("id"))
    if not repo_id or not re.fullmatch(r"[0-9a-f]{40}", meta["head_sha"]):
        raise ValueError("Invalid Actions run metadata")
    return evidence.Run(repository, run_id, attempt, sha, pin, meta["head_sha"], repo_id)


def reconcile(run, kind, selected, fetch=github_api, include_history=False):
    jobs, history_jobs, unavailable = collect(
        run.repository, run.run_id, run.attempt, kind, fetch, run.head_sha
    )
    warnings = list(unavailable.values())
    rows = [summarize(j, kind, j["run_attempt"]) for j in jobs]
    store = artifacts(run, fetch)
    checks, latest, histories = [], {}, []
    issues = list(warnings)
    if store.records is None:
        issues.append("Artifact inventory unavailable or incomplete.")
    for job, summary in zip(jobs, rows):
        name, number = summary["job"], job["run_attempt"]
        row = selected.get(name)
        if row is None or (name, number) in latest:
            issues.append(f"Unexpected or duplicate installer job: {name}, attempt {number}.")
            continue
        proof = {}
        for role, artifact, expected in evidence.expected_artifacts(row, job, run):
            proof[role] = store.inspect(
                artifact,
                role,
                expected,
                partial(evidence.validate_payload, role=role, row=row, job=job, run=run),
            )
        latest[name, number] = job
        checks.append({"job": name, "attempt": number, "job_id": job["id"], "artifacts": proof})

    effective = {}
    for name in selected:
        attempts = [n for job, n in latest if job == name]
        if not attempts:
            effective[name] = {"status": "inventory_unknown" if warnings else "missing_job"}
            issues.append(f"{name}: {effective[name]['status']}.")
            continue
        number = max(attempts)
        job = latest[name, number]
        effective[name] = {"attempt": number, "job_id": job["id"], "status": job.get("conclusion")}
        if any(n >= number for n in unavailable):
            effective[name]["status"] = "inventory_unknown"
            continue
        if job.get("conclusion") != "success":
            issues.append(f"{name}, attempt {number}: {job.get('conclusion') or 'pending'}.")
        else:
            row = selected[name]
            install = evidence.BOOTSTRAP if row.kind == "bootstrap" else row.step_name
            if evidence.step_outcomes(job).get(install) != "success":
                issues.append(f"{name}, attempt {number}: successful install step unverified.")
    for check in checks:
        for proof in check["artifacts"].values():
            if (
                proof["status"] not in {"verified", "not_expected"}
                and effective[check["job"]]["attempt"] == check["attempt"]
            ):
                issues.append(f"{proof['name']}: {proof['status']}.")
    for number in range(1, run.attempt + 1):
        job = history_jobs.get(number, {})
        uploaded = any(
            s.get("name", "").startswith(
                ("Run actions/upload-artifact@", "Retain installer attempt history")
            )
            and s.get("conclusion") == "success"
            for s in job.get("steps", [])
        )
        if number == run.attempt:
            required = include_history
        else:
            required = uploaded or job.get("conclusion") == "success"
        if required:
            proof = history_artifact(store, kind, number, rows)
            histories.append(proof)
            if proof["status"] != "verified":
                issues.append(f"{proof['name']}: {proof['status']}.")
    return {
        "repository": run.repository,
        "run_id": run.run_id,
        "through_attempt": run.attempt,
        "checked_sha": run.checked_sha,
        "run_head_sha": run.head_sha,
        "source_pin": run.source_pin,
        "kind": kind,
        "jobs": rows,
        "warnings": warnings,
        "effective": effective,
        "evidence": checks,
        "history": histories,
        "issues": issues,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kind", choices=JOBS, required=True)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--run-id", type=int, required=True)
    parser.add_argument("--attempt", type=int, required=True)
    parser.add_argument("--checked-sha", required=True)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--include-history", action="store_true", help="Audit an already retained attempt"
    )
    mode.add_argument(
        "--verify-upload",
        type=int,
        metavar="ARTIFACT_ID",
        help="Verify the uploaded report against --output",
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    selected, pin = evidence.committed_matrix(args.kind, args.checked_sha)
    run = run_identity(
        args.repository, args.run_id, args.attempt, args.checked_sha, pin, github_api
    )
    if args.verify_upload is not None:
        if args.verify_upload <= 0:
            parser.error("Invalid uploaded artifact ID")
        proof = verify_upload(run, args.kind, args.verify_upload, args.output.read_bytes())
        print(f"{proof['name']}: {proof['status']}")
        return int(proof["status"] != "verified")
    report = reconcile(run, args.kind, selected, include_history=args.include_history)
    rows = report["jobs"]
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
    lines.extend(["", *report["issues"]])
    if not report["issues"]:
        lines.append(
            f"Verified retained installer evidence for {len(report['effective'])} selected matrix rows."
        )
    if not rows:
        lines.append("No matching installer job was observed.")
    summary = "\n".join(lines) + "\n"
    if destination := os.environ.get("GITHUB_STEP_SUMMARY"):
        with Path(destination).open("a", encoding="utf-8") as stream:
            stream.write(summary)
    else:
        print(summary, end="")
    return int(bool(report["issues"]))


if __name__ == "__main__":
    raise SystemExit(main())
