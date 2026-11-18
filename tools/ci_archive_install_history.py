"""Read bounded Actions evidence for a specific archive installation matrix row."""

from dataclasses import dataclass
from datetime import datetime
import io
import json
import os
import stat
import subprocess
from tempfile import TemporaryFile
from time import monotonic
from urllib.parse import urlencode
import zipfile
import zlib


MEMBER_LIMITS = {"resolved.txt": 128 * 1024, "install.log": 1024 * 1024, "source.json": 4096}
ARCHIVE_LIMIT = 2 * 1024 * 1024
MAX_RUNS = 12
MAX_ATTEMPTS = 3
MAX_JOBS = 200
HISTORY_SECONDS = 30
OUTCOMES = ("success", "failure", "cancelled", "skipped", "timed_out")


class HistoryUnavailable(ValueError):
    """The Actions API or archive did not provide usable bounded evidence."""


def read_archive(content: bytes) -> dict[str, bytes]:
    """Read only named evidence from memory, without extracting any archive paths."""
    if len(content) > ARCHIVE_LIMIT:
        raise HistoryUnavailable("Artifact exceeds size limit")
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            entries = archive.infolist()
            if len(entries) > 128:
                raise HistoryUnavailable("Artifact has too many entries")
            files = {}
            for entry in entries:
                limit = MEMBER_LIMITS.get(entry.filename)
                if limit is None:
                    continue
                if (
                    entry.filename in files
                    or entry.filename != entry.orig_filename
                    or entry.file_size > limit
                    or entry.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED)
                    or stat.S_ISLNK(entry.external_attr >> 16)
                ):
                    raise HistoryUnavailable("Invalid artifact member")
                with archive.open(entry) as stream:
                    files[entry.filename] = stream.read(limit + 1)
                if len(files[entry.filename]) != entry.file_size:
                    raise HistoryUnavailable("Invalid artifact member length")
            return files
    except (OSError, EOFError, RuntimeError, zipfile.BadZipFile, zlib.error):
        raise HistoryUnavailable("Unable to read artifact archive") from None


@dataclass(frozen=True, slots=True)
class InstallRow:
    """Stable workflow labels for one source type, runner label and Python minor."""

    kind: str
    runner: str
    python: str

    @property
    def artifact_prefix(self) -> str:
        prefix = "candidate-archive" if self.kind == "candidate" else "distribution-canary"
        return f"{prefix}-{self.runner}-python-{self.python}"

    @property
    def job_name(self) -> str:
        label = (
            "Installed candidate archive" if self.kind == "candidate" else "Pinned launcher archive"
        )
        return f"{label} ({self.runner}, Python {self.python})"

    @property
    def step_name(self) -> str:
        if self.kind == "candidate":
            return "Install candidate archive without runtime lock"
        return "Resolve and install the pinned archive without a lock or shared cache"

    @property
    def upload_step_name(self) -> str:
        if self.kind == "candidate":
            return "Retain candidate source, resolution and probe evidence"
        return "Retain source, resolution and probe evidence"


@dataclass(frozen=True, slots=True)
class Artifact:
    """An exact Actions attempt and the independently observed installation result."""

    run_id: int
    attempt: int
    job_id: int
    artifact_id: int
    artifact_name: str
    outcome: str
    files: dict[str, bytes]


