"""Durable, offline coordination for the reviewed Unity MCP feature program.

Run with Server/.venv/Scripts/python.exe tools/feature_program.py --help.
The registry is deliberately mutable: every transition appends its durable history.
"""

from __future__ import annotations

import argparse
from collections import Counter
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
import tempfile
from typing import Final, Iterator, TypeAlias


Json: TypeAlias = str | int | float | bool | None | list["Json"] | dict[str, "Json"]
Record: TypeAlias = dict[str, Json]
ROOT: Final = Path(__file__).resolve().parents[1]
DEFAULT_PROGRAM: Final = ".omo/feature-program"
REPORT: Final = DEFAULT_PROGRAM + "/source/feature-opportunities.md"
EXPECTED_IDS: Final = tuple(
    f"{prefix}{index:02d}"
    for prefix, count in (
        ("S", 15),
        ("E", 17),
        ("V", 17),
        ("Q", 16),
        ("P", 15),
        ("DX", 16),
        ("N", 15),
    )
    for index in range(1, count + 1)
    if (prefix, index) != ("DX", 11)
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
    return (json.dumps(value, indent=2, ensure_ascii=True, sort_keys=True) + "\n").encode()


def digest(value: Json) -> str:
    return sha(canonical(value))


def utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def safe_relative(name: str) -> str:
    """Reject sensitive or escaping paths before checking any filesystem contents."""
    require("\\" not in name, "Use repository-relative forward-slash paths")
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


def read_json(path: Path) -> Record:
    def unique(pairs: list[tuple[str, Json]]) -> Record:
        result: Record = {}
        for key, value in pairs:
            require(key not in result, f"Duplicate JSON key: {key}")
            result[key] = value
        return result

    try:
        value = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=unique)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ProgramError(f"Invalid JSON in {path.name}: {exc}") from exc
    return record(value, path.name)


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


