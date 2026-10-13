"""Durable, offline coordination for local work programs.

Run with Server/.venv/Scripts/python.exe tools/feature_program.py --help.
The registry is deliberately mutable: every transition appends its durable history.
"""

from __future__ import annotations

import argparse
from collections import Counter, deque
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
import tempfile
from typing import Callable, Final, Iterator, TypeAlias

# Extensions lazily import core helpers. Reuse this entrypoint module so rejected
# input has the same ProgramError identity under script, module and imported runs.
if __name__ == "__main__":
    core_module = f"{__package__}.feature_program" if __package__ else "feature_program"
    sys.modules.setdefault(core_module, sys.modules[__name__])

if __package__:
    import tools.feature_program_authoring as authoring
    import tools.feature_program_brief as brief
    import tools.feature_program_coverage as coverage
    import tools.feature_program_coordination as coordination
    import tools.feature_program_phases as phases
    import tools.feature_program_planning as planning
    import tools.feature_program_presentation as presentation
    import tools.feature_program_questions as questions
else:
    import feature_program_authoring as authoring
    import feature_program_brief as brief
    import feature_program_coverage as coverage
    import feature_program_coordination as coordination
    import feature_program_phases as phases
    import feature_program_planning as planning
    import feature_program_presentation as presentation
    import feature_program_questions as questions


Json: TypeAlias = str | int | float | bool | None | list["Json"] | dict[str, "Json"]
Record: TypeAlias = dict[str, Json]
ROOT: Final = Path(__file__).resolve().parents[1]
DEFAULT_PROGRAM: Final = ".work/programs/default"
REPORT: Final = DEFAULT_PROGRAM + "/source/feature-opportunities.md"
TASK_ID: Final = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}")
DEVICE_NAMES: Final = frozenset({"con", "prn", "aux", "nul"}) | frozenset(
    f"{prefix}{number}" for prefix in ("com", "lpt") for number in range(1, 10)
)
CHECKS: Final = frozenset(
    {
        "acceptance",
        "scoped_tests",
        "format_lint",
        "native_editor",
        "native_player",
        "compatibility_docs",
        "performance_evidence",
        "docs_contract",
    }
)
STATES: Final = frozenset({"planned", "active", "blocked", "review", "done"})
OWNED_STATES: Final = frozenset({"active", "blocked", "review"})
SKIP_DIRS: Final = frozenset(
    {
        ".git",
        ".work",
        ".codegraph",
        ".venv",
        "venv",
        "__pycache__",
        ".pytest_cache",
        ".ruff_cache",
        "node_modules",
        "library",
        "temp",
        "logs",
        "obj",
    }
)


class ProgramError(RuntimeError):
    """A rejected input or transition; existing durable state must be preserved."""


@dataclass(frozen=True, slots=True)
class PreparedChange:
    """Detached proposal with the same read-only freshness guard used at publication."""

    state: Record
    before_publish: Callable[[], None] | None = None


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ProgramError(message)


def record(value: Json, label: str) -> Record:
    require(isinstance(value, dict), f"{label} must be an object")
    return value


def sequence(value: Json, label: str) -> list[Json]:
    require(isinstance(value, list), f"{label} must be an array")
    return value


def string(value: Json, label: str) -> str:
    require(isinstance(value, str) and bool(value.strip()), f"{label} must be nonblank text")
    return value


def strings(value: Json, label: str) -> list[str]:
    result = [string(item, label) for item in sequence(value, label)]
    require(len(result) == len(set(result)), f"{label} contains duplicates")
    return result


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical(value: Json) -> bytes:
    try:
        return (
            json.dumps(value, indent=2, ensure_ascii=True, sort_keys=True, allow_nan=False) + "\n"
        ).encode()
    except (ValueError, RecursionError) as exc:
        raise ProgramError(f"Invalid canonical JSON: {exc}") from exc


def digest(value: Json) -> str:
    return sha(canonical(value))


def utc() -> str:
    return coordination.observed_timestamp(datetime.now(timezone.utc).isoformat(timespec="seconds"))


def communication_language(value: Json) -> str | None:
    """Validate a portable BCP 47-shaped tag without guessing a language."""
    if value is None:
        return None
    require(
        isinstance(value, str)
        and len(value) <= 63
        and re.fullmatch(r"[A-Za-z]{2,8}(?:-[A-Za-z0-9]{1,8})*", value) is not None,
        "communication_language must be a portable language tag or null",
    )
    return value


def portable_task_id(value: str) -> bool:
    """Task identifiers also become filenames, including on Windows."""
    return (
        TASK_ID.fullmatch(value) is not None
        and not value.endswith(".")
        and value.split(".", 1)[0].casefold() not in DEVICE_NAMES
    )


def check_policy(state: Record) -> tuple[set[str], set[str]]:
    catalog = set(strings(state.get("check_catalog", sorted(CHECKS)), "check_catalog"))
    default_minimum = (
        ["acceptance"]
        if "check_catalog" in state
        else ["acceptance", "scoped_tests", "format_lint"]
    )
    minimum = set(strings(state.get("minimum_checks", default_minimum), "minimum_checks"))
    require(
        bool(catalog)
        and "acceptance" in minimum
        and minimum <= catalog
        and all(TASK_ID.fullmatch(name) for name in catalog),
        "Invalid verification catalog/minimum",
    )
    return catalog, minimum


def safe_relative(name: str) -> str:
    """Reject sensitive or escaping paths before checking any filesystem contents."""
    require("\\" not in name, "Use repository-relative forward-slash paths")
    require(
        all(ord(character) >= 32 for character in name), "Control characters are invalid in paths"
    )
    path = PurePosixPath(name)
    parts = tuple(part.casefold() for part in path.parts)
    require(bool(parts) and not path.is_absolute(), f"Relative path required: {name}")
    require(
        all(part not in {".", ".."} and ":" not in part for part in parts), f"Unsafe path: {name}"
    )
    require(not any(part.startswith(".env") for part in parts), f"Sensitive path: {name}")
    require(
        not any(part in {".aws", ".ssh", ".codex", ".agents", ".git"} for part in parts),
        f"Private path: {name}",
    )
    require(
        "/assets/resources/gamedata/" not in "/" + "/".join(parts) + "/",
        f"Excluded data path: {name}",
    )
    require(
        path.suffix.casefold() not in {".pem", ".key", ".pfx", ".p12"}, f"Credential path: {name}"
    )
    require(
        path.name.casefold()
        not in {"credentials", "credentials.json", "secrets.json", "id_rsa", "id_ed25519"},
        f"Credential path: {name}",
    )
    return path.as_posix()


def safe_path(root: Path, name: str) -> Path:
    relative = safe_relative(name)
    path = root / relative
    cursor = root
    require(root.resolve() == root.absolute(), "Repository/program root must not contain links")
    for part in PurePosixPath(relative).parts:
        cursor /= part
        require(
            not cursor.is_symlink() and not getattr(cursor, "is_junction", lambda: False)(),
            f"Linked path rejected: {name}",
        )
    require(path.resolve().is_relative_to(root.resolve()), f"Path outside root: {name}")
    return path


def bounded_bytes(path: Path, limit: int) -> bytes:
    """Bound allocations even if a regular file grows between metadata and reading."""
    with path.open("rb") as stream:
        content = stream.read(limit + 1)
    require(len(content) <= limit, f"File exceeds bounded read: {path.name}")
    return content


@dataclass(slots=True)
class ReadBudget:
    """One validation read ledger; mutation tracks actual bytes and avoids repeated I/O."""

    remaining: int = 72 * 1024 * 1024
    cached: dict[Path, bytes] | None = None

    def read(self, path: Path, limit: int) -> bytes:
        if self.cached is None:
            self.cached = {}
        if path in self.cached:
            content = self.cached[path]
            require(len(content) <= limit, f"File exceeds bounded read: {path.name}")
            return content
        content = bounded_bytes(path, min(limit, self.remaining))
        self.remaining -= len(content)
        self.cached[path] = content
        return content


@dataclass(slots=True)
class _SourceObservations:
    facts: dict[tuple[Path, Path, tuple[str, ...]], Record] = field(default_factory=dict)
    mismatched: bool = False

    def observe(self, program: Program, scopes: list[str], snapshot: Record) -> None:
        key = (program.repo, program.directory, tuple(sorted(scopes)))
        if key in self.facts and self.facts[key] != snapshot:
            self.mismatched = True
            raise ProgramError("Observed source changed during snapshot; refresh")
        self.facts.setdefault(key, dict(snapshot))

    def verify(self) -> None:
        require(not self.mismatched, "Observed source changed during snapshot; refresh")
        token = _source_observations.set(None)
        try:
            reader = ReadBudget(128 * 1024 * 1024)
            for (repo, directory, scopes), captured in self.facts.items():
                require(
                    Program(repo, directory).source_snapshot(list(scopes), reader) == captured,
                    "Observed source changed during snapshot; refresh",
                )
        finally:
            _source_observations.reset(token)


_source_observations: ContextVar[_SourceObservations | None] = ContextVar(
    "feature_program_source_observations", default=None
)


def read_json(path: Path, limit: int = 8 * 1024 * 1024, budget: ReadBudget | None = None) -> Record:
    def finite_json(value: str) -> None:
        raise ProgramError(f"Nonfinite JSON constant rejected: {value}")

    def finite_float(value: str) -> float:
        parsed = float(value)
        require(math.isfinite(parsed), f"Nonfinite JSON number rejected: {value}")
        return parsed

    def unique(pairs: list[tuple[str, Json]]) -> Record:
        result: Record = {}
        for key, value in pairs:
            require(key not in result, f"Duplicate JSON key: {key}")
            result[key] = value
        return result

    try:
        value = json.loads(
            (budget.read(path, limit) if budget is not None else bounded_bytes(path, limit)).decode(
                "utf-8"
            ),
            object_pairs_hook=unique,
            parse_constant=finite_json,
            parse_float=finite_float,
        )
    except (UnicodeError, ValueError, RecursionError) as exc:
        raise ProgramError(f"Invalid JSON in {path.name}: {exc}") from exc
    return record(value, path.name)


def read_json_frozen(
    path: Path, limit: int = 1024 * 1024, budget: ReadBudget | None = None
) -> tuple[Record, str]:
    """Return parsed metadata and the digest of exactly the same bounded bytes."""
    budget = budget if budget is not None else ReadBudget(limit)
    content = budget.read(path, limit)
    return read_json(path, limit=limit, budget=budget), sha(content)


def atomic_write(path: Path, data: bytes) -> None:
    """Replace one regular file atomically; the caller holds the program lock."""
    path.parent.mkdir(parents=True, exist_ok=True)
    require(not path.is_symlink(), f"Linked output rejected: {path.name}")
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=path.parent, prefix=f".{path.name}.", delete=False
        ) as stream:
            temporary = Path(stream.name)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def extract_sections(data: bytes, expected_ids: list[str] | None = None) -> Record:
    """Keep complete original feature sections, including exact newline bytes."""
    try:
        text = data.decode("utf-8")
    except UnicodeError as exc:
        raise ProgramError("Source must be valid UTF-8") from exc
    headings = list(re.finditer(r"(?m)^#{1,3} [^\r\n]+", text))
    sections: Record = {}
    for index, heading in enumerate(headings):
        match = re.fullmatch(
            r"### ([A-Za-z0-9][A-Za-z0-9_.-]{0,63})\. (.+)", heading[0].rstrip("\r")
        )
        if not match:
            continue
        task_id, title = match.groups()
        require(portable_task_id(task_id), f"Task ID is not portable: {task_id}")
        require(task_id not in sections, f"Duplicate report ID: {task_id}")
        end = headings[index + 1].start() if index + 1 < len(headings) else len(text)
        section = text[heading.start() : end]
        sections[task_id] = {
            "title": title,
            "text": section,
            "sha256": sha(section.encode("utf-8")),
            "line": text.count("\n", 0, heading.start()) + 1,
        }
    require(bool(sections), "Source must contain at least one task section: ### ID. Title")
    require(
        len(sections) == len({name.casefold() for name in sections}),
        "Source task IDs collide on portable filesystems",
    )
    if expected_ids is not None:
        require(
            set(sections) == set(expected_ids),
            "Source and plan must contain the exact same unique task IDs",
        )
    return sections


def git(root: Path, arguments: list[str]) -> str:
    result = subprocess.run(["git", "-C", str(root), *arguments], capture_output=True, check=False)
    require(
        result.returncode == 0,
        "Git metadata unavailable: " + result.stderr.decode(errors="replace").strip(),
    )
    return result.stdout.decode("utf-8", errors="surrogateescape").rstrip("\r\n")


