"""Summarize one unlocked archive install against the latest available comparable run."""

import argparse
from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path
import re
from typing import Literal, TypedDict

from ci_archive_install_history import (
    ActionsHistory,
    Artifact,
    HistoryUnavailable,
    InstallRow,
    MAX_ATTEMPTS,
    MEMBER_LIMITS,
    OUTCOMES,
    positive_int,
)


MAX_PACKAGES = 256
SUMMARY_ROWS = 20
PACKAGE = r"[A-Za-z0-9](?:[A-Za-z0-9_.-]{0,126}[A-Za-z0-9])?"
VERSION = re.compile(
    r"(?:[0-9]+!)?[0-9]+(?:\.[0-9]+)*(?:(?:a|b|rc)[0-9]+)?"
    r"(?:\.post[0-9]+)?(?:\.dev[0-9]+)?(?:\+[a-z0-9]+(?:\.[a-z0-9]+)*)?"
)
REQUIREMENT = re.compile(rf"({PACKAGE})(?:==([^\s]+)|\s+@\s+\S[^\r\n]*)")
LOG_CHANGE = re.compile(rf"\+\s+({PACKAGE})==([^\s]+)(?:\s+\(from .+\))?")
LOG_BUILD = re.compile(rf"Built\s+({PACKAGE})(?:==([^\s]+)|\s+@\s+\S[^\r\n]*)")
UV_PHASE = re.compile(
    r"(Resolved|Prepared|Installed) [0-9]{1,6} packages? in "
    r"(?:(\d{1,3})h )?(?:(\d{1,3})m )?([0-9]{1,5}(?:\.[0-9]{1,6})?)(ms|µs|us|ns|s)"
)
PHASES = ("resolved", "prepared", "installed")
HistoryStatus = Literal["available", "no_comparable_artifact", "incomplete", "unavailable"]


@dataclass(frozen=True, slots=True)
class Observation:
    """Normalized data only. Missing version and timing evidence are represented by null."""

    outcome: str
    inventory_status: str
    versions: dict[str, str | None]
    confirmed_builds: dict[str, str | None]
    log_available: bool
    phase_seconds: dict[str, float | None]
    source_commit: str | None


class ChangeSet(TypedDict):
    """Package presence and version changes between complete inventories."""

    added: dict[str, str | None]
    removed: dict[str, str | None]
    changed: dict[str, list[str]]
    unknown: list[str]


def normalized_name(name: str) -> str:
    """Use the standard distribution-name normalization for comparisons."""
    return re.sub(r"[-_.]+", "-", name).lower()


def valid_version(value: str | None) -> str | None:
    """Accept bounded normalized version syntax, excluding locators and arbitrary text."""
    return value if value is not None and len(value) <= 80 and VERSION.fullmatch(value) else None


def decode(content: bytes | None) -> str | None:
    """Decode CI text files, returning unknown on malformed or absent evidence."""
    if content is None:
        return None
    try:
        encoding = "utf-16" if content.startswith((b"\xff\xfe", b"\xfe\xff")) else "utf-8-sig"
        return content.decode(encoding)
    except UnicodeError:
        return None


def source_commit(content: bytes | None) -> str | None:
    """Retain only a full source commit SHA from the supplied source metadata."""
    try:
        value = json.loads(decode(content) or "null")
    except (json.JSONDecodeError, RecursionError):
        return None
    if isinstance(value, dict):
        sha = value.get("candidate_sha") or value.get("server_commit")
        if isinstance(sha, str) and re.fullmatch(r"[0-9a-f]{40}", sha):
            return sha
    return None


def observe(files: dict[str, bytes], outcome: str) -> Observation:
    """Parse the uv install and freeze outputs with no raw lines in the report."""
    log = decode(files.get("install.log"))
    freeze = decode(files.get("resolved.txt"))
    phases: dict[str, float | None] = dict.fromkeys(PHASES)
    committed_versions = {}
    builds = {}
    for raw in (log or "").splitlines():
        line = re.sub(r"\x1b\[[0-9;]*m", "", raw).strip()
        if match := UV_PHASE.fullmatch(line):
            phase, hours, minutes, elapsed, unit = match.groups()
            multiplier = {"s": 1, "ms": 1e-3, "µs": 1e-6, "us": 1e-6, "ns": 1e-9}[unit]
            phases[phase.lower()] = (
                int(hours or 0) * 3600 + int(minutes or 0) * 60 + float(elapsed) * multiplier
            )
        if (match := LOG_CHANGE.fullmatch(line)) and len(committed_versions) < MAX_PACKAGES:
            if version := valid_version(match[2]):
                committed_versions[normalized_name(match[1])] = version
        if (match := LOG_BUILD.fullmatch(line)) and len(builds) < MAX_PACKAGES:
            builds[normalized_name(match[1])] = valid_version(match[2])

    versions = {}
    complete = outcome == "success"
    for raw in (freeze or "").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        match = REQUIREMENT.fullmatch(line)
        if match is None:
            complete = False
            continue
        name = normalized_name(match[1])
        if name in versions or len(versions) == MAX_PACKAGES:
            complete = False
            continue
        versions[name] = valid_version(match[2]) if match[2] else committed_versions.get(name)
    if not versions or not complete:
        # A missing or partial freeze can still retain individually logged installs.
        for name, version in committed_versions.items():
            if len(versions) == MAX_PACKAGES:
                break
            versions.setdefault(name, version)
        complete = False
    elif any(versions.get(name) != version for name, version in committed_versions.items()):
        # The freeze owns observed versions. Inconsistency prevents comparison.
        complete = False
    return Observation(
        outcome,
        ("complete" if complete else "partial") if versions else "unavailable",
        versions,
        {name: version or committed_versions.get(name) for name, version in builds.items()},
        log is not None and bool(log.strip()),
        phases,
        source_commit(files.get("source.json")),
    )