class ActionsHistory:
    """Resolve install history within one repository and top-level Actions workflow."""

    def __init__(self, repository: str, run_id: int, attempt: int, row: InstallRow):
        self.repository = repository
        self.run_id = run_id
        self.attempt = attempt
        self.row = row
        self.deadline = monotonic() + HISTORY_SECONDS
        self.prefix = f"repos/{repository}/actions"

    def api(self, path: str) -> bytes:
        """Use the hosted gh CLI for bounded, read-only authenticated requests."""
        remaining = self.deadline - monotonic()
        if remaining <= 0:
            raise HistoryUnavailable("Actions history time budget exhausted")
        # gh follows signed artifact redirects. Keep its diagnostics and credentials out of reports.
        with TemporaryFile() as output:
            try:
                process = subprocess.run(
                    [
                        "gh",
                        "api",
                        "--hostname",
                        "github.com",
                        "--method",
                        "GET",
                        "-H",
                        "X-GitHub-Api-Version: 2022-11-28",
                        f"{self.prefix}/{path}",
                    ],
                    env={**os.environ, "GH_DEBUG": "", "GH_PROMPT_DISABLED": "1"},
                    stdout=output,
                    stderr=subprocess.DEVNULL,
                    timeout=min(remaining, 6),
                    check=False,
                )
            except (OSError, subprocess.TimeoutExpired):
                raise HistoryUnavailable("Actions history request unavailable") from None
            if process.returncode or output.tell() > ARCHIVE_LIMIT:
                raise HistoryUnavailable("Actions history response unavailable or too large")
            output.seek(0)
            return output.read(ARCHIVE_LIMIT + 1)

    def document(self, path: str) -> dict:
        """Decode an Actions API object."""
        try:
            data = json.loads(self.api(path))
        except (UnicodeError, json.JSONDecodeError, RecursionError):
            raise HistoryUnavailable("Invalid Actions history response") from None
        if not isinstance(data, dict):
            raise HistoryUnavailable("Invalid Actions history response")
        return data

    def workflow_id(self) -> int:
        """Verify the currently running attempt and obtain its owning workflow."""
        current = self.document(f"runs/{self.run_id}")
        workflow = positive_int(current.get("workflow_id"))
        if (
            workflow is None
            or current.get("id") != self.run_id
            or current.get("run_attempt") != self.attempt
        ):
            raise HistoryUnavailable("Unable to identify the current Actions attempt")
        return workflow

    def prior_runs(self, workflow: int) -> list[tuple[int, int]]:
        """Select recent completed first-party runs, newest workflow run first."""
        query = urlencode({"status": "completed", "per_page": MAX_RUNS})
        records = self.document(f"workflows/{workflow}/runs?{query}")
        candidates = []
        for info in rows(records, "workflow_runs", MAX_RUNS):
            run_id = positive_int(info.get("id"))
            attempt = positive_int(info.get("run_attempt"))
            head = info.get("head_repository")
            if (
                run_id is not None
                and run_id < self.run_id
                and attempt is not None
                and info.get("workflow_id") == workflow
                and info.get("status") == "completed"
                and info.get("event") in ("push", "schedule", "workflow_dispatch")
                and isinstance(head, dict)
                and isinstance(head.get("full_name"), str)
                and head["full_name"].casefold() == self.repository.casefold()
            ):
                candidates.append((run_id, attempt))
        return sorted(set(candidates), reverse=True)

    def artifact(
        self,
        run_id: int,
        attempt: int,
        *,
        allow_legacy: bool = False,
        require_success: bool = False,
    ) -> Artifact | None:
        """Find an exact artifact, then verify its corresponding attempt and install step."""
        legacy = f"{self.row.artifact_prefix}-{run_id}"
        names = [f"{legacy}-attempt-{attempt}"]
        if allow_legacy:
            names.append(legacy)
        for name in names:
            query = urlencode({"name": name, "per_page": 10})
            records = self.document(f"runs/{run_id}/artifacts?{query}")
            matching = [
                row
                for row in rows(records, "artifacts", 10)
                if row.get("name") == name and row.get("expired") is False
            ]
            if not matching:
                continue
            if len(matching) != 1:
                raise HistoryUnavailable("Ambiguous installation artifact")
            artifact_id = positive_int(matching[0].get("id"))
            size = positive_int(matching[0].get("size_in_bytes"))
            if artifact_id is None or size is None or size > ARCHIVE_LIMIT:
                raise HistoryUnavailable("Invalid installation artifact size or identity")
            job = self.install_job(run_id, attempt)
            if name == legacy:
                created_at = actions_timestamp(matching[0].get("created_at"))
                if created_at is None or not uploaded_during(
                    job, self.row.upload_step_name, created_at
                ):
                    continue
            job_id, outcome = self.install_step(job)
            if require_success and outcome != "success":
                return None
            files = read_archive(self.api(f"artifacts/{artifact_id}/zip"))
            return Artifact(run_id, attempt, job_id, artifact_id, name, outcome, files)
        return None

    def install_job(self, run_id: int, attempt: int) -> dict:
        """Find the matrix row without downloading every job in a large workflow run."""
        # Larger responses can time out when GitHub returns every step on each job.
        for page_size in (10, 5):
            try:
                job = self.search_jobs(run_id, attempt, page_size)
            except HistoryUnavailable:
                continue
            if job is not None:
                return job
            break
        raise HistoryUnavailable("Unable to find the historical installation job")

    def search_jobs(self, run_id: int, attempt: int, page_size: int) -> dict | None:
        """Read exact-attempt pages until the installer is found or the page bound is reached."""
        total = None
        for offset in range(0, MAX_JOBS, page_size):
            query = {"per_page": page_size}
            if offset:
                query["page"] = offset // page_size + 1
            jobs = self.document(f"runs/{run_id}/attempts/{attempt}/jobs?{urlencode(query)}")
            count = jobs.get("total_count")
            if type(count) is not int or not 0 <= count <= MAX_JOBS or total not in (None, count):
                raise HistoryUnavailable("Invalid installation jobs inventory")
            total = count
            page_jobs = rows(jobs, "jobs", page_size)
            if len(page_jobs) != min(page_size, total - offset):
                raise HistoryUnavailable("Incomplete installation jobs page")
            matching = [
                job
                for job in page_jobs
                if isinstance(job.get("name"), str)
                and job["name"].rsplit(" / ", 1)[-1] == self.row.job_name
                and job.get("run_id") == run_id
                and job.get("run_attempt") == attempt
            ]
            if len(matching) == 1:
                return matching[0]
            if len(matching) > 1:
                return None
            if offset + page_size >= total:
                break
        return None

    def install_step(self, job: dict) -> tuple[int, str]:
        """Accept only a unique completed installer step with a recognized outcome."""
        steps = completed_steps(job, self.row.step_name)
        job_id = positive_int(job.get("id"))
        if len(steps) == 1 and job_id is not None and steps[0].get("conclusion") in OUTCOMES:
            return job_id, steps[0]["conclusion"]
        raise HistoryUnavailable("Unable to verify the historical installation step")