@dataclass(frozen=True, slots=True)
class Program:
    """A repository-bound registry and its derived, recoverable views."""

    repo: Path
    directory: Path

    @contextmanager
    def lock(
        self,
        relocation_recovery: bool = False,
        succession_recovery: bool = False,
        publication_recovery: bool = False,
    ) -> Iterator[None]:
        planning.validate_publication_endpoint(self.repo, self.directory)
        if publication_recovery:
            pass  # Explicit ledger recovery still verifies exact endpoints under this lock.
        elif succession_recovery:
            require(
                not self.path("RELOCATED.json").exists()
                and not self.path("RELOCATION_INCOMPLETE.json").exists(),
                "Succession recovery cannot bypass relocation guards",
            )
        elif relocation_recovery:
            require(
                self.path("RELOCATION_INCOMPLETE.json").is_file()
                and not self.path("RELOCATED.json").exists(),
                "Recovery requires an incomplete destination",
            )
        else:
            self.require_current_location()
        self.directory.mkdir(parents=True, exist_ok=True)
        path = safe_path(self.directory, ".feature-program.lock")
        try:
            descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError as exc:
            raise ProgramError(
                "Program busy: lock exists; never auto-steal it. Inspect its owner first."
            ) from exc
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                json.dump({"pid": os.getpid(), "created_at": utc()}, stream)
                stream.flush()
                os.fsync(stream.fileno())
            yield
        finally:
            path.unlink()

    def path(self, relative: str) -> Path:
        return safe_path(self.directory, relative)

    def require_current_location(self) -> None:
        try:
            planning.validate_publication_endpoint(self.repo, self.directory)
            planning.publication_runtime_guard(self)
        except planning.PlanningError as exc:
            raise ProgramError(str(exc)) from exc
        phases.succession_guard(self)
        marker = self.path("RELOCATED.json")
        if marker.exists():
            destination = read_json(marker, limit=64 * 1024).get("destination")
            raise ProgramError(f"Program archived after relocation; explicitly use {destination}")
        require(
            not self.path("RELOCATION_INCOMPLETE.json").exists(),
            "Relocation publication incomplete; inspect its recovery receipt",
        )

    def current_head(self) -> str:
        return git(self.repo, ["rev-parse", "HEAD"])

    def source_digest(self, scope: list[str]) -> str:
        return digest(self.source_snapshot(scope))

    def pending_questions(
        self, state: Record, objective_id: str, task_ids: list[str]
    ) -> list[Record]:
        return questions.pending_questions(state, objective_id, task_ids)

    def source_snapshot(self, scopes: list[str], budget: ReadBudget | None = None) -> Record:
        require(bool(scopes), "At least one explicit scope is required")
        snapshot: Record = {}
        total_bytes = 0
        reader = budget if budget is not None else ReadBudget(128 * 1024 * 1024)

        def fingerprint_file(path: Path) -> str:
            nonlocal total_bytes
            require(len(snapshot) < 10000, "Explicit source scope exceeds fingerprint budget")
            content = reader.read(path, 128 * 1024 * 1024 - total_bytes)
            total_bytes += len(content)
            return sha(content)

        for scope in sorted(scopes):
            require(
                not any(part.casefold() == ".work" for part in PurePosixPath(scope).parts),
                "Private .work paths cannot be source scopes",
            )
            path = safe_path(self.repo, scope)
            require(
                path != self.repo and not self.directory.is_relative_to(path),
                "Root/program-wide claims are invalid",
            )
            if not path.exists():
                snapshot[scope] = "missing"
            elif path.is_file():
                snapshot[scope] = fingerprint_file(path)
            else:
                snapshot[scope + "/"] = "directory"
                for folder, dirs, files in os.walk(path, followlinks=False):
                    folder_path = Path(folder)
                    dirs[:] = sorted(name for name in dirs if name.casefold() not in SKIP_DIRS)
                    for name in list(dirs):
                        relative = (folder_path / name).relative_to(self.repo).as_posix()
                        try:
                            safe_path(self.repo, relative)
                        except ProgramError:
                            dirs.remove(name)
                    for name in sorted(files):
                        relative = (folder_path / name).relative_to(self.repo).as_posix()
                        try:
                            candidate = safe_path(self.repo, relative)
                        except ProgramError:
                            continue
                        require(candidate.is_file(), f"Not a regular scoped file: {relative}")
                        snapshot[relative] = fingerprint_file(candidate)
        observer = _source_observations.get()
        if observer is not None:
            observer.observe(self, scopes, snapshot)
        return snapshot

    def evidence(self, task_id: str, attempt: int, names: list[str]) -> list[Json]:
        require(bool(names), "Evidence cannot be empty")
        result: list[Json] = []
        prefix = f"evidence/{task_id}/attempt-{attempt}/"
        for name in names:
            require(name.startswith(prefix), f"Evidence must be attempt-specific under {prefix}")
            path = self.path(name)
            require(path.is_file() and path.stat().st_size > 0, f"Missing/empty evidence: {name}")
            require(path.stat().st_size <= 8 * 1024 * 1024, f"Evidence exceeds 8 MiB: {name}")
            result.append({"path": name, "sha256": sha(bounded_bytes(path, 8 * 1024 * 1024))})
        return result

    def frozen_json(self, task_id: str, attempt: int, name: str) -> tuple[Record, list[Json]]:
        """Parse exactly the captured bytes whose digest is admitted as evidence."""
        metadata, parsed_sha = read_json_frozen(self.path(name), limit=8 * 1024 * 1024)
        refs = self.evidence(task_id, attempt, [name])
        require(
            record(refs[0], "receipt reference")["sha256"] == parsed_sha,
            "Receipt changed while freezing",
        )
        return metadata, refs

    def verify_refs(self, refs: list[Json], budget: ReadBudget | None = None) -> None:
        budget = budget if budget is not None else ReadBudget(64 * 1024 * 1024)
        for value in refs:
            ref = record(value, "evidence reference")
            name = string(ref.get("path"), "evidence path")
            path = self.path(name)
            require(path.is_file(), f"Missing frozen evidence: {name}")
            require(
                sha(budget.read(path, 8 * 1024 * 1024)) == ref.get("sha256"),
                f"Frozen evidence changed: {name}",
            )

    def load(self) -> Record:
        self.require_current_location()
        state = read_json(self.path("backlog.json"))
        self.validate(state)
        return state

    def validate(
        self, state: Record, verify_artifacts: bool = True, *, prospective: bool = False
    ) -> None:
        budget = ReadBudget() if verify_artifacts else None
        require(state.get("schema_version") == 1, "Unsupported registry")
        string(state.get("program_id"), "program_id")
        communication_language(state.get("communication_language"))
        try:
            sections = planning.source_sections(
                self, state, verify_artifacts, budget, prospective=prospective
            )
        except (planning.PlanningError, coordination.GovernanceError) as exc:
            raise ProgramError(str(exc)) from exc
        tasks = sequence(state.get("tasks"), "tasks")
        require(len(tasks) <= 2000, "Registry exceeds 2000 tasks")
        by_id = {
            string(record(task, "task").get("id"), "task ID"): record(task, "task")
            for task in tasks
        }
        require(
            bool(tasks)
            and len(by_id) == len(tasks)
            and set(by_id) == set(sections)
            and len(by_id) == len({name.casefold() for name in by_id})
            and all(portable_task_id(name) for name in by_id),
            "Registry must contain exact unique reviewed IDs",
        )
        streams = sequence(state.get("workstreams"), "workstreams")
        stream_ids = [
            string(record(stream, "workstream").get("id"), "workstream ID") for stream in streams
        ]
        require(len(stream_ids) == len(set(stream_ids)), "Duplicate workstream")
        owners: set[str] = set()
        active: list[Record] = []
        check_catalog, minimum_checks = check_policy(state)
        policy_digest = digest(
            {"check_catalog": sorted(check_catalog), "minimum_checks": sorted(minimum_checks)}
        )
        require(
            state.get("verification_policy_sha256") is None
            or state.get("verification_policy_sha256") == policy_digest,
            "Frozen verification policy changed",
        )
        for task_id, task in by_id.items():
            require(task.get("workstream") in stream_ids, f"Unknown workstream: {task_id}")
            require(
                type(task.get("phase")) is int and task["phase"] > 0,
                f"Invalid phase: {task_id}",
            )
            required = strings(task.get("required_checks"), "required_checks")
            require(
                set(required) <= check_catalog and minimum_checks <= set(required),
                f"Invalid required checks: {task_id}",
            )
            for key in ("depends_on", "related_to", "conflict_groups", "candidate_paths"):
                values = strings(task.get(key), key)
                if key in {"depends_on", "related_to"}:
                    require(
                        set(values) <= set(by_id) and task_id not in values,
                        f"Invalid references: {task_id}",
                    )
                if key == "candidate_paths":
                    for path in values:
                        safe_relative(path)
            string(task.get("next_action"), "next_action")
            require(
                isinstance(task.get("status"), str) and task.get("status") in STATES,
                f"Invalid status: {task_id}",
            )
            source = record(task.get("source"), "source section")
            require(source == sections[task_id], f"Lossless source mismatch: {task_id}")
            require(
                sha(string(source.get("text"), "source text").encode()) == source.get("sha256"),
                f"Source section changed: {task_id}",
            )
            attempts = sequence(task.get("attempts"), "attempts")
            for number, value in enumerate(attempts, 1):
                attempt = record(value, "attempt")
                communication_language(attempt.get("communication_language"))
                require(attempt.get("number") == number, f"Invalid attempt sequence: {task_id}")
                string(attempt.get("owner"), "attempt owner")
                for scope in strings(attempt.get("scope"), "scope"):
                    safe_relative(scope)
                require(
                    digest(record(attempt.get("baseline"), "baseline"))
                    == attempt.get("baseline_digest"),
                    f"Baseline fingerprint changed: {task_id}",
                )
                for ref_value in sequence(attempt.get("evidence"), "attempt evidence"):
                    ref = record(ref_value, "evidence reference")
                    path = string(ref.get("path"), "evidence path")
                    require(
                        path.startswith(f"evidence/{task_id}/attempt-{number}/"),
                        "Evidence identity mismatch",
                    )
                    safe_relative(path)
                    require(
                        bool(
                            re.fullmatch(
                                r"[a-f0-9]{64}", string(ref.get("sha256"), "evidence hash")
                            )
                        ),
                        "Invalid evidence hash",
                    )
                if verify_artifacts:
                    self.verify_refs(sequence(attempt.get("evidence"), "attempt evidence"), budget)
                self.validate_resolution_history(state, task_id, attempt, budget)
                if attempt.get("status") == "done" and verify_artifacts:
                    self.verify_terminal(task, attempt, budget)
            if task["status"] in OWNED_STATES:
                owner = string(task.get("owner"), "owner")
                require(owner not in owners, f"Owner already has an active task: {owner}")
                owners.add(owner)
                require(
                    bool(attempts) and record(attempts[-1], "attempt").get("owner") == owner,
                    f"Ownership mismatch: {task_id}",
                )
                require(
                    task["status"] == record(attempts[-1], "attempt").get("status"),
                    f"Attempt status mismatch: {task_id}",
                )
                active.append(task)
            else:
                require(task.get("owner") is None, f"Unowned state has owner: {task_id}")
                if task["status"] == "done":
                    require(bool(attempts), f"Done task lacks reviewed attempt: {task_id}")
                    require(
                        self.current_attempt(task).get("status") == "done",
                        "Done task lacks completed latest attempt",
                    )
        dependency_counts = {
            task_id: len(strings(task["depends_on"], "depends_on"))
            for task_id, task in by_id.items()
        }
        dependents: dict[str, list[str]] = {task_id: [] for task_id in by_id}
        for task_id, task in by_id.items():
            for dependency in strings(task["depends_on"], "depends_on"):
                dependents[dependency].append(task_id)
        ready = deque(task_id for task_id, count in dependency_counts.items() if count == 0)
        visited = 0
        while ready:
            task_id = ready.popleft()
            visited += 1
            for dependent in dependents[task_id]:
                dependency_counts[dependent] -= 1
                if dependency_counts[dependent] == 0:
                    ready.append(dependent)
        require(visited == len(by_id), "Dependency cycle detected")
        for index, left in enumerate(active):
            for right in active[index + 1 :]:
                require(
                    not self.collisions(left, right),
                    f"Active scope/conflict collision: {left['id']} / {right['id']}",
                )
        sequence(state.get("events"), "events")
        record(state.get("source"), "report source")
        questions.validate_questions(state)
        coordination.validate_governance(state)
        phases.validate(state)
        coverage.validate(state)
        presentation.validate(state)
        authoring.validate_delivery_history(
            self, state, verify_artifacts=verify_artifacts, budget=budget
        )
        if verify_artifacts:
            self.verify_refs(coordination.evidence_references(state), budget)
            self.verify_refs(questions.evidence_references(state), budget)
            self.verify_refs(phases.evidence_references(state), budget)
            self.verify_refs(planning.evidence_references(state), budget)
            coverage.verify_history(self, state, budget)
            presentation.verify_history(self, state, budget)

    def verify_terminal(
        self, task: Record, attempt: Record, budget: ReadBudget | None = None
    ) -> None:
        """Terminal code is historical; its frozen acceptance records remain mandatory."""
        require(attempt.get("status") == "done", "Done task lacks completed attempt")
        budget = budget if budget is not None else ReadBudget()
        self.verify_refs(sequence(attempt.get("evidence"), "evidence"), budget)
        submission = record(attempt.get("submission"), "completed submission")
        require(
            digest(record(submission.get("source_snapshot"), "submitted source snapshot"))
            == submission.get("source_digest"),
            "Submitted snapshot digest mismatch",
        )
        receipt_ref = record(submission.get("receipt"), "completed receipt")
        review_ref = record(attempt.get("review"), "accepted review")
        require(
            receipt_ref in sequence(attempt["evidence"], "evidence")
            and review_ref in sequence(attempt["evidence"], "evidence"),
            "Terminal records lack frozen evidence",
        )
        receipt = read_json(self.path(string(receipt_ref["path"], "receipt path")), budget=budget)
        review = read_json(self.path(string(review_ref["path"], "review path")), budget=budget)
        for artifact in (receipt, review):
            require(
                artifact.get("task_id") == task["id"]
                and artifact.get("attempt") == attempt["number"],
                "Terminal artifact identity mismatch",
            )
            require(artifact.get("acceptance_complete") is True, "Terminal acceptance not complete")
        require(
            review.get("verdict") == "accepted" and review.get("unresolved_findings") == [],
            "Terminal review was not accepted",
        )
        require(
            review.get("receipt_sha256") == receipt_ref["sha256"]
            and review.get("source_digest") == submission["source_digest"],
            "Terminal review binding mismatch",
        )
        require(
            receipt.get("unresolved_acceptance") == [] and receipt.get("unresolved_blockers") == [],
            "Terminal acceptance/blockers unresolved",
        )
        checks = [record(value, "check") for value in sequence(receipt.get("checks"), "checks")]
        require(
            set(strings(task["required_checks"], "required_checks"))
            <= {
                string(check.get("id"), "check ID")
                for check in checks
                if check.get("status") == "passed"
            },
            "Terminal receipt lacks required passing checks",
        )
        for check in checks:
            if check.get("id") in {"native_editor", "native_player"}:
                require(
                    check.get("execution_kind") == "native",
                    "Terminal native check cannot be compilation",
                )

    @staticmethod
    def current_attempt(task: Record) -> Record:
        attempts = sequence(task["attempts"], "attempts")
        require(bool(attempts), "No claimed attempt")
        return record(attempts[-1], "attempt")

    @staticmethod
    def collisions(left: Record, right: Record) -> list[str]:
        reasons = sorted(
            set(strings(left["conflict_groups"], "groups"))
            & set(strings(right["conflict_groups"], "groups"))
        )
        left_scope = (
            strings(Program.current_attempt(left)["scope"], "scope")
            if left["status"] in OWNED_STATES
            else strings(left["candidate_paths"], "candidate paths")
        )
        right_scope = (
            strings(Program.current_attempt(right)["scope"], "scope")
            if right["status"] in OWNED_STATES
            else strings(right["candidate_paths"], "candidate paths")
        )
        for a in left_scope:
            for b in right_scope:
                if (
                    a.casefold() == b.casefold()
                    or a.casefold().startswith(b.casefold().rstrip("/") + "/")
                    or b.casefold().startswith(a.casefold().rstrip("/") + "/")
                ):
                    reasons.append(f"scope: {a} / {b}")
        return sorted(set(reasons))

    def task(self, state: Record, task_id: str) -> Record:
        for value in sequence(state["tasks"], "tasks"):
            task = record(value, "task")
            if task["id"] == task_id:
                return task
        raise ProgramError(f"Unknown task: {task_id}")

    def event(self, state: Record, action: str, detail: Record) -> None:
        sequence(state["events"], "events").append({"action": action, "timestamp": utc(), **detail})

    def initialize(self, args: argparse.Namespace) -> Record:
        self.require_current_location()
        require(
            not self.path("backlog.json").exists(),
            "Registry already exists; initialization never resets progress",
        )
        source_path = safe_path(self.repo, args.report)
        raw = bounded_bytes(source_path, 8 * 1024 * 1024)
        planning_path = safe_path(self.repo, args.planning)
        planning_budget = ReadBudget()
        plan = read_json(planning_path, budget=planning_budget)
        require(plan.get("schema_version") == 1, "Unsupported planning input")
        plan_ids = [
            string(record(value, "planning task").get("id"), "task ID")
            for value in sequence(plan.get("tasks"), "planning tasks")
        ]
        require(
            bool(plan_ids) and len(plan_ids) == len(set(plan_ids)),
            "Planning tasks must be nonempty and unique",
        )
        sections = extract_sections(raw, plan_ids)
        tasks: list[Json] = []
        for value in sequence(plan.get("tasks"), "planning tasks"):
            task = dict(record(value, "planning task"))
            task_id = string(task.get("id"), "task ID")
            require(task_id in sections, f"Unknown planning ID: {task_id}")
            task.update(
                {
                    "source": sections[task_id],
                    "status": "planned",
                    "owner": None,
                    "attempts": [],
                    "notes": [],
                }
            )
            tasks.append(task)
        state: Record = {
            "schema_version": 1,
            "program_id": string(plan.get("program_id"), "program_id"),
            "baseline_head": string(plan.get("baseline_head"), "baseline_head"),
            "initialized_head": git(self.repo, ["rev-parse", "HEAD"]),
            "source": {
                "path": safe_relative(args.report),
                "sha256": sha(raw),
                "review_baseline": args.review_baseline,
                "reference_base": safe_relative(args.reference_base),
                "task_ids": list(sections),
            },
            "planning_sha256": sha(planning_budget.read(planning_path, 8 * 1024 * 1024)),
            "planning_path": safe_relative(args.planning),
            "workstreams": plan.get("workstreams"),
            "tasks": tasks,
            "events": [],
            "checkpoint": None,
        }
        for key in (
            "categories",
            "actors",
            "handoffs",
            "director",
            "responsibilities",
            "objective",
            "check_catalog",
            "minimum_checks",
            "communication_language",
        ):
            if key in plan:
                state[key] = plan[key]
        catalog, minimum = check_policy(state)
        state["verification_policy_sha256"] = digest(
            {"check_catalog": sorted(catalog), "minimum_checks": sorted(minimum)}
        )
        self.validate(state)
        self.event(
            state,
            "init",
            {
                "actor": "leader",
                "previous_state": None,
                "new_state": "planned",
                "reason": "Lossless reviewed report import",
                "source_sha256": sha(raw),
            },
        )
        return state

    def check(self, state: Record) -> None:
        sections = planning.source_sections(self, state, True, ReadBudget())
        for value in sequence(state["tasks"], "tasks"):
            task = record(value, "task")
            require(
                task["source"] == sections[string(task["id"], "ID")],
                f"Lossless source mismatch: {task['id']}",
            )
            for attempt_value in sequence(task["attempts"], "attempts"):
                attempt = record(attempt_value, "attempt")
                self.verify_refs(sequence(attempt["evidence"], "evidence"))
                submission = attempt.get("submission")
                if (
                    submission is not None
                    and task["status"] == "review"
                    and attempt is self.current_attempt(task)
                ):
                    self.verify_submission(attempt)
        for name, data in self.views(state).items():
            path = self.path(name)
            require(
                path.is_file() and bounded_bytes(path, 8 * 1024 * 1024) == data.encode("utf-8"),
                f"Derived view drift: {name}; use render",
            )

    def claim(self, state: Record, args: argparse.Namespace) -> None:
        proposal = self.prepare_claim(state, args, observed_head=self.current_head())
        state.clear()
        state.update(proposal)

    def prepare_claim(
        self, state: Record, args: argparse.Namespace, *, observed_head: str
    ) -> Record:
        """Prepare a claim from a real metadata observation without publication or Git."""
        state = deepcopy(state)
        self.require_current_location()
        phases.guard_execution(self, state, args.id, args.scope)
        task = self.task(state, args.id)
        coordination.validate_claim_transfer(state, args.id, args.owner)
        require(
            task["status"] == "planned",
            "Claim requires planned state; release existing ownership first",
        )
        for dependency in strings(task["depends_on"], "depends_on"):
            require(
                self.task(state, dependency)["status"] == "done", f"Unmet dependency: {dependency}"
            )
        attempt_number = len(sequence(task["attempts"], "attempts")) + 1
        scope = sorted(set(safe_relative(path) for path in args.scope))
        require(len(scope) == len(args.scope), "Duplicate scope")
        baseline = self.source_snapshot(scope)
        head = observed_head
        require(bool(re.fullmatch(r"[a-f0-9]{40}", head)), "Invalid observed current HEAD")
        validation, evidence = self.frozen_json(args.id, attempt_number, args.revalidation)
        require(
            validation.get("task_id") == args.id and validation.get("attempt") == attempt_number,
            "Revalidation identity mismatch",
        )
        require(
            validation.get("head") == head and validation.get("source_digest") == digest(baseline),
            "Stale revalidation: HEAD/scoped source changed",
        )
        require(
            sorted(strings(validation.get("scope"), "revalidation scope")) == scope,
            "Revalidation scope mismatch",
        )
        require(
            validation.get("acceptance_reviewed") is True,
            "Revalidation must review the complete acceptance scope",
        )
        string(validation.get("summary"), "revalidation summary")
        evidence += self.evidence(
            args.id, attempt_number, strings(validation.get("evidence"), "revalidation evidence")
        )
        for value in sequence(state["tasks"], "tasks"):
            other = record(value, "task")
            if other["status"] in OWNED_STATES:
                require(other["owner"] != args.owner, "Owner already has an active task")
                proposal = {**task, "status": "active", "attempts": [{"scope": scope}]}
                require(
                    not self.collisions(proposal, other),
                    f"Scope/conflict collision with {other['id']}",
                )
        attempt: Record = {
            "number": attempt_number,
            "owner": args.owner,
            "scope": scope,
            "claimed_head": head,
            "baseline": baseline,
            "baseline_digest": digest(baseline),
            "status": "active",
            "evidence": evidence,
            "submission": None,
            "rounds": [],
            "review": None,
            "next_action": args.next_action,
        }
        attempt["assignment"] = coordination.assignment_metadata(state, args.owner)
        attempt["communication_language"] = communication_language(
            state.get("communication_language")
        )
        sequence(task["attempts"], "attempts").append(attempt)
        task.update({"status": "active", "owner": args.owner, "next_action": args.next_action})
        self.event(
            state,
            "claim",
            {
                "id": args.id,
                "actor": args.owner,
                "previous_state": "planned",
                "new_state": "active",
                "reason": "Current source revalidated",
                "attempt": attempt_number,
                "scope": scope,
                "source_digest": digest(baseline),
                "evidence": evidence,
            },
        )

        return state

    def transition(self, state: Record, args: argparse.Namespace) -> None:
        self.require_current_location()
        self.verify_refs(coordination.evidence_references(state))
        task = self.task(state, args.id)
        require(
            task["status"] != "done" or args.command in {"note", "reopen"},
            "Completed task cannot be reassigned",
        )
        previous = task["status"]
        if args.command == "reopen":
            require(previous == "done", "Reopen requires reviewed completion")
            coordination.require_task_authority(state, args.owner)
            task["status"] = "planned"
        if previous in OWNED_STATES:
            coordination.require_task_authority(state, args.owner, string(task["owner"], "owner"))
        block_origin: Record | None = None
        if args.command == "block":
            block_origin = (
                self.block_binding(state, task)
                if previous == "blocked"
                else {
                    "origin": previous,
                    "origin_event_index": len(sequence(state["events"], "events")),
                }
            )
        if args.command in {"block", "release"}:
            require(previous in OWNED_STATES, "Block/release requires an owned task")
            attempt = self.current_attempt(task)
            new_status = "blocked" if args.command == "block" else "planned"
            attempt.update(
                {
                    "status": "blocked" if args.command == "block" else "released",
                    "next_action": args.next_action,
                }
            )
            if args.command == "release":
                attempt["revalidation_stale"] = True
                task["owner"] = None
            task["status"] = new_status
        task["next_action"] = args.next_action
        note: Record = {
            "actor": args.owner,
            "reason": args.reason,
            "next_action": args.next_action,
            "timestamp": utc(),
        }
        if args.evidence:
            attempt = self.current_attempt(task)
            number = attempt["number"]
            require(type(number) is int, "Invalid attempt number")
            refs = self.evidence(args.id, number, args.evidence)
            self.verify_refs(sequence(attempt["evidence"], "evidence"))
            attempt["evidence"] = sequence(attempt["evidence"], "evidence") + refs
            note["evidence"] = refs
        sequence(task["notes"], "notes").append(note)
        self.event(
            state,
            args.command,
            {
                "id": args.id,
                "actor": args.owner,
                "previous_state": previous,
                "new_state": task["status"],
                "reason": args.reason,
                "next_action": args.next_action,
                **(
                    {"attempt": self.current_attempt(task)["number"]}
                    if previous in OWNED_STATES
                    else {}
                ),
            },
        )
        if block_origin is not None:
            event_index = len(sequence(state["events"], "events")) - 1
            sequence(self.current_attempt(task).setdefault("blocks", []), "blocks").append(
                {
                    "origin": block_origin["origin"],
                    "origin_event_index": block_origin["origin_event_index"],
                    "event_index": event_index,
                    "event_digest": digest(sequence(state["events"], "events")[event_index]),
                }
            )

    def block_binding(self, state: Record, task: Record) -> Record:
        """Prove block origin from the exact current-attempt event chain, including legacy data."""
        attempt = self.current_attempt(task)
        events = sequence(state["events"], "events")
        claims = [
            index
            for index, value in enumerate(events)
            if record(value, "event").get("action") == "claim"
            and record(value, "event").get("id") == task["id"]
            and canonical(record(value, "event").get("attempt")) == canonical(attempt["number"])
        ]
        recovery = "Ambiguous block origin; use supported release and fresh claim"
        require(len(claims) == 1, recovery)
        claim = record(events[claims[0]], "claim event")
        require(
            claim.get("actor") == attempt["owner"] == task["owner"]
            and claim.get("previous_state") == "planned"
            and claim.get("new_state") == "active",
            recovery,
        )
        if "scope" in claim:
            claimed_scope = strings(claim["scope"], "original claim scope")
        else:
            claim_refs = sequence(claim.get("evidence"), "original claim evidence")
            require(bool(claim_refs), "Legacy claim scope unavailable; release and fresh claim")
            self.verify_refs(claim_refs)
            validation = read_json(
                self.path(
                    string(record(claim_refs[0], "claim reference")["path"], "claim evidence path")
                )
            )
            require(
                canonical(validation.get("attempt")) == canonical(attempt["number"])
                and validation.get("task_id") == task["id"]
                and validation.get("source_digest") == attempt["baseline_digest"]
                and validation.get("head") == attempt["claimed_head"],
                "Legacy claim scope proof is ambiguous; release and fresh claim",
            )
            claimed_scope = strings(validation.get("scope"), "original claim scope")
        require(
            canonical(claimed_scope) == canonical(strings(attempt["scope"], "scope")),
            "Claimed scope changed; release and fresh claim",
        )
        status: Json = "active"
        origin: Json = None
        first: Json = None
        latest: int | None = None
        for index in range(claims[0] + 1, len(events)):
            event = record(events[index], "event")
            if event.get("id") != task["id"] or event.get("previous_state") is None:
                continue
            require(event.get("previous_state") == status, recovery)
            if "attempt" in event:
                require(canonical(event["attempt"]) == canonical(attempt["number"]), recovery)
            if event.get("action") == "block":
                require(
                    status in {"active", "review", "blocked"}
                    and event.get("new_state") == "blocked",
                    recovery,
                )
                if status != "blocked":
                    origin, first = status, index
                latest = index
            status = string(event.get("new_state"), "event state")
            require(status in STATES, recovery)
        require(
            status == task["status"] == "blocked"
            and origin in {"active", "review"}
            and latest is not None,
            recovery,
        )
        binding: Record = {
            "origin": origin,
            "origin_event_index": first,
            "block": {"event_index": latest, "event_digest": digest(events[latest])},
        }
        if "blocks" in attempt:
            blocks = sequence(attempt["blocks"], "blocks")
            require(bool(blocks), recovery)
            stored = record(blocks[-1], "block observation")
            require(
                canonical(stored)
                == canonical(
                    {
                        **record(binding["block"], "block"),
                        "origin": origin,
                        "origin_event_index": first,
                    }
                ),
                recovery,
            )
        return binding

    def validate_resolution_history(
        self, state: Record, task_id: str, attempt: Record, budget: ReadBudget | None
    ) -> None:
        """Check recorded observations against retained events without present-day authority replay."""
        events = sequence(state.get("events"), "events")
        for value in sequence(attempt.get("blocks", []), "blocks"):
            block = record(value, "block observation")
            index, first = block.get("event_index"), block.get("origin_event_index")
            require(
                type(index) is int and type(first) is int and 0 <= first <= index < len(events),
                "Invalid block event identity",
            )
            event, original = (
                record(events[index], "block event"),
                record(events[first], "origin event"),
            )
            require(
                event.get("id") == original.get("id") == task_id
                and event.get("action") == original.get("action") == "block"
                and event.get("new_state") == "blocked"
                and string(block.get("origin"), "block origin") in {"active", "review"}
                and original.get("previous_state") == block["origin"]
                and block.get("event_digest") == digest(event),
                "Block observation differs from original events",
            )
        resolutions = [
            record(value, "resolution")
            for value in sequence(attempt.get("resolutions", []), "resolutions")
        ]
        require(
            len(resolutions)
            == len({string(value.get("id"), "resolution ID") for value in resolutions}),
            "Duplicate resolution history ID",
        )
        for resolution in resolutions:
            binding = record(resolution.get("binding"), "resolution binding")
            require(
                canonical(binding.get("attempt")) == canonical(attempt["number"])
                and binding.get("task_id") == task_id
                and binding.get("owner") == attempt["owner"]
                and binding.get("program_id") == state["program_id"]
                and string(resolution.get("destination"), "resolution destination")
                in {"active", "review"},
                "Historical resolution identity changed",
            )
            block = record(binding.get("block"), "resolution block")
            index = block.get("event_index")
            require(
                type(index) is int
                and 0 <= index < len(events)
                and block.get("event_digest") == digest(events[index]),
                "Historical resolution block changed",
            )
            require(
                record(resolution.get("receipt"), "resolution receipt")
                in sequence(attempt["evidence"], "evidence"),
                "Resolution receipt lacks frozen attempt evidence",
            )
            string(resolution.get("reason"), "resolution reason")
            string(resolution.get("actor"), "resolution actor")
            index = resolution.get("event_index")
            require(
                type(index) is int and 0 <= index < len(events), "Invalid resolution event identity"
            )
            event = record(events[index], "resolution event")
            require(
                resolution.get("event_digest") == digest(event)
                and event.get("action") == "resume"
                and event.get("id") == task_id
                and canonical(event.get("attempt")) == canonical(attempt["number"])
                and event.get("new_state") == resolution["destination"]
                and canonical(event.get("receipt")) == canonical(resolution["receipt"]),
                "Historical resolution event changed",
            )
            if budget is not None:
                ref = record(resolution["receipt"], "resolution receipt")
                receipt = read_json(
                    self.path(string(ref["path"], "resolution path")), budget=budget
                )
                for key, value in {
                    **binding,
                    "id": resolution["id"],
                    "actor_id": resolution["actor"],
                    "reason": resolution["reason"],
                    "next_action": resolution["next_action"],
                }.items():
                    require(
                        canonical(receipt.get(key)) == canonical(value),
                        "Historical resolution receipt binding changed",
                    )

    def resume_binding(self, state: Record, task_id: str) -> Record:
        """Read-only present facts for an authored resolution, never a readiness approval."""
        return self.prepare_resume_binding(state, task_id, observed_head=self.current_head())

    def prepare_resume_binding(self, state: Record, task_id: str, *, observed_head: str) -> Record:
        """Bind resolution facts to explicit metadata without observing Git."""
        require(bool(re.fullmatch(r"[a-f0-9]{40}", observed_head)), "Invalid observed current HEAD")
        self.require_current_location()
        task = self.task(state, task_id)
        require(task["status"] == "blocked", "Resume requires current blocked ownership")
        attempt = self.current_attempt(task)
        origin = self.block_binding(state, task)
        scope = strings(attempt["scope"], "scope")
        return {
            "program_id": state["program_id"],
            "task_id": task_id,
            "owner": task["owner"],
            "attempt": attempt["number"],
            "expected_revision": phases.revision(state),
            "definition_digest": digest(
                {
                    key: value
                    for key, value in task.items()
                    if key not in phases.TASK_PROGRESS_FIELDS
                }
            ),
            "block": origin["block"],
            "origin": origin["origin"],
            "scope": scope,
            "head": observed_head,
            "source_digest": self.source_digest(scope),
        }

    def resume(self, state: Record, args: argparse.Namespace) -> None:
        """Commit one immutable same-attempt resolution after two fresh prerequisite checks."""
        prepared = self._prepare_resume(state, args, self.resume_binding)
        self.save(prepared.state, before_publish=prepared.before_publish)
        state.clear()
        state.update(prepared.state)

    def prepare_resume(
        self, state: Record, args: argparse.Namespace, *, observed_head: str
    ) -> PreparedChange:
        """Prepare with explicit Git metadata, including a Git-free final prerequisite check."""
        return self._prepare_resume(
            state,
            args,
            lambda current, task_id: self.prepare_resume_binding(
                current, task_id, observed_head=observed_head
            ),
        )

    def _prepare_resume(
        self, state: Record, args: argparse.Namespace, binding: Callable[[Record, str], Record]
    ) -> PreparedChange:
        """Reuse actual resolution validation and the publication recheck without writing."""
        self.require_current_location()
        self.validate(state)
        task = self.task(state, args.id)
        attempt = self.current_attempt(task)
        number = attempt["number"]
        require(type(number) is int and number > 0, "Invalid current attempt")
        receipt, refs = self.frozen_json(args.id, number, args.receipt)
        require(
            type(receipt.get("schema_version")) is int
            and receipt["schema_version"] == 1
            and receipt.get("operation") == "resume",
            "Invalid resume receipt schema/operation",
        )
        identifier = string(receipt.get("id"), "resolution ID")
        require(portable_task_id(identifier), "Invalid resolution ID")
        require(
            not any(
                record(value, "resolution").get("id") == identifier
                for value in sequence(attempt.get("resolutions", []), "resolutions")
            ),
            "Resolution already recorded; inspect immutable history",
        )
        reason = string(receipt.get("reason"), "resolution reason")
        next_action = string(receipt.get("next_action"), "resolution next action")
        require(receipt.get("actor_id") == args.owner, "Resolution acting identity mismatch")
        refs += self.evidence(
            args.id, number, strings(receipt.get("evidence"), "resolution evidence")
        )
        expected = binding(state, args.id)

        def prerequisites(current: Record) -> None:
            self.require_current_location()
            current_task = self.task(current, args.id)
            current_attempt = self.current_attempt(current_task)
            require(current_task["owner"] == current_attempt["owner"], "Stale current owner")
            coordination.require_task_authority(
                current, args.owner, string(current_task["owner"], "owner")
            )
            coordination.require_task_authority(
                current,
                string(current_task["owner"], "owner"),
                string(current_task["owner"], "owner"),
            )
            coordination.require_resume_transfer(
                current, args.id, number, strings(current_attempt["scope"], "scope")
            )
            for key, value in binding(current, args.id).items():
                require(
                    canonical(receipt.get(key)) == canonical(value), f"Stale resume binding: {key}"
                )
            phases.guard_execution(
                self, current, args.id, strings(current_attempt["scope"], "scope")
            )
            for dependency in strings(current_task["depends_on"], "dependencies"):
                require(
                    self.task(current, dependency)["status"] == "done",
                    f"Unmet dependency: {dependency}",
                )
            for value in sequence(current["tasks"], "tasks"):
                other = record(value, "task")
                if other["id"] != args.id and other["status"] in OWNED_STATES:
                    require(
                        other["owner"] != current_task["owner"]
                        and not self.collisions(current_task, other),
                        f"Resume ownership/scope collision with {other['id']}",
                    )
            self.verify_refs(sequence(current_attempt["evidence"], "evidence") + refs)

        prerequisites(state)
        destination = "active"
        if expected["origin"] == "review":
            submission = record(attempt.get("submission"), "blocked review submission")
            require(
                digest(record(submission.get("source_snapshot"), "submitted snapshot"))
                == submission.get("source_digest"),
                "Blocked submission snapshot binding changed",
            )
            require(
                bool(sequence(attempt["rounds"], "rounds"))
                and canonical(sequence(attempt["rounds"], "rounds")[-1]) == canonical(submission),
                "Blocked submission differs from immutable current round",
            )
            self.verify_refs(sequence(submission.get("evidence"), "submission evidence"))
            destination = (
                "review" if expected["source_digest"] == submission["source_digest"] else "active"
            )
        if "destination" in receipt:
            require(
                receipt["destination"] == destination,
                "Destination is derived from authoritative origin/source",
            )
        proposed = deepcopy(state)
        resumed_task = self.task(proposed, args.id)
        resumed_attempt = self.current_attempt(resumed_task)
        if expected["origin"] == "review" and destination == "active":
            resumed_attempt["submission"] = None
        resumed_attempt.update({"status": destination, "next_action": next_action})
        resumed_task.update({"status": destination, "next_action": next_action})
        resumed_attempt["evidence"] = sequence(resumed_attempt["evidence"], "evidence") + refs
        sequence(resumed_attempt.setdefault("resolutions", []), "resolutions").append(
            {
                "id": identifier,
                "actor": args.owner,
                "reason": reason,
                "next_action": next_action,
                "binding": expected,
                "destination": destination,
                "receipt": refs[0],
                "evidence": refs,
                "recorded_at": utc(),
            }
        )
        sequence(resumed_task["notes"], "notes").append(
            {
                "actor": args.owner,
                "reason": reason,
                "next_action": next_action,
                "timestamp": utc(),
                "evidence": refs,
            }
        )
        self.event(
            proposed,
            "resume",
            {
                "id": args.id,
                "actor": args.owner,
                "attempt": number,
                "previous_state": "blocked",
                "new_state": destination,
                "reason": reason,
                "next_action": next_action,
                "receipt": refs[0],
                "block": expected["block"],
            },
        )
        resolution_event = len(sequence(proposed["events"], "events")) - 1
        record(sequence(resumed_attempt["resolutions"], "resolutions")[-1], "resolution").update(
            {
                "event_index": resolution_event,
                "event_digest": digest(sequence(proposed["events"], "events")[resolution_event]),
            }
        )

        def precommit() -> None:
            current = self.load()
            require(
                phases.revision(current) == expected["expected_revision"],
                "Stale resume registry revision at publication",
            )
            prerequisites(current)

        return PreparedChange(proposed, precommit)

    def submit(self, state: Record, args: argparse.Namespace) -> None:
        proposal = self.prepare_submit(state, args, observed_head=self.current_head())
        state.clear()
        state.update(proposal)

    def prepare_submit(
        self, state: Record, args: argparse.Namespace, *, observed_head: str
    ) -> Record:
        """Validate a real submission on detached state without locks, files or publication."""
        state = deepcopy(state)
        require(bool(re.fullmatch(r"[a-f0-9]{40}", observed_head)), "Invalid observed current HEAD")
        self.require_current_location()
        task = self.task(state, args.id)
        require(
            task["status"] == "active" and task["owner"] == args.owner,
            "Submit requires active owner",
        )
        attempt = self.current_attempt(task)
        phases.guard_execution(self, state, args.id, strings(attempt["scope"], "scope"))
        number = attempt["number"]
        require(type(number) is int, "Invalid attempt number")
        self.verify_refs(sequence(attempt["evidence"], "evidence"))
        receipt, receipt_refs = self.frozen_json(args.id, number, args.receipt)
        coverage.validate_receipt_refs(self, state, receipt, args.id)
        require(
            receipt.get("task_id") == args.id and receipt.get("attempt") == number,
            "Receipt identity mismatch",
        )
        string(receipt.get("summary"), "receipt summary")
        string(receipt.get("next_action"), "receipt next_action")
        strings(receipt.get("limitations"), "limitations")
        require(
            receipt.get("acceptance_complete") is True
            and receipt.get("unresolved_acceptance") == []
            and receipt.get("unresolved_blockers") == [],
            "Unresolved acceptance/blockers prevent submission",
        )
        snapshot = self.source_snapshot(strings(attempt["scope"], "scope"))
        baseline = record(attempt["baseline"], "baseline")
        actual_changed = self.changed_paths(baseline, snapshot)
        changed = sorted(strings(receipt.get("changed_paths"), "changed_paths"))
        require(
            changed == actual_changed,
            "Receipt changed_paths must match every created/changed/deleted scoped file",
        )
        for path in changed:
            safe_path(self.repo, path)
        refs = list(receipt_refs)
        passed: set[str] = set()
        for value in sequence(receipt.get("checks"), "checks"):
            check = record(value, "check")
            check_id = string(check.get("id"), "check ID")
            require(
                check_id in check_policy(state)[0] and check_id not in passed,
                "Unknown/duplicate check ID",
            )
            require(check.get("status") == "passed", f"Check did not pass: {check_id}")
            string(check.get("command"), "executed command")
            kind = string(check.get("execution_kind"), "execution_kind")
            require(
                kind in {"native", "test", "static", "manual", "compile"}, "Invalid execution kind"
            )
            if check_id in {"native_editor", "native_player"}:
                require(kind == "native", "Compilation cannot replace native execution")
            refs += self.evidence(args.id, number, strings(check.get("evidence"), "check evidence"))
            passed.add(check_id)
        require(
            set(strings(task["required_checks"], "required_checks")) <= passed,
            "Missing required passing checks",
        )
        acceptance = sequence(receipt.get("acceptance_evidence"), "acceptance_evidence")
        require(bool(acceptance), "Acceptance evidence is required")
        for value in acceptance:
            criterion = record(value, "acceptance criterion")
            string(criterion.get("criterion"), "acceptance criterion")
            require(criterion.get("status") == "passed", "Acceptance criterion not passed")
            refs += self.evidence(
                args.id, number, strings(criterion.get("evidence"), "acceptance evidence")
            )
        submission: Record = {
            "receipt": receipt_refs[0],
            "source_digest": digest(snapshot),
            "source_snapshot": snapshot,
            "evidence": refs,
            "submitted_head": observed_head,
            "round": len(sequence(attempt["rounds"], "rounds")) + 1,
            "review": None,
        }
        attempt["submission"] = submission
        sequence(attempt["rounds"], "rounds").append(submission)
        attempt["evidence"] = sequence(attempt["evidence"], "evidence") + refs
        attempt["status"] = "review"
        task.update(
            {
                "status": "review",
                "next_action": "Review the frozen receipt, complete acceptance scope, evidence and source digest.",
            }
        )
        self.event(
            state,
            "submit",
            {
                "id": args.id,
                "actor": args.owner,
                "previous_state": "active",
                "new_state": "review",
                "reason": receipt["summary"],
                "source_digest": digest(snapshot),
                "receipt": receipt_refs[0],
            },
        )
        return state

    def verify_submission(self, attempt: Record) -> Record:
        submission = record(attempt.get("submission"), "submission")
        self.verify_refs(sequence(attempt["evidence"], "evidence"))
        snapshot = self.source_snapshot(strings(attempt["scope"], "scope"))
        require(
            digest(snapshot) == submission.get("source_digest"),
            "Submitted source changed; release and revalidate a new attempt",
        )
        return submission

    @staticmethod
    def changed_paths(baseline: Record, snapshot: Record) -> list[str]:
        return sorted(
            path
            for path in set(snapshot) | set(baseline)
            if snapshot.get(path) != baseline.get(path)
            and not path.endswith("/")
            and any(
                isinstance(value, str) and re.fullmatch(r"[a-f0-9]{64}", value)
                for value in (snapshot.get(path), baseline.get(path))
            )
        )

    def review(self, state: Record, args: argparse.Namespace) -> None:
        observed_head = self.current_head() if args.command == "complete" else None
        proposal = self.prepare_review(state, args, observed_head=observed_head)
        state.clear()
        state.update(proposal)

    def prepare_review(
        self, state: Record, args: argparse.Namespace, *, observed_head: str | None
    ) -> Record:
        """Prepare actual review validation without publishing or changing caller state."""
        state = deepcopy(state)
        self.require_current_location()
        self.verify_refs(coordination.evidence_references(state))
        task = self.task(state, args.id)
        require(task["status"] == "review", "Complete requires submitted review state")
        attempt = self.current_attempt(task)
        submission = self.verify_submission(attempt)
        number = attempt["number"]
        require(type(number) is int, "Invalid attempt number")
        review, refs = self.frozen_json(args.id, number, args.review)
        coverage.validate_receipt_refs(self, state, review, args.id)
        require(
            review.get("task_id") == args.id and review.get("attempt") == number,
            "Review identity mismatch",
        )
        verdict = "accepted" if args.command == "complete" else "rejected"
        require(review.get("verdict") == verdict, f"Review must have verdict={verdict}")
        require(
            review.get("receipt_sha256") == record(submission["receipt"], "receipt")["sha256"]
            and review.get("source_digest") == submission["source_digest"],
            "Review must bind the exact submitted receipt/source",
        )
        if verdict == "accepted":
            require(
                isinstance(observed_head, str)
                and bool(re.fullmatch(r"[a-f0-9]{40}", observed_head)),
                "Invalid observed current HEAD",
            )
            require(
                review.get("acceptance_complete") is True
                and review.get("unresolved_findings") == [],
                "Review acceptance remains unresolved",
            )
        else:
            require(
                bool(strings(review.get("unresolved_findings"), "unresolved_findings")),
                "Rejected review requires actionable findings",
            )
            string(review.get("next_action"), "review next_action")
        reviewer = string(review.get("reviewer"), "reviewer")
        role = string(review.get("reviewer_role"), "reviewer_role")
        require(role in {"independent", "leader"}, "Invalid reviewer role")
        if role == "leader" or reviewer == attempt["owner"]:
            string(review.get("reason"), "leader/self-review reason")
            require(role == "leader", "Self-review must be honestly labeled leader review")
            coordination.require_task_authority(state, reviewer)
        refs += self.evidence(args.id, number, strings(review.get("evidence"), "review evidence"))
        attempt["evidence"] = sequence(attempt["evidence"], "evidence") + refs
        submission["review"] = refs[0]
        sequence(attempt["rounds"], "rounds")[-1] = submission
        if verdict == "accepted":
            attempt.update(
                {
                    "status": "done",
                    "review": refs[0],
                    "completed_head": observed_head,
                }
            )
            task.update(
                {
                    "status": "done",
                    "owner": None,
                    "next_action": "Reviewed complete; delivery/CI is tracked separately.",
                }
            )
        else:
            attempt.update({"status": "active", "submission": None})
            task.update({"status": "active", "next_action": review["next_action"]})
        self.event(
            state,
            args.command,
            {
                "id": args.id,
                "actor": reviewer,
                "previous_state": "review",
                "new_state": task["status"],
                "reason": review.get("reason", f"{verdict} independent review"),
                "review": refs[0],
                "source_digest": submission["source_digest"],
            },
        )
        return state

    def validate_delivery_metadata(
        self, task: Record, attempt: Record, receipt: Record, *, budget: ReadBudget | None = None
    ) -> str:
        require(
            receipt.get("task_id") == task["id"] and receipt.get("attempt") == attempt["number"],
            "Delivery identity mismatch",
        )
        for key in ("branch", "base", "pr_url", "tested_commit", "merge_status"):
            string(receipt.get(key), key)
        require(
            receipt.get("reviewed_source_digest")
            == record(attempt["submission"], "submission")["source_digest"],
            "Delivery must identify the exact reviewed source digest",
        )
        commits = strings(receipt.get("commits"), "commits")
        require(
            bool(commits)
            and receipt["tested_commit"] in commits
            and all(re.fullmatch(r"[a-f0-9]{40}", commit) for commit in commits),
            "Delivery must name exact tested commit among full commit SHAs",
        )
        ci = sequence(receipt.get("ci"), "ci")
        require(bool(ci), "Delivery requires explicit CI run/job records")
        required_ci = strings(receipt.get("required_ci"), "required_ci")
        require(bool(required_ci), "Name every required CI check")
        conclusions = []
        check_names = []
        for value in ci:
            check = record(value, "CI result")
            for key in ("url", "name", "conclusion", "commit"):
                string(check.get(key), key)
            require(
                check["commit"] == receipt["tested_commit"],
                "CI must test the exact delivery commit",
            )
            conclusions.append(check["conclusion"])
            check_names.append(check["name"])
        require(
            len(check_names) == len(set(check_names)) and set(required_ci) == set(check_names),
            "CI receipt must cover exactly the required unique checks",
        )
        status = (
            "ci_recorded_success"
            if all(value == "success" for value in conclusions)
            else "ci_pending"
            if any(value in {"pending", "in_progress", "queued"} for value in conclusions)
            else "ci_failed"
        )
        authoring.validate_cleanup(self, task, attempt, receipt)
        if receipt.get("provider_observation") is not None:
            provider = authoring.provider_observation(self, receipt["provider_observation"], budget)
            require(
                all(
                    canonical(receipt.get(key)) == canonical(provider.get(key))
                    for key in authoring.PROVIDER_FIELDS
                ),
                "Delivery facts differ from frozen provider observation",
            )
        return status

    def delivery(self, state: Record, args: argparse.Namespace) -> None:
        task = self.task(state, args.id)
        require(task["status"] == "done", "Delivery metadata requires reviewed completion")
        attempt = self.current_attempt(task)
        number = attempt["number"]
        require(type(number) is int, "Invalid attempt number")
        self.verify_refs(sequence(attempt["evidence"], "evidence"))
        receipt, refs = self.frozen_json(args.id, number, args.receipt)
        status = self.validate_delivery_metadata(task, attempt, receipt)
        metadata = {
            "status": status,
            "remote_verified_by_tool": False,
            "receipt": refs[0],
            "metadata": receipt,
        }
        refs += self.evidence(
            args.id, number, strings(receipt.get("evidence"), "delivery evidence")
        )
        if receipt.get("cleanup") is not None:
            refs += self.evidence(
                args.id,
                number,
                strings(record(receipt["cleanup"], "cleanup")["evidence"], "cleanup evidence"),
            )
        deliveries = sequence(task.setdefault("deliveries", []), "deliveries")
        deliveries.append(metadata)
        attempt["evidence"] = sequence(attempt["evidence"], "evidence") + refs
        self.event(
            state,
            "delivery",
            {
                "id": args.id,
                "actor": "leader",
                "previous_state": "done",
                "new_state": "done",
                "reason": status,
                "receipt": refs[0],
            },
        )

    def prepare_language(self, state: Record, args: argparse.Namespace) -> Record:
        state = deepcopy(state)
        value = communication_language(args.value)
        director = coordination.current_director_id(state)
        require(
            args.owner == (director or "leader"),
            "Language changes require the current director (or legacy leader)",
        )
        previous = state.get("communication_language")
        state["communication_language"] = value
        self.event(
            state,
            "language",
            {
                "actor": args.owner,
                "previous_language": previous,
                "communication_language": value,
                "reason": args.reason,
            },
        )
        return state

    def checkpoint(self, state: Record, args: argparse.Namespace) -> None:
        dirty = git(self.repo, ["status", "--porcelain=v1", "-z", "--untracked-files=all"])
        proposal = self.prepare_checkpoint(
            state, args, observed_head=self.current_head(), observed_dirty=dirty
        )
        state.clear()
        state.update(proposal)

    def prepare_checkpoint(
        self, state: Record, args: argparse.Namespace, *, observed_head: str, observed_dirty: str
    ) -> Record:
        """Describe a checkpoint from explicitly observed metadata without creating it."""
        state = deepcopy(state)
        dirty = observed_dirty
        paths = []
        for entry in dirty.split("\0"):
            name = entry[3:] if len(entry) > 3 and entry[2] == " " else entry
            if name:
                try:
                    paths.append(safe_relative(name))
                except ProgramError:
                    continue
        state["checkpoint"] = {
            "timestamp": utc(),
            "head": observed_head,
            "dirty_paths": sorted(set(paths)),
            "note": args.note,
            "next_action": args.next_action,
            "team_state": args.team_state,
        }
        self.event(
            state,
            "checkpoint",
            {
                "actor": "leader",
                "previous_state": None,
                "new_state": None,
                "reason": args.note,
                "next_action": args.next_action,
            },
        )

        return state

    def packet(self, state: Record, task: Record) -> str:
        source = planning.source_for_task(state, task)
        section = record(task["source"], "section")
        attempt = self.current_attempt(task) if sequence(task["attempts"], "attempts") else None
        metadata = {
            key: task[key]
            for key in (
                "id",
                "workstream",
                "phase",
                "status",
                "owner",
                "depends_on",
                "related_to",
                "conflict_groups",
                "candidate_paths",
                "required_checks",
                "next_action",
                "attempts",
                "notes",
            )
        }
        metadata["deliveries"] = task.get("deliveries", [])
        metadata["planning_note"] = task.get("planning_note")
        metadata["execution_priority"] = record(
            state.get("execution_priorities", {}), "priorities"
        ).get(task["id"])
        metadata["planning_mutations"] = [
            value
            for value in sequence(state.get("amendments", []), "amendments")
            if record(value, "amendment").get("operation") in planning.PLANNED_COMMANDS
            and any(
                record(change, "change")["id"] == task["id"]
                for change in sequence(record(value, "amendment")["changes"], "changes")
            )
        ]
        metadata["communication_language"] = state.get("communication_language")
        return (
            f"# Task {task['id']}: {section['title']}\n\n"
            "This packet is sufficient to recover the assigned task without chat history.\n"
            "Read README.md, DESIGN.md, DECISIONS.md and CHECKPOINT.md in the program directory.\n"
            "Inspect actual Git state and native agent liveness; saved owners are not live-agent proof.\n"
            "Preserve other work. Revalidate current source before each new claim. Do not run Unity,\n"
            "install packages, deploy or publish unless the task/session authorizes that action.\n\n"
            "Use the recorded communication_language for user questions, options and explanations.\n"
            "A missing language is unknown: preserve explicit session language without inferring one.\n"
            "Change it only after an explicit human language change; preserve source and replies.\n\n"
            f"Repository: `{self.repo.as_posix()}`\nProgram: `{self.directory.relative_to(self.repo).as_posix()}`\n"
            f"Original source: `{source['path']}:{section['line']}`\n"
            f"Original relative evidence reference base: `{source['reference_base']}`\n"
            f"Reviewed baseline: `{source['review_baseline']}`\nSetup baseline: `{state['baseline_head']}`\n"
            f"Original section SHA-256: `{section['sha256']}`\n"
            f"Current attempt: `{attempt['number'] if attempt else 'none'}`\n\n"
            "## Durable assignment and history\n\n```json\n"
            + canonical(metadata).decode()
            + "```\n\n"
            "## Complete original reviewed section\n\n" + string(section["text"], "source text")
        )

    def views(self, state: Record) -> dict[str, str]:
        tasks = [record(value, "task") for value in sequence(state["tasks"], "tasks")]
        counts = Counter(string(task["status"], "status") for task in tasks)
        board = [
            f"# Work program: {state['program_id']}",
            "",
            "Derived from backlog.json; update state through tools/feature_program.py.",
            "",
            f"All {len(tasks)} reviewed items are retained. Planned work requires current-source revalidation.",
            "Done means reviewed implementation; delivery and remote CI remain separate recorded evidence.",
            "",
            "| State | Count |",
            "| --- | ---: |",
        ]
        board.extend(f"| {status} | {counts[status]} |" for status in sorted(STATES))
        board += [
            "",
            "| ID | Phase | Workstream | Status | Owner | Dependencies | Next action |",
            "| --- | ---: | --- | --- | --- | --- | --- |",
        ]
        for task in tasks:
            values = [
                f"[{task['id']}](tasks/{task['id']}.md)",
                task["phase"],
                task["workstream"],
                task["status"],
                task["owner"] or "-",
                ", ".join(strings(task["depends_on"], "depends_on")) or "-",
                task["next_action"],
            ]
            board.append(
                "| "
                + " | ".join(str(value).replace("|", "\\|").replace("\n", " ") for value in values)
                + " |"
            )
        checkpoint = state.get("checkpoint")
        lines = [
            "# Feature program checkpoint",
            "",
            "Derived from backlog.json. No recorded agent path proves liveness.",
            "",
        ]
        if checkpoint is None:
            lines += [
                "No leader checkpoint recorded yet.",
                "Next action: inspect status/check and revalidate an eligible task.",
            ]
        else:
            current = record(checkpoint, "checkpoint")
            lines += [
                f"Recorded: {current['timestamp']}",
                f"HEAD: `{current['head']}`",
                f"Note: {current['note']}",
                f"Next action: {current['next_action']}",
                f"Team state: `{current['team_state']}`",
                "",
                "Dirty paths (names only):",
            ]
            lines += [f"- `{name}`" for name in strings(current["dirty_paths"], "dirty_paths")] or [
                "- none"
            ]
        lines += ["", "## Active and blocked assignments", ""]
        for task in tasks:
            if task["status"] in OWNED_STATES:
                attempt = self.current_attempt(task)
                lines.append(
                    f"- {task['id']} ({task['status']}): {task['owner']}, attempt {attempt['number']}; {task['next_action']}; [packet](tasks/{task['id']}.md)"
                )
        result = {"BOARD.md": "\n".join(board) + "\n", "CHECKPOINT.md": "\n".join(lines) + "\n"}
        result.update({f"tasks/{task['id']}.md": self.packet(state, task) for task in tasks})
        return result

    def prepare_save(self, state: Record) -> bytes:
        """Share actual publication preflight without creating or changing any files."""
        self.require_current_location()
        self.validate(state)
        encoded = canonical(state)
        require(
            len(encoded) <= 8 * 1024 * 1024,
            "Canonical registry exceeds 8 MiB; existing state preserved",
        )
        derived = self.views(state)
        require(
            all(len(text.encode("utf-8")) <= 8 * 1024 * 1024 for text in derived.values()),
            "Derived document exceeds 8 MiB; existing state preserved",
        )
        for name in ("backlog.json", *derived):
            destination = self.path(name)
            require(
                not destination.exists() or destination.is_file(),
                f"Publication output is not a regular file: {name}",
            )
            for parent in destination.parents:
                require(
                    not parent.exists() or parent.is_dir(),
                    f"Publication output parent is not a directory: {name}",
                )
                if parent == self.directory:
                    break
        return encoded

    def save(self, state: Record, *, before_publish: Callable[[], None] | None = None) -> None:
        self.require_current_location()
        reliance_guard = coverage.publication_guard(self, state)
        encoded = self.prepare_save(state)
        if before_publish is not None:
            before_publish()
        if reliance_guard is not None:
            reliance_guard()
        atomic_write(self.path("backlog.json"), encoded)
        try:
            self.render(state)
        except (OSError, ProgramError) as exc:
            raise ProgramError(
                f"Registry committed at revision {sha(encoded)}; derived rendering failed. Run render to recover views: {exc}"
            ) from exc

    def render(self, state: Record) -> None:
        for name, text in self.views(state).items():
            atomic_write(self.path(name), text.encode("utf-8"))

    def status(self, state: Record, next_only: bool = False) -> Record:
        tasks = [record(value, "task") for value in sequence(state["tasks"], "tasks")]
        active = [task for task in tasks if task["status"] in OWNED_STATES]
        candidates: list[Json] = []
        for task in planning.task_order(state):
            if task["status"] != "planned" or task.get("disposition") in {"split", "retired"}:
                continue
            goal = state.get("objective")
            if (
                goal is not None
                and record(goal, "objective").get("membership_revision") is not None
                and task["id"] not in strings(record(goal, "objective")["task_ids"], "members")
            ):
                continue
            blocked = [
                dependency
                for dependency in strings(task["depends_on"], "depends_on")
                if self.task(state, dependency)["status"] != "done"
            ]
            collisions = [
                {"id": other["id"], "reasons": self.collisions(task, other)}
                for other in active
                if self.collisions(task, other)
            ]
            if next_only and (blocked or collisions):
                continue
            candidates.append(
                {
                    "id": task["id"],
                    "phase": task["phase"],
                    "execution_priority": record(
                        state.get("execution_priorities", {}), "priorities"
                    ).get(task["id"]),
                    "workstream": task["workstream"],
                    "unmet_dependencies": blocked,
                    "scope_conflicts": collisions,
                    "requires_revalidation": True,
                    "next_action": task["next_action"],
                }
            )
        return {
            "counts": dict(Counter(string(task["status"], "status") for task in tasks)),
            "owned": [
                {
                    "id": task["id"],
                    "owner": task["owner"],
                    "status": task["status"],
                    "attempt": self.current_attempt(task)["number"],
                    "next_action": task["next_action"],
                }
                for task in active
            ],
            "candidates": candidates,
            "delivery": [
                {
                    "id": task["id"],
                    "latest": self.current_delivery(task),
                }
                for task in tasks
                if task["status"] == "done"
            ],
        }

    def current_delivery(self, task: Record) -> Record:
        attempt = self.current_attempt(task)
        source_digest = record(attempt["submission"], "submission")["source_digest"]
        for value in reversed(sequence(task.get("deliveries", []), "deliveries")):
            delivery = record(value, "delivery")
            metadata = record(delivery["metadata"], "delivery metadata")
            if (
                metadata["attempt"] == attempt["number"]
                and metadata["reviewed_source_digest"] == source_digest
            ):
                return delivery
        return {"status": "unrecorded", "remote_verified_by_tool": False}

    def template(self, state: Record, args: argparse.Namespace) -> Record:
        head = self.current_head() if args.kind in {"revalidation", "delivery", "resume"} else None
        return self.prepare_template(state, args, observed_head=head)

    def prepare_template(
        self, state: Record, args: argparse.Namespace, *, observed_head: str | None = None
    ) -> Record:
        """Build the existing nonpassing template using explicit observations only."""
        task = self.task(state, args.id)
        attempts = sequence(task["attempts"], "attempts")
        number = len(attempts) + 1 if args.kind == "revalidation" else len(attempts)
        base: Record = {"task_id": args.id, "attempt": number}
        match args.kind:
            case "resume":
                return {
                    **self.prepare_resume_binding(state, args.id, observed_head=observed_head),
                    "schema_version": 1,
                    "operation": "resume",
                    "id": "replace-with-unique-resolution-id",
                    "actor_id": task["owner"],
                    "reason": "Describe actual blocker resolution",
                    "next_action": "Describe the next verified action",
                    "evidence": [],
                }
            case "revalidation":
                require(bool(args.scope), "Revalidation template requires explicit --scope")
                scope = sorted(set(safe_relative(path) for path in args.scope))
                snapshot = self.source_snapshot(scope)
                return {
                    **base,
                    "head": observed_head,
                    "scope": scope,
                    "source_digest": digest(snapshot),
                    "acceptance_reviewed": False,
                    "summary": "Describe current-code findings against complete original acceptance",
                    "evidence": [],
                }
            case "execution":
                require(task["status"] == "active", "Execution template requires active claim")
                attempt = self.current_attempt(task)
                snapshot = self.source_snapshot(strings(attempt["scope"], "scope"))
                return {
                    **base,
                    "summary": "Describe actual changes",
                    "changed_paths": self.changed_paths(
                        record(attempt["baseline"], "baseline"), snapshot
                    ),
                    "checks": [
                        {
                            "id": name,
                            "status": "not_run",
                            "command": "Replace with exact executed command",
                            "execution_kind": "native"
                            if name in {"native_editor", "native_player"}
                            else "test",
                            "evidence": [],
                        }
                        for name in strings(task["required_checks"], "required_checks")
                    ],
                    "acceptance_complete": False,
                    "acceptance_evidence": [
                        {
                            "criterion": "Compare complete original acceptance scope",
                            "status": "not_run",
                            "evidence": [],
                        }
                    ],
                    "unresolved_acceptance": ["Complete acceptance verification"],
                    "unresolved_blockers": [],
                    "limitations": [],
                    "next_action": "Request review after verification",
                }
            case "review":
                require(task["status"] == "review", "Review template requires submitted task")
                submission = record(self.current_attempt(task)["submission"], "submission")
                return {
                    **base,
                    "receipt_sha256": record(submission["receipt"], "receipt")["sha256"],
                    "source_digest": submission["source_digest"],
                    "verdict": "pending",
                    "reviewer": "Replace with reviewer identity",
                    "reviewer_role": "independent",
                    "reason": "Record reason for leader/self review",
                    "acceptance_complete": False,
                    "unresolved_findings": ["Review complete acceptance and native evidence"],
                    "evidence": [],
                    "next_action": "Address review findings or accept exact submission",
                }
            case "delivery":
                require(task["status"] == "done", "Delivery template requires reviewed completion")
                head = observed_head
                return {
                    **base,
                    "branch": "Record actual work branch",
                    "base": "beta",
                    "commits": [head],
                    "pr_url": "Record actual PR URL",
                    "tested_commit": head,
                    "reviewed_source_digest": record(
                        self.current_attempt(task)["submission"], "submission"
                    )["source_digest"],
                    "required_ci": ["Record required run/job"],
                    "ci": [
                        {
                            "name": "Record required run/job",
                            "url": "Record actual CI URL",
                            "commit": head,
                            "conclusion": "pending",
                        }
                    ],
                    "merge_status": "open",
                    "evidence": [],
                }
            case other:
                raise ProgramError(f"Unknown template: {other}")