def compare(current: Observation, baseline: Observation) -> ChangeSet | None:
    """Calculate deltas only when both package inventories are complete."""
    if current.inventory_status != "complete" or baseline.inventory_status != "complete":
        return None
    before, after = baseline.versions, current.versions
    changed, unknown = {}, []
    for name in sorted(before.keys() & after.keys()):
        old, new = before[name], after[name]
        if old is None or new is None:
            unknown.append(name)
        elif old != new:
            changed[name] = [old, new]
    return {
        "added": {name: after[name] for name in sorted(after.keys() - before.keys())},
        "removed": {name: before[name] for name in sorted(before.keys() - after.keys())},
        "changed": changed,
        "unknown": unknown,
    }


def historical_evidence(artifact: Artifact) -> dict:
    """Keep the exact Actions evidence identifiers alongside parsed observations."""
    return {
        "run_id": artifact.run_id,
        "attempt": artifact.attempt,
        "job_id": artifact.job_id,
        "artifact_id": artifact.artifact_id,
        "artifact_name": artifact.artifact_name,
        **asdict(observe(artifact.files, artifact.outcome)),
    }


def find_baseline(history: ActionsHistory, workflow: int) -> tuple[Artifact | None, HistoryStatus]:
    """Return a verified baseline and indicate whether the bounded search had gaps."""
    unavailable = False
    observed = False
    for run_id, latest in history.prior_runs(workflow):
        for attempt in range(latest, max(latest - MAX_ATTEMPTS, 0), -1):
            try:
                artifact = history.artifact(
                    run_id, attempt, allow_legacy=True, require_success=True
                )
            except HistoryUnavailable:
                unavailable = True
                continue
            observed = True
            if (
                artifact
                and observe(artifact.files, artifact.outcome).inventory_status == "complete"
            ):
                return artifact, "incomplete" if unavailable else "available"
    if unavailable:
        return None, "incomplete" if observed else "unavailable"
    return None, "no_comparable_artifact"


def list_items(values: list[str]) -> str:
    """Limit long package lists in the GitHub step summary."""
    extra = len(values) - SUMMARY_ROWS
    return ", ".join(values[:SUMMARY_ROWS]) + (f" and {extra} more" if extra > 0 else "")


def render(data: dict) -> str:
    """Render fixed Markdown using only parsed, allowlisted data fields."""
    current, baseline, previous = (data[key] for key in ("current", "baseline", "previous_attempt"))
    lines = [
        "\n## Unlocked archive install comparison\n",
        f"{data['kind'].capitalize()} source on `{data['runner']}` / Python `{data['python']}`.",
        f"Current run `{data['run_id'] or 'unknown'}`, attempt `{data['attempt'] or 'unknown'}`. "
        f"Install outcome `{current['outcome']}`, inventory `{current['inventory_status']}`.",
    ]
    if current["source_commit"]:
        lines.append(f"Current archive source commit `{current['source_commit']}`.")
    for label, observation in (("Prior-run baseline", baseline), ("Previous attempt", previous)):
        if observation:
            lines.append(
                f"{label} from run `{observation['run_id']}`, attempt `{observation['attempt']}`, "
                f"job `{observation['job_id']}`, artifact `{observation['artifact_id']}`. "
                f"Install outcome `{observation['outcome']}`."
            )
        elif label == "Previous attempt" and (data["attempt"] or 0) > 1:
            lines.append(f"{label} unavailable within the retained Actions history.")
    if data["history_status"] == "unavailable":
        lines.append("Actions history could not be read for this run.")
    elif data["history_status"] == "incomplete":
        lines.append("Some eligible Actions history could not be assessed.")
    elif data["history_status"] == "no_comparable_artifact":
        lines.append(
            "No comparable successful install was found in the bounded Actions history search."
        )
    lines.extend(
        [
            "",
            "| uv phase | Current | Prior-run baseline | Previous attempt |",
            "| --- | ---: | ---: | ---: |",
        ]
    )
    for phase in PHASES:
        cells = []
        for observation in (current, baseline, previous):
            seconds = observation["phase_seconds"][phase] if observation else None
            cells.append(f"{seconds:.3f} s" if seconds is not None else "unknown")
        lines.append(f"| {phase.capitalize()} | {' | '.join(cells)} |")

    lines.append("")
    for label, observation in (
        ("Current", current),
        ("Baseline", baseline),
        ("Previous", previous),
    ):
        if observation is not None:
            builds = [
                f"`{name}=={version}`" if version else f"`{name}`"
                for name, version in sorted(observation["confirmed_builds"].items())
            ]
            finding = list_items(builds) or (
                "no explicit uv build completions observed"
                if observation["log_available"]
                else "unknown, install log unavailable"
            )
            lines.append(f"- {label} confirmed source builds — {finding}.")

    changes = data["changes"]
    lines.append("")
    if changes is None:
        lines.append("Package deltas require complete current and baseline inventories.")
    else:
        for key in ("added", "changed", "removed", "unknown"):
            if key == "changed":
                values = [f"`{name}` {old} → {new}" for name, (old, new) in changes[key].items()]
            elif key == "unknown":
                values = [f"`{name}`" for name in changes[key]]
            else:
                values = [f"`{name}=={v or 'unknown'}`" for name, v in changes[key].items()]
            label = "Unverified versions" if key == "unknown" else key.capitalize()
            lines.append(f"- {label} ({len(values)}) — {list_items(values) or 'none'}.")
    return "\n".join(lines) + "\n\n"