def extract_sections(data: bytes) -> Record:
    """Keep complete original feature sections, including exact newline bytes."""
    text = data.decode("utf-8")
    headings = list(re.finditer(r"(?m)^#{1,3} [^\r\n]+", text))
    sections: Record = {}
    for index, heading in enumerate(headings):
        match = re.fullmatch(r"### ((?:DX|S|E|V|Q|P|N)\d{2})\. (.+)", heading[0].rstrip("\r"))
        if not match:
            continue
        task_id, title = match.groups()
        require(task_id not in sections, f"Duplicate report ID: {task_id}")
        end = headings[index + 1].start() if index + 1 < len(headings) else len(text)
        section = text[heading.start() : end]
        sections[task_id] = {
            "title": title,
            "text": section,
            "sha256": sha(section.encode("utf-8")),
            "line": text.count("\n", 0, heading.start()) + 1,
        }
    require(
        set(sections) == set(EXPECTED_IDS),
        "Report must contain the exact 110 reviewed IDs (DX11 absent)",
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
    def lock(self) -> Iterator[None]:
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

    def source_snapshot(self, scopes: list[str]) -> Record:
        require(bool(scopes), "At least one explicit scope is required")
        snapshot: Record = {}
        for scope in sorted(scopes):
            path = safe_path(self.repo, scope)
            require(
                path != self.repo and not self.directory.is_relative_to(path),
                "Root/program-wide claims are invalid",
            )
            if not path.exists():
                snapshot[scope] = "missing"
            elif path.is_file():
                snapshot[scope] = sha(path.read_bytes())
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
                        snapshot[relative] = sha(candidate.read_bytes())
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
            result.append({"path": name, "sha256": sha(path.read_bytes())})
        return result

    def verify_refs(self, refs: list[Json]) -> None:
        for value in refs:
            ref = record(value, "evidence reference")
            name = string(ref.get("path"), "evidence path")
            path = self.path(name)
            require(path.is_file(), f"Missing frozen evidence: {name}")
            require(sha(path.read_bytes()) == ref.get("sha256"), f"Frozen evidence changed: {name}")

    def load(self) -> Record:
        state = read_json(self.path("backlog.json"))
        self.validate(state)
        return state

    def validate(self, state: Record) -> None:
        require(
            state.get("schema_version") == 1 and state.get("program_id") == "unity-mcp-110",
            "Unsupported registry",
        )
        tasks = sequence(state.get("tasks"), "tasks")
        by_id = {
            string(record(task, "task").get("id"), "task ID"): record(task, "task")
            for task in tasks
        }
        require(
            len(by_id) == len(tasks) and set(by_id) == set(EXPECTED_IDS),
            "Registry must contain exact unique reviewed IDs",
        )
        streams = sequence(state.get("workstreams"), "workstreams")
        stream_ids = [
            string(record(stream, "workstream").get("id"), "workstream ID") for stream in streams
        ]
        require(len(stream_ids) == len(set(stream_ids)), "Duplicate workstream")
        owners: set[str] = set()
        active: list[Record] = []
        for task_id, task in by_id.items():
            require(task.get("workstream") in stream_ids, f"Unknown workstream: {task_id}")
            require(
                type(task.get("phase")) is int and task["phase"] in (1, 2, 3, 4),
                f"Invalid phase: {task_id}",
            )
            required = strings(task.get("required_checks"), "required_checks")
            require(
                set(required) <= CHECKS
                and {"acceptance", "scoped_tests", "format_lint"} <= set(required),
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
            require(task.get("status") in STATES, f"Invalid status: {task_id}")
            source = record(task.get("source"), "source section")
            require(
                sha(string(source.get("text"), "source text").encode()) == source.get("sha256"),
                f"Source section changed: {task_id}",
            )
            attempts = sequence(task.get("attempts"), "attempts")
            for number, value in enumerate(attempts, 1):
                attempt = record(value, "attempt")
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
                if attempt.get("status") == "done":
                    self.verify_terminal(task, attempt)
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
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(task_id: str) -> None:
            require(task_id not in visiting, f"Dependency cycle at {task_id}")
            if task_id in visited:
                return
            visiting.add(task_id)
            for dependency in strings(by_id[task_id]["depends_on"], "depends_on"):
                visit(dependency)
            visiting.remove(task_id)
            visited.add(task_id)

        for task_id in by_id:
            visit(task_id)
        for index, left in enumerate(active):
            for right in active[index + 1 :]:
                require(
                    not self.collisions(left, right),
                    f"Active scope/conflict collision: {left['id']} / {right['id']}",
                )
        sequence(state.get("events"), "events")
        record(state.get("source"), "report source")

    def verify_terminal(self, task: Record, attempt: Record) -> None:
        """Terminal code is historical; its frozen acceptance records remain mandatory."""
        require(attempt.get("status") == "done", "Done task lacks completed attempt")
        self.verify_refs(sequence(attempt.get("evidence"), "evidence"))
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
        receipt = read_json(self.path(string(receipt_ref["path"], "receipt path")))
        review = read_json(self.path(string(review_ref["path"], "review path")))
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
        require(task_id in EXPECTED_IDS, f"Unknown task: {task_id}")
        return next(
            record(task, "task")
            for task in sequence(state["tasks"], "tasks")
            if record(task, "task")["id"] == task_id
        )

    def event(self, state: Record, action: str, detail: Record) -> None:
        sequence(state["events"], "events").append({"action": action, "timestamp": utc(), **detail})

    def initialize(self, args: argparse.Namespace) -> Record:
        require(
            not self.path("backlog.json").exists(),
            "Registry already exists; initialization never resets progress",
        )
        source_path = safe_path(self.repo, args.report)
        raw = source_path.read_bytes()
        sections = extract_sections(raw)
        plan = read_json(safe_path(self.repo, args.planning))
        require(
            plan.get("schema_version") == 1 and plan.get("program_id") == "unity-mcp-110",
            "Unsupported planning input",
        )
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
            "program_id": "unity-mcp-110",
            "baseline_head": string(plan.get("baseline_head"), "baseline_head"),
            "initialized_head": git(self.repo, ["rev-parse", "HEAD"]),
            "source": {
                "path": safe_relative(args.report),
                "sha256": sha(raw),
                "review_baseline": args.review_baseline,
                "reference_base": safe_relative(args.reference_base),
            },
            "planning_sha256": sha(safe_path(self.repo, args.planning).read_bytes()),
            "workstreams": plan.get("workstreams"),
            "tasks": tasks,
            "events": [],
            "checkpoint": None,
        }
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
        source = record(state["source"], "source")
        raw = safe_path(self.repo, string(source["path"], "source path")).read_bytes()
        require(sha(raw) == source["sha256"], "Original report drifted; do not reset progress")
        sections = extract_sections(raw)
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
                path.is_file() and path.read_bytes() == data.encode("utf-8"),
                f"Derived view drift: {name}; use render",
            )

    def claim(self, state: Record, args: argparse.Namespace) -> None:
        task = self.task(state, args.id)
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
        head = git(self.repo, ["rev-parse", "HEAD"])
        evidence = self.evidence(args.id, attempt_number, [args.revalidation])
        validation = read_json(self.path(args.revalidation))
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
                "source_digest": digest(baseline),
                "evidence": evidence,
            },
        )

    def transition(self, state: Record, args: argparse.Namespace) -> None:
        task = self.task(state, args.id)
        require(
            task["status"] != "done" or args.command in {"note", "reopen"},
            "Completed task cannot be reassigned",
        )
        previous = task["status"]
        if args.command == "reopen":
            require(
                previous == "done" and args.owner == "leader",
                "Reopen requires reviewed completion and explicit leader actor",
            )
            task["status"] = "planned"
        if previous in OWNED_STATES:
            require(
                args.owner == task["owner"] or args.owner == "leader",
                "Only owner/leader may update a claim",
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
            },
        )

    def submit(self, state: Record, args: argparse.Namespace) -> None:
        task = self.task(state, args.id)
        require(
            task["status"] == "active" and task["owner"] == args.owner,
            "Submit requires active owner",
        )
        attempt = self.current_attempt(task)
        number = attempt["number"]
        require(type(number) is int, "Invalid attempt number")
        self.verify_refs(sequence(attempt["evidence"], "evidence"))
        receipt_refs = self.evidence(args.id, number, [args.receipt])
        receipt = read_json(self.path(args.receipt))
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
            require(check_id in CHECKS and check_id not in passed, "Unknown/duplicate check ID")
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
            "submitted_head": git(self.repo, ["rev-parse", "HEAD"]),
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
        task = self.task(state, args.id)
        require(task["status"] == "review", "Complete requires submitted review state")
        attempt = self.current_attempt(task)
        submission = self.verify_submission(attempt)
        number = attempt["number"]
        require(type(number) is int, "Invalid attempt number")
        refs = self.evidence(args.id, number, [args.review])
        review = read_json(self.path(args.review))
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
        refs += self.evidence(args.id, number, strings(review.get("evidence"), "review evidence"))
        attempt["evidence"] = sequence(attempt["evidence"], "evidence") + refs
        submission["review"] = refs[0]
        sequence(attempt["rounds"], "rounds")[-1] = submission
        if verdict == "accepted":
            attempt.update(
                {
                    "status": "done",
                    "review": refs[0],
                    "completed_head": git(self.repo, ["rev-parse", "HEAD"]),
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

    def delivery(self, state: Record, args: argparse.Namespace) -> None:
        task = self.task(state, args.id)
        require(task["status"] == "done", "Delivery metadata requires reviewed completion")
        attempt = self.current_attempt(task)
        number = attempt["number"]
        require(type(number) is int, "Invalid attempt number")
        self.verify_refs(sequence(attempt["evidence"], "evidence"))
        refs = self.evidence(args.id, number, [args.receipt])
        receipt = read_json(self.path(args.receipt))
        require(
            receipt.get("task_id") == args.id and receipt.get("attempt") == number,
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
        metadata = {
            "status": status,
            "remote_verified_by_tool": False,
            "receipt": refs[0],
            "metadata": receipt,
        }
        refs += self.evidence(
            args.id, number, strings(receipt.get("evidence"), "delivery evidence")
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

    def checkpoint(self, state: Record, args: argparse.Namespace) -> None:
        dirty = git(self.repo, ["status", "--porcelain=v1", "-z", "--untracked-files=all"])
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
            "head": git(self.repo, ["rev-parse", "HEAD"]),
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

    def packet(self, state: Record, task: Record) -> str:
        source = record(state["source"], "source")
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
        return (
            f"# Task {task['id']}: {section['title']}\n\n"
            "This packet is sufficient to recover the assigned task without chat history.\n"
            "Read README.md, DESIGN.md, DECISIONS.md and CHECKPOINT.md in the program directory.\n"
            "Inspect actual Git state and native agent liveness; saved owners are not live-agent proof.\n"
            "Preserve other work. Revalidate current source before each new claim. Do not run Unity,\n"
            "install packages, deploy or publish unless the task/session authorizes that action.\n\n"
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
        tasks = [self.task(state, task_id) for task_id in EXPECTED_IDS]
        counts = Counter(string(task["status"], "status") for task in tasks)
        board = [
            "# Unity MCP feature program",
            "",
            "Derived from backlog.json; update state through tools/feature_program.py.",
            "",
            "All 110 reviewed items are retained. Planned work requires current-source revalidation.",
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

    def save(self, state: Record) -> None:
        self.validate(state)
        atomic_write(self.path("backlog.json"), canonical(state))
        self.render(state)

    def render(self, state: Record) -> None:
        for name, text in self.views(state).items():
            atomic_write(self.path(name), text.encode("utf-8"))

    def status(self, state: Record, next_only: bool = False) -> Record:
        tasks = [record(value, "task") for value in sequence(state["tasks"], "tasks")]
        active = [task for task in tasks if task["status"] in OWNED_STATES]
        candidates: list[Json] = []
        for task in sorted(
            tasks, key=lambda item: (item["phase"], EXPECTED_IDS.index(string(item["id"], "ID")))
        ):
            if task["status"] != "planned":
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
        task = self.task(state, args.id)
        attempts = sequence(task["attempts"], "attempts")
        number = len(attempts) + 1 if args.kind == "revalidation" else len(attempts)
        base: Record = {"task_id": args.id, "attempt": number}
        match args.kind:
            case "revalidation":
                require(bool(args.scope), "Revalidation template requires explicit --scope")
                scope = sorted(set(safe_relative(path) for path in args.scope))
                snapshot = self.source_snapshot(scope)
                return {
                    **base,
                    "head": git(self.repo, ["rev-parse", "HEAD"]),
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
                head = git(self.repo, ["rev-parse", "HEAD"])
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


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--repo", type=Path, default=ROOT)
    result.add_argument(
        "--program-dir", default=DEFAULT_PROGRAM, help="Repository-relative program directory"
    )
    commands = result.add_subparsers(dest="command", required=True)
    init = commands.add_parser("init")
    init.add_argument("--report", default=REPORT)
    init.add_argument("--planning", default=DEFAULT_PROGRAM + "/planning.json")
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
    packet = commands.add_parser("packet")
    packet.add_argument("id", choices=EXPECTED_IDS)
    fingerprint = commands.add_parser("fingerprint")
    fingerprint.add_argument("--scope", action="append", required=True)
    template = commands.add_parser("template")
    template.add_argument("id", choices=EXPECTED_IDS)
    template.add_argument(
        "--kind", choices=("revalidation", "execution", "review", "delivery"), required=True
    )
    template.add_argument("--scope", action="append")
    claim = commands.add_parser("claim")
    claim.add_argument("id", choices=EXPECTED_IDS)
    claim.add_argument("--owner", required=True)
    claim.add_argument("--scope", action="append", required=True)
    claim.add_argument("--next-action", required=True)
    claim.add_argument("--revalidation", required=True, help="Program-relative evidence JSON")
    for name in ("note", "block", "release", "reopen"):
        command = commands.add_parser(name)
        command.add_argument("id", choices=EXPECTED_IDS)
        command.add_argument("--owner", required=True, help="Current owner or leader")
        command.add_argument("--reason", required=True)
        command.add_argument("--next-action", required=True)
        command.add_argument("--evidence", action="append", default=[])
    submit = commands.add_parser("submit")
    submit.add_argument("id", choices=EXPECTED_IDS)
    submit.add_argument("--owner", required=True)
    submit.add_argument("--receipt", required=True)
    for name in ("complete", "rework"):
        review = commands.add_parser(name)
        review.add_argument("id", choices=EXPECTED_IDS)
        review.add_argument("--review", required=True)
    delivery = commands.add_parser("delivery")
    delivery.add_argument("id", choices=EXPECTED_IDS)
    delivery.add_argument("--receipt", required=True)
    checkpoint = commands.add_parser("checkpoint")
    checkpoint.add_argument("--note", required=True)
    checkpoint.add_argument("--next-action", required=True)
    checkpoint.add_argument("--team-state", default=".omo/teams/team-e58decd2/team.json")
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        repo = args.repo.absolute()
        program = Program(repo, safe_path(repo, args.program_dir))
        for name in ("owner", "reason", "next_action", "note"):
            if hasattr(args, name):
                string(getattr(args, name), name)
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
            match args.command:
                case "init":
                    program.save(state)
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
                case "note" | "block" | "release" | "reopen":
                    program.transition(state, args)
                    program.save(state)
                case "submit":
                    program.submit(state, args)
                    program.save(state)
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
    except (ProgramError, OSError) as exc:
        print(f"feature-program: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