def _delivery_history_projection(state: Record, revision: str) -> list[Record]:
    """Detach complete recorded deliveries; only exact unique events supply local time."""
    events: dict[tuple[str, bytes], list[tuple[int, Record]]] = {}
    for index, value in enumerate(sequence(state["events"], "events")):
        if not isinstance(value, dict) or value.get("action") != "delivery":
            continue
        reference = value.get("receipt")
        identifier = value.get("id")
        if (
            isinstance(identifier, str)
            and isinstance(reference, dict)
            and isinstance(reference.get("path"), str)
            and isinstance(reference.get("sha256"), str)
            and re.fullmatch(r"[a-f0-9]{64}", reference["sha256"])
        ):
            events.setdefault((identifier, canonical(reference)), []).append((index, value))
    histories: list[Record] = []
    for task_index, value in enumerate(sequence(state["tasks"], "tasks")):
        task = record(value, "task")
        items: list[Json] = []
        for index, value in enumerate(sequence(task.get("deliveries", []), "deliveries")):
            delivery = record(value, "delivery")
            reference = delivery.get("receipt")
            matches = (
                events.get((str(task["id"]), canonical(reference)), [])
                if isinstance(reference, dict)
                else []
            )
            timestamp: Json = None
            event_reference: Json = None
            if len(matches) == 1:
                event_index, event = matches[0]
                try:
                    coordination._timestamp(event.get("timestamp"), "delivery event timestamp")
                except coordination.GovernanceError:
                    pass
                else:
                    timestamp = event["timestamp"]
                    event_reference = {
                        "path": "backlog.json",
                        "pointer": f"/events/{event_index}",
                        "revision": revision,
                    }
            items.append(
                {
                    "record": deepcopy(delivery),
                    "reference": {
                        "path": "backlog.json",
                        "pointer": f"/tasks/{task_index}/deliveries/{index}",
                        "revision": revision,
                    },
                    "recorded_at": timestamp,
                    "event_reference": event_reference,
                    "time_status": "recorded" if timestamp is not None else "unknown",
                }
            )
        histories.append({"status": "recorded" if items else "unrecorded", "items": items})
    return histories


