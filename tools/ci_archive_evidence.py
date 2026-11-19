"""Reconcile the committed installer matrix with evidence retained in the same Actions run."""

from dataclasses import dataclass
from itertools import product
import json
import re
import subprocess

import yaml

if __package__:
    from .ci_archive_install_history import ARCHIVE_LIMIT, InstallRow, positive_int, read_archive
else:
    from ci_archive_install_history import ARCHIVE_LIMIT, InstallRow, positive_int, read_archive


CHECKPOINT = "Preserve installer checkpoint"
COMPARE = "Compare archive installation"
BOOTSTRAP = "Verify cold and offline server bootstrap"
RAW_FILES = {
    "source.json": 4096,
    "resolved.txt": 128 * 1024,
    "install.log": 1024 * 1024,
    "toolchain.txt": 64 * 1024,
    "behavior.json": 128 * 1024,
    "registration.jsonl": 128 * 1024,
}
MEMBERS = {
    "checkpoint": {"archive-checkpoint.json": 8192},
    "report": {"install-report.json": 256 * 1024},
    "raw": RAW_FILES,
    "history": {"archive-attempts.json": ARCHIVE_LIMIT},
}


@dataclass(frozen=True)
class Run:
    repository: str
    run_id: int
    attempt: int
    checked_sha: str
    source_pin: str
    head_sha: str
    repository_id: int


def committed_matrix(kind, sha):
    """Read the same workflow matrix and launcher source as the tested checkout."""
    if not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise ValueError("Expected a full tested commit")

    def read(path):
        return subprocess.check_output(["git", "show", f"{sha}:{path}"])

    filename = "python-tests" if kind == "candidate" else "distribution-canary"
    jobs = yaml.safe_load(read(f".github/workflows/{filename}.yml"))["jobs"]
    manifest = json.loads(read("MCPForUnity/package.json"))
    source = re.fullmatch(
        r"https://github\.com/ykh09242/unity-mcp/archive/([0-9a-f]{40})\.zip#subdirectory=Server",
        manifest["mcpServerSource"],
    )
    if not source:
        raise ValueError("Invalid pinned launcher archive")
    selected = {}
    workloads = {"installed_archive": "candidate", "server_bootstrap": "bootstrap"}
    for job, workload in (workloads if kind == "candidate" else {"canary": "published"}).items():
        matrix = jobs[job]["strategy"]["matrix"]
        if set(matrix) - {"os", "python-version", "include"}:
            raise ValueError("Unsupported installer matrix axes")
        if workload == "bootstrap":
            checkpoint = next(s for s in jobs[job]["steps"] if s.get("name") == CHECKPOINT)
            versions = [checkpoint["with"]["python-version"]]
        else:
            versions = matrix.get("python-version", [])
        pairs = list(product(matrix.get("os", []), versions))
        pairs.extend((r["os"], r["python-version"]) for r in matrix.get("include", []))
        if not pairs:
            raise ValueError("Empty installer matrix")
        for runner, python in pairs:
            row = InstallRow(workload, runner, python)
            job_name = f"Server Bootstrap ({runner})" if workload == "bootstrap" else row.job_name
            if (
                job_name in selected
                or not re.fullmatch(r"[\w.-]+", runner)
                or not re.fullmatch(r"[0-9]+\.[0-9]+", python)
            ):
                raise ValueError("Invalid or duplicate installer matrix row")
            selected[job_name] = row
    return selected, source[1]


def object_file(files, name):
    value = json.loads(files.get(name, b"null"))
    if not isinstance(value, dict):
        raise ValueError(f"Invalid {name}")
    return value


def require_fields(document, **expected):
    if not isinstance(document, dict) or any(
        type(document.get(k)) is not type(v) or document.get(k) != v for k, v in expected.items()
    ):
        raise ValueError("Evidence identity or outcome mismatch")


def step_outcomes(job):
    return {step["name"]: step.get("conclusion") or step["status"] for step in job.get("steps", [])}