def completed_steps(job: dict, name: str) -> list[dict]:
    """Find exact completed step names without copying other step metadata."""
    return [
        step
        for step in rows(job, "steps", 100)
        if step.get("name") == name and step.get("status") == "completed"
    ]


def uploaded_during(job: dict, step_name: str, created_at: datetime) -> bool:
    """Attribute a run-only artifact to the attempt that actually uploaded it."""
    uploads = completed_steps(job, step_name)
    if len(uploads) != 1 or uploads[0].get("conclusion") != "success":
        return False
    start = actions_timestamp(uploads[0].get("started_at"))
    end = actions_timestamp(uploads[0].get("completed_at"))
    return start is not None and end is not None and start <= created_at <= end


def actions_timestamp(value: object) -> datetime | None:
    """Accept only bounded, timezone-aware timestamp strings from Actions."""
    if not isinstance(value, str) or len(value) > 40:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def rows(document: dict, key: str, limit: int) -> list[dict]:
    """Accept a bounded list of object rows."""
    data = document.get(key)
    if (
        not isinstance(data, list)
        or len(data) > limit
        or any(not isinstance(x, dict) for x in data)
    ):
        raise HistoryUnavailable("Invalid Actions response rows")
    return data


def positive_int(value: object) -> int | None:
    """Accept an Actions numeric identifier or small positive count."""
    if type(value) is int and 0 < value < 10**20:
        return value
    return None