def read_snapshot(repo: Path, program_dir: Path) -> Record:
    return _read_projection(repo, program_dir)


def read_brief(repo: Path, program_dir: Path, task_id: str | None = None) -> Record:
    return _read_projection(repo, program_dir, brief_only=True, task_id=task_id)


def _read_projection(
    repo: Path, program_dir: Path, *, brief_only: bool = False, task_id: str | None = None
) -> Record:
    observations = _SourceObservations()
    token = _source_observations.set(observations)
    try:
        return _capture_projection(repo, program_dir, observations, brief_only, task_id)
    finally:
        _source_observations.reset(token)


def _capture_projection(
    repo: Path,
    program_dir: Path,
    source_observations: _SourceObservations,
    brief_only: bool,
    task_id: str | None,
) -> Record:
    """Return bounded recorded state for a local UI, without locks, Git or writes.

    Integrity warnings keep recorded context visible when artifacts drift. Structural
    errors reject the snapshot. No evidence contents or source file bytes are served.
    """
    repo = repo.absolute()
    directory = program_dir.absolute() if program_dir.is_absolute() else repo / program_dir
    require(
        directory.is_relative_to(repo) and directory != repo,
        "Program must be within configured repository",
    )
    program = Program(repo, safe_path(repo, directory.relative_to(repo).as_posix()))
    program.require_current_location()
    backlog = program.path("backlog.json")
    require(
        backlog.is_file() and backlog.stat().st_size <= 8 * 1024 * 1024,
        "Snapshot registry missing or exceeds 8 MiB",
    )
    initial = bounded_bytes(backlog, 8 * 1024 * 1024)
    registry_budget = ReadBudget(cached={backlog: initial})
    state = read_json(backlog, budget=registry_budget)
    require(
        sha(bounded_bytes(backlog, 8 * 1024 * 1024)) == sha(initial),
        "Registry changed during snapshot; refresh",
    )
    tasks = [record(value, "task") for value in sequence(state.get("tasks"), "tasks")]
    require(len(tasks) <= 2000, "Snapshot exceeds 2000 tasks")
    try:
        program.validate(state, verify_artifacts=False)
    except (
        coordination.GovernanceError,
        phases.PhaseError,
        planning.PlanningError,
        questions.QuestionError,
    ) as exc:
        raise ProgramError(str(exc)) from exc
    # Capture source identities separately from their recorded hashes: an already
    # drifted source must not change again while its advisory projection is built.
    source_inputs = ReadBudget(64 * 1024 * 1024)
    unavailable_sources: set[Path] = set()
    source_names = {str(item["path"]) for item in planning.descriptors(state)}
    if state.get("planning_path"):
        source_names.add(str(state["planning_path"]))
    source_names.update(str(item["planning"]["path"]) for item in planning.intakes(state).values())
    for name in sorted(source_names):
        path = safe_path(repo, name)
        if path.is_file():
            source_inputs.read(path, 8 * 1024 * 1024)
        else:
            unavailable_sources.add(path)
    try:
        warnings = planning.observe_sources(program, state)
    except planning.PlanningError as exc:
        raise ProgramError(str(exc)) from exc
    refs: dict[str, str] = {}
    observations: dict = {"submissions": {}, "blocks": {}, "recovery": [], "publication": []}
    publication_inputs = ReadBudget(64 * 1024 * 1024)
    absent_publication: set[Path] = set()
    for name in sorted(planning.PUBLICATION_MARKERS | {planning.TRACKING}):
        path = program.path(name)
        if path.is_file():
            raw = publication_inputs.read(path, 8 * 1024 * 1024)
            observations["publication"].append(
                {"path": name, "sha256": sha(raw), "status": "observed"}
            )
        else:
            absent_publication.add(path)
    review_sources = ReadBudget(128 * 1024 * 1024)
    all_refs = (
        list(coordination.evidence_references(state))
        + list(questions.evidence_references(state))
        + phases.evidence_references(state)
        + planning.evidence_references(state)
        + coverage.evidence_references(state)
        + authoring.delivery_evidence_references(state)
        + presentation.evidence_references(state)
    )
    for task in tasks:
        for value in sequence(task["attempts"], "attempts"):
            all_refs += sequence(record(value, "attempt")["evidence"], "evidence")
        review_origin = task["status"] == "review"
        if task["status"] == "blocked":
            try:
                binding = program.block_binding(state, task)
                observations["blocks"][task["id"]] = {"status": "observed", **binding}
                review_origin = binding["origin"] == "review"
            except (ProgramError, OSError) as exc:
                observations["blocks"][task["id"]] = {"status": "not_assessed", "reason": str(exc)}
        if review_origin:
            attempt = program.current_attempt(task)
            try:
                submission = record(attempt["submission"], "submission")
                scope = strings(attempt["scope"], "scope")
                snapshot = program.source_snapshot(scope, review_sources)
                if digest(snapshot) != submission["source_digest"]:
                    warnings.append(f"Pending submitted source changed: {task['id']}")
                    observations["submissions"][task["id"]] = {
                        "status": "stale",
                        "reason": "Pending submitted source changed",
                        "recorded_digest": submission["source_digest"],
                        "observed_digest": digest(snapshot),
                        "next_action": (
                            "Resolve the recorded block through resume; changed source requires "
                            "rework and a new submission."
                            if task["status"] == "blocked"
                            else "Rework and submit the changed source for a new review."
                        ),
                    }
                else:
                    observations["submissions"][task["id"]] = {
                        "status": "observed",
                        "recorded_digest": submission["source_digest"],
                        "observed_digest": digest(snapshot),
                    }
            except (ProgramError, OSError):
                warnings.append(f"Pending submitted source could not be verified: {task['id']}")
                observations["submissions"][task["id"]] = {
                    "status": "not_assessed",
                    "reason": "Pending submitted source could not be verified",
                }
    for value in all_refs:
        ref = record(value, "evidence reference")
        name = string(ref["path"], "evidence path")
        expected = string(ref["sha256"], "evidence hash")
        if name in refs and refs[name] != expected:
            warnings.append(f"Conflicting frozen evidence hashes: {name}")
        refs[name] = expected
    require(len(refs) <= 2000, "Snapshot exceeds 2000 evidence files")
    evidence_budget = ReadBudget(64 * 1024 * 1024)
    unavailable_evidence: set[Path] = set()
    for name, expected in refs.items():
        path = program.path(name)
        if not path.is_file():
            warnings.append(f"Frozen evidence unavailable: {name}")
            unavailable_evidence.add(path)
            continue
        content = evidence_budget.read(path, 8 * 1024 * 1024)
        if sha(content) != expected:
            warnings.append(f"Frozen evidence changed: {name}")
    try:
        for name, text in program.views(state).items():
            path = program.path(name)
            if (
                not path.is_file()
                or path.stat().st_size > 8 * 1024 * 1024
                or bounded_bytes(path, 8 * 1024 * 1024) != text.encode("utf-8")
            ):
                warnings.append(f"Derived view drift: {name}; director may run render.")
    except OSError:
        warnings.append("Derived views unavailable; recorded registry remains readable.")
    result_tasks: list[Json] = []
    delivery_histories = _delivery_history_projection(state, sha(initial))
    for task_index, task in enumerate(tasks):
        section = record(task["source"], "source section")
        source = planning.source_for_task(state, task)
        item: Record = {
            key: task[key]
            for key in (
                "id",
                "workstream",
                "phase",
                "status",
                "owner",
                "depends_on",
                "related_to",
                "conflict_groups",
                "candidate_paths",
                "required_checks",
                "next_action",
                "notes",
            )
        }
        item.update(
            {
                "title": string(section["title"], "title"),
                "planning_note": task.get("planning_note", ""),
                "source": {
                    **section,
                    "path": source["path"],
                    "reference_base": source["reference_base"],
                    "review_baseline": source["review_baseline"],
                    "source_intake_id": task.get("source_intake_id"),
                },
                "delivery": program.current_delivery(task)
                if task["status"] == "done"
                else {"status": "unrecorded", "remote_verified_by_tool": False},
                "delivery_history": delivery_histories[task_index],
                "attempts": [],
            }
        )
        for value in sequence(task["attempts"], "attempts"):
            attempt = record(value, "attempt")
            history: Record = {
                key: content
                for key, content in attempt.items()
                if key not in {"baseline", "submission", "rounds"}
            }
            if "assignment" not in history:
                unknown = coordination.assignment_metadata(
                    {}, string(attempt["owner"], "attempt owner")
                )
                unknown.update({"actor_revision": None, "role": "unknown", "recorded_at": None})
                history["assignment"] = unknown
            rounds: list[Json] = []
            for round_value in sequence(attempt.get("rounds", []), "rounds"):
                submission = record(round_value, "round")
                summary: Record = {
                    key: content for key, content in submission.items() if key != "source_snapshot"
                }
                for kind in ("receipt", "review"):
                    artifact = submission.get(kind)
                    if artifact is None:
                        continue
                    ref = record(artifact, kind)
                    path = program.path(string(ref["path"], "artifact path"))
                    if path.is_file() and path.stat().st_size <= 1024 * 1024:
                        captured = evidence_budget.read(path, 1024 * 1024)
                        if sha(captured) == ref["sha256"]:
                            metadata, parsed_sha = read_json_frozen(path, budget=evidence_budget)
                            require(
                                parsed_sha == ref["sha256"], "Captured artifact digest mismatch"
                            )
                            summary[kind + "_record"] = metadata
                rounds.append(summary)
            history["rounds"] = rounds
            sequence(item["attempts"], "attempts").append(history)
        result_tasks.append(item)
    governance = coordination.snapshot_metadata(state)
    categories = sequence(governance.get("categories", []), "categories")
    for value in result_tasks:
        task = record(value, "task")
        task["category_id"] = next(
            (
                record(category, "category")["id"]
                for category in categories
                if task["id"]
                in sequence(record(category, "category").get("task_ids", []), "category task_ids")
            ),
            task["workstream"],
        )
    result: Record = {
        "snapshot_version": 1,
        "revision": sha(initial),
        "generated_at": utc(),
        "program_id": state["program_id"],
        "total_tasks": len(tasks),
        "counts": {
            status: sum(task["status"] == status for task in tasks) for status in sorted(STATES)
        },
        "workstreams": state["workstreams"],
        "communication_language": state.get("communication_language"),
        "check_catalog": sorted(check_policy(state)[0]),
        "minimum_checks": sorted(check_policy(state)[1]),
        "bounds": {
            "max_tasks": 2000,
            "max_evidence_files": 2000,
            "max_document_bytes": 8 * 1024 * 1024,
            "max_evidence_bytes": 64 * 1024 * 1024,
            "truncated": False,
        },
        "integrity": {
            "status": "stale" if warnings else "fresh",
            "warnings": sorted(set(warnings)),
        },
        "checkpoint": state.get("checkpoint"),
        "tasks": result_tasks,
        **governance,
        "questions": questions.snapshot_questions(state),
        "source_intakes": state.get("source_intakes", []),
        "amendments": state.get("amendments", []),
        "execution_priorities": state.get("execution_priorities"),
        "coverage": coverage.projection(program, state, evidence_budget),
        "presentation": presentation.projection(
            program, state, evidence_budget, revision=sha(initial)
        ),
    }
    lock = program.path(".feature-program.lock")
    lock_before: bytes | None = None
    lock_unavailable: str | None = None
    if lock.exists():
        try:
            lock_before = bounded_bytes(lock, 64 * 1024)
            read_json(lock, limit=64 * 1024, budget=ReadBudget(cached={lock: lock_before}))
            lock_status = "present"
        except (ProgramError, OSError) as exc:
            lock_status = "unavailable"
            lock_unavailable = str(exc)
        observations["recovery"].append(
            {
                "path": ".feature-program.lock",
                "status": lock_status,
                "reason": "Recorded lock exists; liveness and takeover are unknown"
                if lock_status == "present"
                else "Lock metadata unavailable: " + str(lock_unavailable),
            }
        )
    projection = brief.project(program, state, result, observations)
    result["brief"] = projection["program"]
    for item in result_tasks:
        record(item, "task")["readiness"] = projection["tasks"][record(item, "task")["id"]][
            "readiness"
        ]
    require(len(canonical(result)) <= 8 * 1024 * 1024, "Snapshot output exceeds 8 MiB")
    output = (
        (brief.selected(projection, task_id) if task_id is not None else projection["program"])
        if brief_only
        else result
    )
    for budget in (source_inputs, evidence_budget, publication_inputs):
        for path, captured in (budget.cached or {}).items():
            require(
                path.is_file() and bounded_bytes(path, 8 * 1024 * 1024) == captured,
                "Observed source/evidence changed during snapshot; refresh",
            )
    require(
        not any(
            path.is_file()
            for path in unavailable_sources | unavailable_evidence | absent_publication
        ),
        "Unavailable source/evidence appeared during snapshot; refresh",
    )
    require(
        sha(bounded_bytes(backlog, 8 * 1024 * 1024)) == sha(initial),
        "Registry changed during snapshot; refresh",
    )
    source_observations.verify()
    if lock_before is not None:
        require(
            lock.is_file() and bounded_bytes(lock, 64 * 1024) == lock_before,
            "Lock changed during snapshot; refresh",
        )
    elif lock_unavailable is None:
        require(not lock.exists(), "Lock appeared during snapshot; refresh")
    program.require_current_location()
    require(
        sha(bounded_bytes(backlog, 8 * 1024 * 1024)) == sha(initial),
        "Registry changed during snapshot; refresh",
    )
    return output