def expected_artifacts(row, job, run):
    """A completed upload and successful installation create distinct proof obligations."""
    steps = step_outcomes(job)
    success = job["conclusion"] == "success"
    install = steps.get(BOOTSTRAP if row.kind == "bootstrap" else row.step_name)
    attempted = install not in {None, "skipped", "pending", "queued", "waiting"}
    suffix = f"{row.runner}-python-{row.python}-{run.run_id}-attempt-{job['run_attempt']}"
    yield (
        "checkpoint",
        f"archive-checkpoint-{row.kind}-{suffix}",
        success or attempted or steps.get(CHECKPOINT) in {"success", "failure"},
    )
    if row.kind != "bootstrap":
        yield (
            "report",
            f"archive-install-report-{row.kind}-{suffix}",
            success
            or steps.get(COMPARE) in {"success", "failure"}
            or (job["conclusion"] == "failure" and install in {"success", "failure"}),
        )
        raw_expected = success or (
            steps.get(row.upload_step_name) in {"success", "failure"}
            and (attempted or steps.get("Select committed launcher source") == "success")
        )
        yield (
            "raw",
            f"{row.artifact_prefix}-{run.run_id}-attempt-{job['run_attempt']}",
            raw_expected,
        )


def validate_payload(files, *, role, row, job, run):
    source = run.checked_sha if row.kind == "candidate" else run.source_pin
    identity = dict(
        repository=run.repository, run_id=run.run_id, attempt=job["run_attempt"], kind=row.kind
    )
    steps = step_outcomes(job)
    success = job["conclusion"] == "success"
    install = steps.get(row.step_name)
    if role == "checkpoint":
        require_fields(
            object_file(files, "archive-checkpoint.json"),
            **identity,
            checked_commit=run.checked_sha,
            source_commit=source,
            platform=row.runner,
            python_requested=row.python,
        )
    elif role == "report":
        report = object_file(files, "install-report.json")
        require_fields(report, **identity, runner=row.runner, python=row.python)
        current = report.get("current")
        require_fields(current, outcome=install)
        if success or install == "success" or current.get("source_commit") is not None:
            require_fields(current, source_commit=source)
    elif role == "raw":
        behavior = next((v for k, v in steps.items() if k.startswith("Exercise installed ")), None)
        mandatory = set(RAW_FILES) if success or behavior == "success" else set()
        if install == "success":
            mandatory.update(("source.json", "install.log", "resolved.txt", "toolchain.txt"))
        if row.kind == "published" and steps.get("Select committed launcher source") == "success":
            mandatory.add("source.json")
        if any(not files.get(name, b"").strip() for name in mandatory) or not files:
            raise ValueError("Incomplete raw evidence")
        if "source.json" in files:
            info = object_file(files, "source.json")
            if row.kind == "candidate":
                require_fields(info, candidate_sha=source, python_version=row.python)
            else:
                require_fields(
                    info,
                    checked_commit=run.checked_sha,
                    server_commit=source,
                    server_source=f"https://github.com/ykh09242/unity-mcp/archive/{source}.zip#subdirectory=Server",
                )
        if "behavior.json" in mandatory:
            probe = object_file(files, "behavior.json")
            require_fields(probe, candidate_sha=source)
            if not str(probe.get("python", "")).startswith(row.python + "."):
                raise ValueError("Incorrect raw probe interpreter")


class Artifacts:
    def __init__(self, run, records, fetch):
        self.run = run
        self.records = records
        self.fetch = fetch

    def inspect(self, name, role, expected, validate):
        result = {"name": name, "expected": expected}
        if self.records is None:
            return {**result, "status": "inventory_unknown"}
        matches = [item for item in self.records if item["name"] == name]
        if not matches:
            return {**result, "status": "missing" if expected else "not_expected"}
        if len(matches) != 1:
            return {**result, "status": "ambiguous"}
        artifact = matches[0]
        try:
            run = self.run
            require_fields(
                artifact.get("workflow_run"),
                id=run.run_id,
                head_sha=run.head_sha,
                repository_id=run.repository_id,
            )
            artifact_id = positive_int(artifact.get("id"))
            if not artifact_id or type(artifact.get("expired")) is not bool:
                raise ValueError("Invalid artifact identity")
            result["id"] = artifact_id
            if artifact["expired"]:
                return {**result, "status": "expired"}
            size = positive_int(artifact.get("size_in_bytes"))
            if not size or size > ARCHIVE_LIMIT:
                raise ValueError("Invalid artifact size")
            try:
                data = self.fetch(f"repos/{run.repository}/actions/artifacts/{artifact_id}/zip")
            except (ValueError, OSError, subprocess.SubprocessError):
                return {**result, "status": "download_unavailable"}
            validate(read_archive(data, MEMBERS[role]))
        except (ValueError, TypeError, KeyError, RecursionError) as error:
            return {**result, "status": "invalid", "reason": str(error)[:120]}
        return {**result, "status": "verified"}