def read_local(directory: Path) -> dict[str, bytes]:
    """Read the known local evidence members within the historical archive limits."""
    files = {}
    for name, limit in MEMBER_LIMITS.items():
        path = directory / name
        try:
            if path.is_symlink() or not path.is_file():
                continue
            with path.open("rb") as stream:
                content = stream.read(limit + 1)
            if len(content) <= limit:
                files[name] = content
        except OSError:
            continue
    return files


def action_number(name: str) -> int | None:
    """Accept an Actions identifier from the fixed runner environment."""
    raw = os.environ.get(name, "")
    return positive_int(int(raw)) if re.fullmatch(r"[0-9]{1,20}", raw) else None


def build_report(row: InstallRow, current: Observation) -> dict:
    """Produce available observations even if the runner cannot access any retained history."""
    repository = os.environ.get("GITHUB_REPOSITORY", "")
    valid_repository = re.fullmatch(r"[A-Za-z0-9-]{1,39}/[A-Za-z0-9_.-]{1,100}", repository)
    if not valid_repository or repository.rsplit("/", 1)[-1] in (".", ".."):
        repository = ""
    run_id = action_number("GITHUB_RUN_ID")
    attempt = action_number("GITHUB_RUN_ATTEMPT")
    workflow, baseline, previous = None, None, None
    status: HistoryStatus = "unavailable"
    if repository and run_id and attempt and os.environ.get("GH_TOKEN"):
        history = ActionsHistory(repository, run_id, attempt, row)
        try:
            workflow = history.workflow_id()
            baseline, status = find_baseline(history, workflow)
        except HistoryUnavailable:
            pass
        if workflow is not None and attempt > 1:
            try:
                previous = history.artifact(run_id, attempt - 1, allow_legacy=True)
            except HistoryUnavailable:
                pass
    return {
        "schema": "unity-mcp/ci-archive-install@1",
        "repository": repository or None,
        "workflow_id": workflow,
        "run_id": run_id,
        "attempt": attempt,
        **asdict(row),
        "current": asdict(current),
        "baseline": historical_evidence(baseline) if baseline else None,
        "previous_attempt": historical_evidence(previous) if previous else None,
        "changes": compare(current, observe(baseline.files, baseline.outcome))
        if baseline
        else None,
        "history_status": status,
    }


def main() -> None:
    """Write a diagnostic artifact and append a best-effort GitHub step summary."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kind", choices=("candidate", "published"), required=True)
    parser.add_argument("--os", dest="runner", required=True)
    parser.add_argument("--python", required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--install-outcome", choices=(*OUTCOMES, "", "unknown"), required=True)
    args = parser.parse_args()
    if not re.fullmatch(r"(?:ubuntu|windows|macos)-[a-z0-9.-]{1,30}", args.runner):
        parser.error("Unsupported runner label")
    if not re.fullmatch(r"3\.[0-9]{1,2}", args.python):
        parser.error("Expected a Python 3 minor version")
    row = InstallRow(args.kind, args.runner, args.python)
    data = build_report(row, observe(read_local(args.evidence), args.install_outcome or "unknown"))
    args.evidence.mkdir(parents=True, exist_ok=True)
    (args.evidence / "install-report.json").write_text(
        json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    if summary := os.environ.get("GITHUB_STEP_SUMMARY"):
        with Path(summary).open("a", encoding="utf-8") as stream:
            stream.write(render(data))


if __name__ == "__main__":
    main()