def prepare_relocation(
    program: Program, state: Record, args: argparse.Namespace
) -> tuple[Program, bytes, list[str]]:
    """Publish a verified recovery copy; archive the old location without deleting it."""
    director = coordination.current_director_id(state)
    require(
        args.owner == (director or "leader"),
        "Relocation requires current director or legacy leader",
    )
    requested = Path(args.destination)
    destination = requested.absolute() if requested.is_absolute() else program.repo / requested
    require(
        destination.is_relative_to(program.repo) and destination != program.repo,
        "Destination must stay within repository",
    )
    destination = safe_path(program.repo, destination.relative_to(program.repo).as_posix())
    require(
        not destination.is_relative_to(program.directory)
        and not program.directory.is_relative_to(destination),
        "Relocation roots must not overlap",
    )
    require(not destination.exists(), "Relocation destination already exists; no overwrite")
    original = bounded_bytes(program.path("backlog.json"), 8 * 1024 * 1024)
    # Preflight before creating a destination or reading any excluded path.
    names: list[str] = []
    for folder, dirs, filenames in os.walk(program.directory):
        relative_folder = Path(folder).relative_to(program.directory)
        for name in dirs:
            safe_path(program.directory, (relative_folder / name).as_posix())
        for name in sorted(filenames):
            relative = (relative_folder / name).as_posix()
            path = program.path(relative)
            if name == ".feature-program.lock":
                require(relative == name, "Nested program is busy")
                continue
            if name == "backlog.json" and relative != name:
                require(
                    path.with_name("RELOCATED.json").is_file(),
                    "Relocate active nested programs separately first",
                )
            require(path.is_file(), "Relocation accepts regular files only")
            names.append(relative)
    require(len(names) <= 10000, "Relocation exceeds 10000 files")
    target = Program(program.repo, destination)
    planning.validate_publication_endpoint(program.repo, destination)
    planning.require_unreserved(target)
    return target, original, names


def relocate(program: Program, state: Record, args: argparse.Namespace) -> None:
    target, original, names = prepare_relocation(program, state, args)
    destination = target.directory
    backup_path = f"recovery/relocations/{sha(original)}/original-backlog.json"
    manifest_path = f"recovery/relocations/{sha(original)}/manifest.json"
    files: dict[str, str] = {}
    budget = ReadBudget(256 * 1024 * 1024)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.mkdir()  # Exclusive reservation: a concurrent/existing target is never replaced.
    with target.lock():
        require(
            sorted(item.name for item in destination.iterdir()) == [".feature-program.lock"],
            "Destination acquired content before reservation; no overwrite",
        )
        marker: Record = {
            "source": program.directory.relative_to(program.repo).as_posix(),
            "destination": destination.relative_to(program.repo).as_posix(),
            "phase": "copying",
        }
        operation_id = planning.publication_register(
            program,
            "relocation",
            {
                **marker,
                "program_id": state["program_id"],
                "original_registry_sha256": sha(original),
                "copying_marker": marker,
            },
            target,
            supersedes=getattr(args, "supersedes", None),
        )
        planning.publication_begin(program, operation_id)
        atomic_write(target.path("RELOCATION_INCOMPLETE.json"), canonical(marker))
        for name in names:
            content = budget.read(program.path(name), 8 * 1024 * 1024)
            files[name] = sha(content)
            atomic_write(target.path(name), content)
        require(
            sha(bounded_bytes(program.path("backlog.json"), 8 * 1024 * 1024)) == sha(original),
            "Source registry changed during relocation",
        )
        old_root = string(marker["source"], "old root")
        new_root = string(marker["destination"], "new root")
        planning.remap_sources(state, old_root, new_root)
        source = record(state["source"], "source")
        old_source = dict(source)
        for key in ("path", "reference_base"):
            value = string(source[key], key)
            if value == old_root or value.startswith(old_root + "/"):
                source[key] = new_root + value[len(old_root) :]
        if state.get("planning_path") is not None:
            planning_path = string(state["planning_path"], "planning path")
            if planning_path.startswith(old_root + "/"):
                state["planning_path"] = new_root + planning_path[len(old_root) :]
        contract = state.get("phases")
        if contract is not None:
            phase_metadata = record(contract, "phases")
            planning_path = string(phase_metadata["planning_path"], "phase planning path")
            if planning_path.startswith(old_root + "/"):
                phase_metadata["planning_path"] = new_root + planning_path[len(old_root) :]
        program.event(
            state,
            "relocate",
            {
                "actor": args.owner,
                "reason": args.reason,
                "source_directory": old_root,
                "destination_directory": new_root,
                "original_registry_sha256": sha(original),
                "previous_source": old_source,
            },
        )
        target.validate(state)
        encoded = canonical(state)
        require(len(encoded) <= 8 * 1024 * 1024, "Canonical registry exceeds 8 MiB")
        derived = target.views(state)
        for name, content in files.items():
            require(
                sha(bounded_bytes(target.path(name), 8 * 1024 * 1024)) == content,
                f"Copied artifact changed: {name}",
            )
        require(
            not target.path(backup_path).exists() and not target.path(manifest_path).exists(),
            "Recovery archive collision",
        )
        atomic_write(target.path(backup_path), original)
        atomic_write(target.path("backlog.json"), encoded)
        for name, content in derived.items():
            atomic_write(target.path(name), content.encode("utf-8"))
        target.validate(read_json(target.path("backlog.json")))
        manifest: Record = {
            **marker,
            "phase": "ready",
            "backup_path": backup_path,
            "manifest_path": manifest_path,
            "original_registry_sha256": sha(original),
            "registry_sha256": sha(encoded),
            "files": {
                name: value
                for name, value in files.items()
                if name != "backlog.json" and name not in derived
            },
            "source_files": files,
        }
        atomic_write(target.path(manifest_path), canonical(manifest))
        atomic_write(target.path("RELOCATION_INCOMPLETE.json"), canonical(manifest))
        # Immediately before archival, detect external writes even during publication.
        fresh_names = sorted(
            path.relative_to(program.directory).as_posix()
            for path in program.directory.rglob("*")
            if path.is_file() and path.name != ".feature-program.lock"
        )
        require(fresh_names == sorted(names), "Source inventory changed during relocation")
        fresh_budget = ReadBudget(256 * 1024 * 1024)
        for name, expected in files.items():
            require(
                sha(fresh_budget.read(program.path(name), 8 * 1024 * 1024)) == expected,
                f"Source artifact changed during relocation: {name}",
            )
        target.validate(read_json(target.path("backlog.json")))
        copied_budget = ReadBudget(256 * 1024 * 1024)
        for name, expected in record(manifest["files"], "copy manifest").items():
            require(
                sha(copied_budget.read(target.path(name), 8 * 1024 * 1024)) == expected,
                f"Copied artifact changed before archival: {name}",
            )
        atomic_write(
            program.path("RELOCATED.json"),
            canonical(
                {"destination": new_root, "registry_sha256": sha(encoded), "recorded_at": utc()}
            ),
        )
        target.path("RELOCATION_INCOMPLETE.json").unlink()
        planning.publication_complete(program, operation_id, manifest)


def prepare_relocation_recovery(
    program: Program, args: argparse.Namespace
) -> tuple[Program, Record | None, object]:
    """Finalize only an already verified publication; never promote an incomplete copy."""
    located = planning.publication_find(
        program, "relocation", getattr(args, "publication_id", None)
    )
    if not program.path("RELOCATION_INCOMPLETE.json").exists() and located is not None:
        target_name = string(sequence(located[0]["endpoints"], "endpoints")[1], "destination")
        require(
            target_name.casefold()
            == program.directory.relative_to(program.repo).as_posix().casefold(),
            "Finalize completed relocation at its exact destination",
        )
        state = program.load()
        require(
            args.owner == (coordination.current_director_id(state) or "leader"),
            "Recovery requires current director or legacy leader",
        )
        source_program = Program(
            program.repo,
            safe_path(
                program.repo, string(sequence(located[0]["endpoints"], "endpoints")[0], "source")
            ),
        )
        return source_program, None, located
    marker = read_json(program.path("RELOCATION_INCOMPLETE.json"))
    require(
        marker.get("phase") == "ready",
        "Copy never reached verified publication; original remains authoritative",
    )
    require(
        marker.get("destination") == program.directory.relative_to(program.repo).as_posix(),
        "Recovery destination mismatch",
    )
    source = Program(
        program.repo, safe_path(program.repo, string(marker.get("source"), "source directory"))
    )
    require(
        sha(bounded_bytes(program.path("backlog.json"), 8 * 1024 * 1024))
        == marker.get("registry_sha256"),
        "Published registry changed",
    )
    require(
        sha(
            bounded_bytes(
                program.path(string(marker.get("backup_path"), "backup path")), 8 * 1024 * 1024
            )
        )
        == marker.get("original_registry_sha256"),
        "Recovery backup changed",
    )
    for name, expected in record(marker.get("files"), "copy manifest").items():
        require(
            sha(bounded_bytes(program.path(name), 8 * 1024 * 1024)) == expected,
            f"Recovery artifact changed: {name}",
        )
    state = read_json(program.path("backlog.json"))
    program.validate(state)
    require(
        args.owner == (coordination.current_director_id(state) or "leader"),
        "Recovery requires current director or legacy leader",
    )
    for name, content in program.views(state).items():
        require(
            bounded_bytes(program.path(name), 8 * 1024 * 1024) == content.encode("utf-8"),
            "Recovery views changed",
        )
    if source.path("RELOCATED.json").exists():
        archive = read_json(source.path("RELOCATED.json"), limit=64 * 1024)
        require(
            archive.get("destination") == marker["destination"]
            and archive.get("registry_sha256") == marker.get("registry_sha256"),
            "Source archive does not match publication",
        )
    else:
        require(
            sha(bounded_bytes(source.path("backlog.json"), 8 * 1024 * 1024))
            == marker.get("original_registry_sha256"),
            "Original source changed; recovery cannot archive it",
        )
        source.load()
        source_files = record(marker.get("source_files"), "source manifest")
        require(
            sorted(
                path.relative_to(source.directory).as_posix()
                for path in source.directory.rglob("*")
                if path.is_file() and path.name != ".feature-program.lock"
            )
            == sorted(source_files),
            "Original inventory changed; recovery cannot archive it",
        )
        for name, expected in source_files.items():
            require(
                sha(bounded_bytes(source.path(name), 8 * 1024 * 1024)) == expected,
                f"Original artifact changed: {name}",
            )
    return source, marker, located


def recover_relocation(program: Program, args: argparse.Namespace) -> None:
    source, _, _ = prepare_relocation_recovery(program, args)
    with source.lock(publication_recovery=True):
        source, marker, located = prepare_relocation_recovery(program, args)
        if marker is None:
            require(located is not None, "Missing completed publication")
            planning.publication_complete(program, string(located[0]["id"], "publication ID"))
            return
        if located is None:
            state = read_json(program.path("backlog.json"))
            operation_id = planning.publication_register(
                source,
                "relocation",
                {**marker, "program_id": state["program_id"]},
                program,
                legacy_observed=True,
            )
            planning.publication_begin(program, operation_id)
            located = planning.publication_find(program, "relocation", operation_id)
        if not source.path("RELOCATED.json").exists():
            atomic_write(
                source.path("RELOCATED.json"),
                canonical(
                    {
                        "destination": marker["destination"],
                        "registry_sha256": marker["registry_sha256"],
                        "recorded_at": utc(),
                        "recovery_reason": args.reason,
                    }
                ),
            )
        program.path("RELOCATION_INCOMPLETE.json").unlink()
        if located is not None:
            planning.publication_complete(
                program, string(located[0]["id"], "publication ID"), marker
            )


def starter(program: Program, args: argparse.Namespace) -> None:
    """Create synthetic local starting material; never initialize progress or evidence."""
    task_ids = args.task or ["TASK-1", "TASK-2"]
    require(
        bool(task_ids)
        and len(task_ids) == len({name.casefold() for name in task_ids})
        and all(portable_task_id(name) for name in task_ids),
        "Starter IDs must be portable unique identifiers",
    )
    string(args.program_id, "program_id")
    objective = string(args.objective, "human objective")
    criteria = [string(value, "human objective criterion") for value in args.criterion]
    require(bool(criteria), "Human objective needs explicit completion criteria")
    report = "# Synthetic work-program source\n\nReplace the examples with the approved scope before initialization.\n\n"
    for task_id in task_ids:
        report += f"### {task_id}. Replace this synthetic task\n\n- Goal: describe a bounded authorized outcome.\n- Existing evidence: inspect current source; do not assume an old suggestion is a bug.\n- Proposed change: replace this example with the approved delta.\n- Acceptance: record every criterion and actual verification required.\n\n"
    head = program.current_head()
    plan: Record = {
        "schema_version": 1,
        "program_id": args.program_id,
        "baseline_head": head,
        "objective": {
            "id": "objective-1",
            "title": objective,
            "criteria": criteria,
            "task_ids": list(task_ids),
            "status": "active",
            "supplied_by": "user",
            "decision_owner": "director",
            "next_action_owner": "director",
            "history": [],
        },
        "workstreams": [
            {
                "id": "delivery",
                "name": "Delivery",
                "default_agent": "worker",
                "focus": "Replace with approved bounded focus",
            }
        ],
        "tasks": [
            {
                "id": task_id,
                "workstream": "delivery",
                "phase": index + 1,
                "depends_on": [],
                "related_to": [],
                "conflict_groups": [],
                "candidate_paths": [],
                "required_checks": ["acceptance", "scoped_tests", "format_lint"],
                "next_action": "Replace synthetic scope and revalidate current source before claiming",
                "planning_note": "Example only; no completed work or evidence",
            }
            for index, task_id in enumerate(task_ids)
        ],
    }
    if args.language is not None:
        plan["communication_language"] = communication_language(args.language)
    files = {
        "source/feature-opportunities.md": report,
        "planning.json": canonical(plan).decode(),
        "README.md": "# Local work-program recovery\n\nRead DIRECTOR.md, DECISIONS.md and CHECKPOINT.md if present. Inspect actual Git state and recorded claims before changing anything. Never infer a live worker or completion from a saved name. Keep all real plans, source, state and evidence local/ignored.\n\nThe generated source/plan are synthetic examples: replace them, review the complete scope, and explicitly initialize with tools/feature_program.py init --review-baseline CURRENT_REVIEWED_SHA. For a custom --program-dir, supply its repository-relative --report and --planning paths. No backlog or passing evidence is created by starter. Use status/check/packet and bounded revalidation before each claim.\n",
        "DIRECTOR.md": "# Final director contract\n\nThe human supplies the major objective and completion criteria. The final director decomposes it and decides task purposes, methods, priority, scope, conflicts, acceptance, assignments and authorized delivery within that objective. Check current user authority before Git/PR/CI/merge actions; authority is not granted by this template. Create, reuse, replace or retire workers/sub-directors according to independent work, capability, ownership, cost and observed availability; do not enforce a fixed staff count. Record reasons, exact verified launch settings or inherited/unknown values, and evidence. Native agent creation happens outside this tool. A saved address is not live-agent proof.\n\nSub-directors audit bounded areas, continuity, blockers and requirements. Multiple sub-directors may report to the final director; hierarchy does not grant merge authority or promote them automatically. Consult their durable record after compaction. Verify claims, complete acceptance, source hashes and actual latest-revision CI before accepting delivery. Repeated review must bind changed bytes.\n\nWhen the final director accepts the entire objective against its explicit criteria/evidence, record that verdict and assign next-goal setting to the human. Wait in awaiting_next_goal; do not invent or autonomously start another major objective. Start the next preserved cycle only after an explicit human goal arrives.\n",
        "SUBDIRECTOR.md": "# Bounded oversight and continuity\n\nAudit assigned areas and report goal/criteria drift, ownership conflicts, stale evidence and unrecorded decisions to the final director. Multiple sub-directors may oversee independent areas; only the appointed final director arbitrates conflicts and authorized integration. Inspect actual diffs/evidence against the goal, not only worker self-reports. Review at claim/start, meaningful milestones, changed assumptions/scope, final review/merge and recovery.\n\nUse note/block/rework/release/handoff to record drift, correction and fresh source validation. Routine methods can be corrected within the approved objective. If the director is uncertain about a material objective, acceptance or scope conflict, ask the human a focused question with options/tradeoffs and block only dependent work. Never infer consent from elapsed time. Preserve the reply and latest decision pointers before resuming.\n",
        "WORKER.md": "# Worker assignment and evidence\n\nRead the task packet and current decisions. Own only the explicit claimed paths and attempt evidence; preserve others' edits. Revalidate complete acceptance at actual HEAD and scoped digest before claim. Capture executed commands/outcomes honestly. Compilation and native execution are distinct. Block only affected work. Submit immutable execution receipts for review; never mark done from a chat message.\n\nBefore replacement, preserve outgoing goal, constraints, HEAD, scope/digest, changed paths, evidence, decisions, unresolved findings and next action. The director records explicit release; the incoming worker acknowledges the handoff and freshly revalidates before claiming a new attempt. Timeout alone never authorizes takeover.\n",
        "REVIEW.md": "# Repeated review contract\n\nCompare the entire original acceptance against the exact submitted receipt/source hashes and real evidence. Record accepted or rejected review with precise findings. Leader/self review needs an honest reason, never a claim of independence. Rework retains frozen rounds and uses unique filenames for new evidence. Completed attempts remain historical; regressions require a reasoned leader reopening and new revalidation. Caller-recorded CI is distinct from verified remote observations and merge.\n",
        "EVIDENCE.md": "# Evidence protocol\n\nEvidence belongs under evidence/TASK/attempt-N/ and becomes immutable when admitted. Use template TASK --kind revalidation|execution|review|delivery for failing-safe JSON skeletons. Replace every placeholder with actual facts; absent, skipped or unavailable verification never means passed. Native checks cannot be replaced by compilation. Do not store credentials, raw user data, private configuration contents or huge logs.\n",
        "DECISIONS.md": "# Local program decisions\n\nNo execution, staffing or delivery authorization is implied by these synthetic starter files. Record current human authorization, affected tasks, rationale and consequences before applying new direction. Keep temporary status in backlog/checkpoints.\n",
        "DESIGN.md": "# Program invariants\n\nCanonical backlog.json is the only runtime status store. Source/plan task sets match exactly; source sections remain lossless. Hard dependencies are acyclic; claims require fresh source/evidence and serialize scopes/conflict groups. Requirements are not silently waived. The tool records facts, does not execute tests, native apps, agents or GitHub automation. Derived views can be recovered through render without resetting history.\n",
        "DELIVERY.md": "# Reviewed delivery and branch cleanup\n\nThe final director checks the complete reviewed source, actual latest-revision PR/CI and current human merge authority before integration. Record reviewed completion, caller-recorded CI, verified remote observations, push, merge and cleanup as separate facts. Unexpected skipped, missing, failed or canceled required checks prevent verified delivery. No Git action is performed by this registry or dashboard.\n\nAfter confirmed merge and target inclusion, inspect whether the work branch is unchanged, has no active checkout/worktree and no dependent PR. Only then use authorized normal local/remote branch deletion and record done/deferred evidence. Never delete an unmerged/default/protected/target/advanced branch, never force deletion, and preserve PR/commit/CI/evidence history. Deferred cleanup does not erase completed work.\n",
        "QUESTIONS.md": "# Human questions and recorded replies\n\nThe director issues a focused question when the major goal, acceptance or material scope is uncertain. Include options/tradeoffs and identify dependent work. The human can answer through the configured local question inbox; this is the dashboard's narrow write exception. A saved answer is not automatically applied.\n\nPreserve issued -> answered -> acknowledged -> applied states, exact question/version and answer receipt. The director inspects the recorded reply, acknowledges it, records the resulting decision and applies only authorized follow-up. Do not interpret a timeout as consent or automatically wake/recreate a stopped director. After recovery, inspect unanswered or unacknowledged questions before dependent work resumes.\n",
    }
    files["DIRECTOR.md"] += (
        "\n## Launch settings and integrated acceptance\n\nChoose supported model/reasoning combinations according to complexity, consequence, independent review, speed and cost. Preserve more specific human choices. Announce actual launch settings before launching and record the reason/provenance; inherited unverified settings stay unknown. Running agents do not change configuration when a saved actor record changes. Use an acknowledged handoff and a new launch when a change is required. Freeze assignment-time settings for history.\n\nBefore goal completion, require a fresh independent integrated review of all goal criteria and aggregate member-task scope, bound to the exact current source digest. Preserve its accepted artifact and evidence under the coordination evidence directory. A prior review or per-task success alone does not establish complete goal acceptance.\n"
    )
    files["SUBDIRECTOR.md"] += (
        "\nAudit launch-setting rationale and provenance and immutable assignment history. Do not claim a running role was reconfigured from a metadata edit. Surface missing independent integrated acceptance to the final director.\n"
    )
    for name in ("README.md", "DIRECTOR.md", "SUBDIRECTOR.md", "WORKER.md", "QUESTIONS.md"):
        files[name] += (
            "\nUse the program's recorded communication_language for user questions, options and explanations across recovery and handoffs. Missing/null means unknown; never infer the language from a browser locale. Preserve original source and user replies. Change the recorded language only after an explicit human language change. Maintained documentation remains English; scripts, tests and UI may contain the authorized user language.\n"
        )
    files["README.md"] += (
        "\nThis guide was created with an uninitialized starter. If backlog.json is absent, "
        "explicitly initialize before using status, check or packet. If backlog.json is present, "
        "inspect actual status/check results and do not repeat initialization. Initialization preserves "
        "this guide and any authored replacement; generated BOARD.md, CHECKPOINT.md and task "
        "packets describe initialized work. Resume from the selected program's actual registry "
        "and location guards, not from these examples.\n"
    )
    for name in ("DIRECTOR.md", "SUBDIRECTOR.md", "WORKER.md"):
        files[name] += (
            "\nInspect related_to, dependencies and the current owner/contact registry. Communicate directly with peers for bounded API, data and dependency agreements while preserving owned-file boundaries. Persist consequential agreements/blockers as task notes/evidence before relying on them and record counterpart acknowledgement. Escalate cross-scope or goal conflicts to the sub-director/final director. If the native runtime cannot address a peer, relay through the director; never invent delivery or acknowledgement. This tool does not send messages or launch agents.\n"
        )
    require(
        not program.path("backlog.json").exists()
        and all(not program.path(name).exists() for name in files),
        "Starter refuses to overwrite existing program material",
    )
    for name, text in files.items():
        atomic_write(program.path(name), text.encode("utf-8"))


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--repo", type=Path, default=ROOT)
    result.add_argument(
        "--program-dir",
        default=DEFAULT_PROGRAM,
        help="Program directory inside the repository (relative or absolute)",
    )
    commands = result.add_subparsers(dest="command", required=True)
    start = commands.add_parser(
        "starter", help="Create synthetic local source/plan and recovery instructions"
    )
    start.add_argument("--program-id", required=True)
    start.add_argument(
        "--language", help="Explicit user communication language tag; absent is unknown"
    )
    start.add_argument("--objective", required=True, help="Explicit human-supplied major objective")
    start.add_argument(
        "--criterion",
        action="append",
        required=True,
        help="Human objective completion criterion; repeat",
    )
    start.add_argument(
        "--task", action="append", help="Portable task ID; repeat for multiple synthetic examples"
    )
    commands.add_parser(
        "snapshot", help="Print bounded read-only dashboard JSON; never creates a lock"
    )
    init = commands.add_parser("init")
    init.add_argument("--report")
    init.add_argument("--planning")
    init.add_argument(
        "--review-baseline", required=True, help="Original report reviewed commit SHA"
    )
    init.add_argument(
        "--reference-base",
        default="docs/development",
        help="Original source-relative evidence link base",
    )
    for name in ("check", "status", "next", "render"):
        commands.add_parser(name)
    language = commands.add_parser(
        "language", help="Record an explicit human communication language"
    )
    language.add_argument("--value", required=True)
    language.add_argument("--owner", required=True)
    language.add_argument("--reason", required=True)
    move = commands.add_parser(
        "relocate", help="Verify a recovery copy and archive the original program"
    )
    move.add_argument("--destination", required=True)
    move.add_argument("--owner", required=True)
    move.add_argument("--reason", required=True)
    move.add_argument("--supersedes", help="Explicit active failed-copy operation to replace")
    recover = commands.add_parser(
        "relocate-recover", help="Finalize an already verified incomplete publication"
    )
    recover.add_argument("--owner", required=True)
    recover.add_argument("--reason", required=True)
    recover.add_argument(
        "--publication-id", help="Exact active operation when recovery is ambiguous"
    )
    packet = commands.add_parser("packet")
    packet.add_argument("id")
    fingerprint = commands.add_parser("fingerprint")
    fingerprint.add_argument("--scope", action="append", required=True)
    template = commands.add_parser("template")
    template.add_argument("id")
    template.add_argument(
        "--kind",
        choices=("revalidation", "execution", "review", "delivery", "resume"),
        required=True,
    )
    template.add_argument("--scope", action="append")
    claim = commands.add_parser("claim")
    claim.add_argument("id")
    claim.add_argument("--owner", required=True)
    claim.add_argument("--scope", action="append", required=True)
    claim.add_argument("--next-action", required=True)
    claim.add_argument("--revalidation", required=True, help="Program-relative evidence JSON")
    for name in ("note", "block", "release", "reopen"):
        command = commands.add_parser(name)
        command.add_argument("id")
        command.add_argument(
            "--owner",
            required=True,
            help="Current owner or accepted director (legacy leader without appointment)",
        )
        command.add_argument("--reason", required=True)
        command.add_argument("--next-action", required=True)
        command.add_argument("--evidence", action="append", default=[])
    submit = commands.add_parser("submit")
    submit.add_argument("id")
    submit.add_argument("--owner", required=True)
    submit.add_argument("--receipt", required=True)
    resume = commands.add_parser(
        "resume", help="Resolve a blocker on the same guarded owner/attempt"
    )
    resume.add_argument("id")
    resume.add_argument("--owner", required=True)
    resume.add_argument(
        "--receipt", required=True, help="Immutable attempt-relative resolution JSON"
    )
    for name in ("complete", "rework"):
        review = commands.add_parser(name)
        review.add_argument("id")
        review.add_argument("--review", required=True)
    delivery = commands.add_parser("delivery")
    delivery.add_argument("id")
    delivery.add_argument("--receipt", required=True)
    checkpoint = commands.add_parser("checkpoint")
    checkpoint.add_argument("--note", required=True)
    checkpoint.add_argument("--next-action", required=True)
    checkpoint.add_argument("--team-state", default=".work/handoffs/current.json")
    questions.add_commands(commands)
    coordination.add_commands(commands)
    phases.add_commands(commands)
    planning.add_commands(commands)
    coverage.add_commands(commands)
    presentation.add_commands(commands)
    authoring.add_commands(commands)
    brief_command = commands.add_parser("brief", help="Bounded advisory program or task context")
    brief_command.add_argument("--task")
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        repo = args.repo.absolute()
        requested = Path(args.program_dir)
        directory = requested.absolute() if requested.is_absolute() else repo / requested
        require(
            directory.is_relative_to(repo) and directory != repo,
            "Program must be within configured repository",
        )
        relative_program = directory.relative_to(repo).as_posix()
        planning.validate_publication_endpoint(repo, directory)
        program = Program(repo, safe_path(repo, relative_program))
        if args.command in presentation.READ_COMMANDS:
            sys.stdout.buffer.write(canonical(presentation.read_command(program, args)))
            return 0
        if args.command in authoring.READ_COMMANDS:
            print(canonical(authoring.read_command(program, args)).decode(), end="")
            return 0
        if args.command == "snapshot":
            print(canonical(read_snapshot(repo, program.directory)).decode(), end="")
            return 0
        if args.command == "brief":
            # The bound covers emitted bytes, including on Windows: text-mode
            # newline translation would otherwise expand the canonical LF output.
            sys.stdout.buffer.write(canonical(read_brief(repo, program.directory, args.task)))
            return 0
        if args.command in {"coverage", "coverage-binding"}:
            initial = bounded_bytes(program.path("backlog.json"), 8 * 1024 * 1024)
            state = read_json(
                program.path("backlog.json"),
                budget=ReadBudget(cached={program.path("backlog.json"): initial}),
            )
            program.require_current_location()
            if args.command == "coverage-binding":
                result = coverage.bind_input(program, state, read_json(program.path(args.input)))
            else:
                observed_head = None
                metadata_error: Record | None = None
                if (
                    args.operation in {"submit", "complete", "objective-complete"}
                    and args.input is not None
                ):
                    try:
                        observed_head = program.current_head()
                    except (ProgramError, OSError) as exc:
                        metadata_error = {
                            "operation": args.operation,
                            "status": "not_assessed",
                            "reasons": [str(exc)],
                            "failure_type": type(exc).__name__,
                            "limitations": [
                                "Current Git metadata could not be observed; no baseline substitution."
                            ],
                        }
                diagnostic = coverage.diagnostics(program, state, args, observed_head=observed_head)
                structural = record(diagnostic["structural_validation"], "original validation")
                if structural["status"] == "failed":
                    result = {
                        "projection": {
                            **structural,
                            "status": "unavailable",
                            "limitations": [
                                "Original state failed authoritative validation; coverage facts were not projected."
                            ],
                        }
                    }
                else:
                    result = coverage.projection(program, state)
                if metadata_error is not None and diagnostic["status"] == "not_assessed":
                    diagnostic = {**diagnostic, **metadata_error}
                result["diagnostics"] = diagnostic
            require(
                bounded_bytes(program.path("backlog.json"), 8 * 1024 * 1024) == initial,
                "Registry changed during coverage observation; refresh",
            )
            require(len(canonical(result)) <= 8 * 1024 * 1024, "Coverage output exceeds 8 MiB")
            print(canonical(result).decode(), end="")
            return 0
        if args.command == "admission-binding":
            state = program.load()
            receipt = read_json(program.path(args.input))
            print(canonical(planning.preview(program, state, receipt)).decode(), end="")
            return 0
        if args.command == "starter":
            with program.lock():
                starter(program, args)
            print("starter: OK; synthetic files only, no registry or passing evidence created")
            return 0
        if args.command == "init":
            planning.require_unreserved(program)
            args.report = args.report or relative_program + "/source/feature-opportunities.md"
            args.planning = args.planning or relative_program + "/planning.json"
        for name in ("owner", "reason", "next_action", "note"):
            if hasattr(args, name):
                string(getattr(args, name), name)
        if args.command == "relocate-recover":
            with program.lock(publication_recovery=True):
                recover_relocation(program, args)
            print("relocate-recover: OK; explicitly use this destination")
            return 0
        if args.command == "program-succeed-recover":
            with program.lock(publication_recovery=True):
                phases.recover_succession(program, args)
            print("program-succeed-recover: OK; predecessor guarded, use identified successor")
            return 0
        if args.command == "fingerprint":
            snapshot = program.source_snapshot([safe_relative(path) for path in args.scope])
            print(
                canonical(
                    {
                        "head": git(repo, ["rev-parse", "HEAD"]),
                        "scope": sorted(args.scope),
                        "source_digest": digest(snapshot),
                        "source_snapshot": snapshot,
                    }
                ).decode(),
                end="",
            )
            return 0
        with program.lock():
            state = program.initialize(args) if args.command == "init" else program.load()
            if args.command == "presentation-record":
                prepared = presentation.prepare_record(program, state, args.input)
                program.save(prepared.state, before_publish=prepared.before_publish)
                print("presentation-record: OK")
                return 0
            if args.command == "coverage-record":
                coverage.record(program, state, args.input)
                print(f"coverage-record: OK; committed revision {phases.revision(state)}")
                return 0
            if args.command in {"publication-adopt", "publication-reconcile"}:
                if args.command == "publication-adopt":
                    planning.publication_adopt(program, state, args.input)
                else:
                    planning.publication_reconcile(program, state, args.input)
                print(f"{args.command}: OK; explicit publication observation recorded")
                return 0
            if planning.execute(program, state, args):
                print(f"{args.command}: OK; committed revision {phases.revision(state)}")
                return 0
            if (
                phases.apply_command(program, state, args)
                or questions.apply_command(program, state, args)
                or coordination.apply_command(program, state, args)
            ):
                program.save(state)
                print(f"{args.command}: OK")
                return 0
            match args.command:
                case "init":
                    planning.fresh_init(program, args, state)
                    program.save(state)
                case "phase-binding":
                    print(canonical(phases.binding(program, state)).decode(), end="")
                    return 0
                case "program-succeed":
                    phases.succeed(program, state, args)
                case "relocate":
                    relocate(program, state, args)
                case "check":
                    program.check(state)
                case "status" | "next":
                    print(canonical(program.status(state, args.command == "next")).decode(), end="")
                    return 0
                case "packet":
                    print(program.packet(state, program.task(state, args.id)), end="")
                    return 0
                case "template":
                    print(canonical(program.template(state, args)).decode(), end="")
                    return 0
                case "render":
                    program.render(state)
                case "claim":
                    program.claim(state, args)
                    program.save(state)
                case "language":
                    proposal = program.prepare_language(state, args)
                    state.clear()
                    state.update(proposal)
                    program.save(state)
                case "note" | "block" | "release" | "reopen":
                    program.transition(state, args)
                    program.save(state)
                case "submit":
                    program.submit(state, args)
                    program.save(state)
                case "resume":
                    program.resume(state, args)
                case "complete" | "rework":
                    program.review(state, args)
                    program.save(state)
                case "delivery":
                    program.delivery(state, args)
                    program.save(state)
                case "checkpoint":
                    program.checkpoint(state, args)
                    program.save(state)
                case other:
                    raise ProgramError(f"Unknown command: {other}")
            print(f"{args.command}: OK")
        return 0
    except (
        ProgramError,
        coordination.GovernanceError,
        phases.PhaseError,
        planning.PlanningError,
        questions.QuestionError,
        OSError,
    ) as exc:
        print(f"feature-program: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
