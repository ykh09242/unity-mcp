"""Durable, offline coordination for local work programs.

Run with Server/.venv/Scripts/python.exe tools/feature_program.py --help.
The registry is deliberately mutable: every transition appends its durable history.
"""

from __future__ import annotations

import argparse
from collections import Counter, deque
from contextlib import contextmanager, nullcontext
from contextvars import ContextVar
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timezone
from functools import lru_cache
import hashlib
import importlib
import json
import math
import os
from pathlib import Path, PosixPath, PurePath, PurePosixPath, PureWindowsPath, WindowsPath
import re
import stat
from time import perf_counter
import subprocess
import sys
import tempfile
from typing import TYPE_CHECKING, Callable, Final, Iterator, Literal, TypeAlias

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
    import tools.feature_program_verification as verification
    import tools.feature_program_delivery as provider_delivery
else:
    import feature_program_authoring as authoring
    import feature_program_brief as brief
    import feature_program_coverage as coverage
    import feature_program_coordination as coordination
    import feature_program_phases as phases
    import feature_program_planning as planning
    import feature_program_presentation as presentation
    import feature_program_questions as questions
    import feature_program_verification as verification
    import feature_program_delivery as provider_delivery


if TYPE_CHECKING:
    from tools.feature_program_batch import _CheckedHistory
    from tools.feature_program_storage import RegistryObservation
    from tools.feature_program_read_model import Collector


# One explicit CLI invocation only; this records publication, never authorizes it.
_MUTATION_OUTCOME: ContextVar[dict | None] = ContextVar(
    "feature_program_mutation_outcome", default=None
)
OUTCOME_COMMANDS: Final = frozenset(
    {
        "note",
        "checkpoint",
        "verification-record",
        "review-handoff-record",
        "submit",
        "complete",
        "rework",
        "delivery",
        "objective-complete",
        "presentation-record",
    }
)


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


def flow_module(name: str):
    """Load an offline flow producer only when its supported operation is requested."""
    prefix = "tools." if __package__ else ""
    return importlib.import_module(prefix + "feature_program_" + name)


def _active_collector():
    """Return only an explicitly activated collector; ordinary reads import nothing new."""
    name = ("tools." if __package__ else "") + "feature_program_read_model"
    module = sys.modules.get(name)
    return module.active_collector() if module is not None else None


_OBSERVED_IMAGE_LIMIT: Final = 4096
_OBSERVED_BYTE_LIMIT: Final = 64 * 1024 * 1024


@dataclass(slots=True)
class _ByteObservations:
    """Pure bookkeeping for retained immutable objects, never pathname truth."""

    images: dict[int, tuple[bytes, str]] = field(default_factory=dict)
    descriptors: set[tuple[Path, str, int, int, str | None]] = field(default_factory=set)
    retained_bytes: int = 0


@dataclass(frozen=True, slots=True)
class _DependencyNames:
    """Pure results retained only by one explicit collector activation."""

    owner: Collector
    paths: dict[str, tuple[Path, str] | None] = field(default_factory=dict)
    byte_observations: _ByteObservations = field(default_factory=_ByteObservations)


_dependency_names: ContextVar[_DependencyNames | None] = ContextVar(
    "dependency_names", default=None
)


@contextmanager
def _collect_dependencies(collector: Collector) -> Iterator[None]:
    token = _dependency_names.set(_DependencyNames(collector))
    try:
        with flow_module("read_model").collect(collector):
            yield
    finally:
        _dependency_names.reset(token)


_HELD_IMAGE_LIMIT: Final = 10000
_HELD_BYTE_LIMIT: Final = 128 * 1024 * 1024


@dataclass(slots=True)
class _InitialImages:
    """Held immutable inputs for one explicitly enrolled initial computation."""

    repo: Path
    directory: Path
    original_storage_sha256: str
    proposed_body_sha256: str
    collector: Collector
    aliases: dict[tuple[str, str], tuple[Path, bytes, str]] = field(default_factory=dict)
    retained_bytes: int = 0

    def key(self, root: Path, name: str) -> tuple[str, str] | None:
        if _initial_images.get() is not self or _active_collector() is not self.collector:
            return None
        if not root.is_absolute():
            return None
        spelling = str(root)
        if spelling not in {str(self.repo), str(self.directory)}:
            return None
        relative = safe_relative(name)
        path = root / relative
        if path.is_relative_to(self.directory):
            local = path.relative_to(self.directory).as_posix().casefold()
            if local in {
                "backlog.json",
                ".feature-program.lock",
                "board.md",
                "checkpoint.md",
            } or local.startswith(("tasks/", "recovery/")):
                return None
        return spelling, name

    def remember(self, key: tuple[str, str], image: CapturedBytes) -> None:
        previous = self.aliases.get(key)
        if previous is not None:
            require(previous[1] == image.content, "Held initial input changed during capture")
        elif (
            len(self.aliases) < _HELD_IMAGE_LIMIT
            and self.retained_bytes + len(image.content) <= _HELD_BYTE_LIMIT
        ):
            self.aliases[key] = (image.path, image.content, image.sha256)
            self.retained_bytes += len(image.content)


_initial_images: ContextVar[_InitialImages | None] = ContextVar("initial_images", default=None)


@contextmanager
def _hold_initial_images(
    program: Program,
    original: RegistryObservation,
    collector: Collector,
    *,
    proposed_body: bytes,
) -> Iterator[None]:
    """Opt in only during checked initial computation; ordinary readers stay fresh."""
    require(_active_collector() is collector, "Initial image collector changed")
    require(
        collector.repo == program.repo.absolute()
        and collector.program_dir == program.directory.absolute()
        and collector.body_sha256 == sha(proposed_body)
        and sha(original.raw_bytes) == original.storage_sha256,
        "Initial image operation binding changed",
    )
    held = _InitialImages(
        program.repo.absolute(),
        program.directory.absolute(),
        original.storage_sha256,
        collector.body_sha256,
        collector,
    )
    token = _initial_images.set(held)
    try:
        yield
    finally:
        _initial_images.reset(token)
        held.aliases.clear()
        held.retained_bytes = 0


def _dependency_name(path: Path) -> tuple[Path, str] | None:
    collector = _active_collector()
    if collector is None:
        return None
    memo = _dependency_names.get()
    # Exact spelling preserves case-distinct original logical aliases. Relative
    # inputs remain dynamic because their absolute meaning depends on the cwd.
    key = str(path) if path.is_absolute() else None
    if memo is not None and memo.owner is collector and key is not None and key in memo.paths:
        return memo.paths[key]
    absolute = path.absolute()
    result = None
    for root in (collector.program_dir, collector.repo, ROOT):
        if absolute != root and absolute.is_relative_to(root):
            result = (root, absolute.relative_to(root).as_posix())
            break
    if result is None:
        collector.ineligible("dependency_outside_bound_root")
    if (
        memo is not None
        and memo.owner is collector
        and key is not None
        and len(memo.paths) < 100000
    ):
        memo.paths[key] = result
    return result


def observe_path_error(path: Path, reason: str) -> None:
    """Opaque filesystem failures are not stable missing-file observations."""
    collector = _active_collector()
    if collector is not None:
        collector.ineligible(reason)


def observe_path_probe(path: Path, operation: str, outcome: str) -> None:
    """Record the result of an explicit unchanged filesystem branch."""
    collector = _active_collector()
    named = _dependency_name(path)
    if (
        collector is not None
        and named is not None
        and not (named[0] == collector.program_dir and os.path.normcase(named[1]) == "backlog.json")
    ):
        collector.observe_probe(*named, operation=operation, outcome=outcome)


def _observe_kind(path: Path) -> None:
    try:
        mode = path.stat().st_mode
    except FileNotFoundError:
        kind = "missing"
    except OSError:
        observe_path_error(path, "path_kind_unavailable")
        return
    else:
        kind = "file" if stat.S_ISREG(mode) else "directory" if stat.S_ISDIR(mode) else "other"
    observe_path_probe(path, "kind", kind)


def observed_exists(path: Path) -> bool:
    outcome = path.exists()
    observe_path_probe(path, "exists", "true" if outcome else "false")
    if _active_collector() is not None:
        _observe_kind(path)
    return outcome


def observed_is_file(path: Path) -> bool:
    outcome = path.is_file()
    observe_path_probe(path, "is_file", "true" if outcome else "false")
    if _active_collector() is not None:
        _observe_kind(path)
    return outcome


def observed_is_dir(path: Path) -> bool:
    outcome = path.is_dir()
    observe_path_probe(path, "is_dir", "true" if outcome else "false")
    if _active_collector() is not None:
        _observe_kind(path)
    return outcome


def observed_size(path: Path) -> int:
    outcome = path.stat().st_size
    observe_path_probe(path, "size", str(outcome))
    return outcome


def _observe_bytes(path: Path, content: bytes, limit: int, budget_tag: str | None = None) -> None:
    collector = _active_collector()
    named = _dependency_name(path)
    if (
        collector is not None
        and named is not None
        and not (named[0] == collector.program_dir and os.path.normcase(named[1]) == "backlog.json")
    ):
        active = _dependency_names.get()
        memo = (
            active.byte_observations if active is not None and active.owner is collector else None
        )
        reusable = (
            memo is not None
            and type(content) is bytes
            and type(limit) is int
            and 0 <= limit <= 128 * 1024 * 1024
            and (budget_tag is None or type(budget_tag) is str)
        )
        identity = id(content)
        previous = memo.images.get(identity) if reusable else None
        descriptor = (*named, identity, limit, budget_tag)
        retained = previous is not None and previous[0] is content
        if retained and descriptor in memo.descriptors:
            return
        digest_value = previous[1] if retained else sha(content)
        collector.observe_file(
            *named,
            kind="file",
            sha256=digest_value,
            size=len(content),
            limit=limit,
            budget_tag=budget_tag,
        )
        if reusable:
            if (
                not retained
                and len(memo.images) < _OBSERVED_IMAGE_LIMIT
                and memo.retained_bytes + len(content) <= _OBSERVED_BYTE_LIMIT
            ):
                # The strong reference makes integer object-id reuse impossible.
                memo.images[identity] = (content, digest_value)
                memo.retained_bytes += len(content)
                retained = True
            if retained and len(memo.descriptors) < _OBSERVED_IMAGE_LIMIT:
                memo.descriptors.add(descriptor)


def parse_registry_state(observation: RegistryObservation, *, path: Path) -> Record:
    """Strictly parse BODY only at this explicitly typed registry boundary."""
    state = _parse_json(observation.body_bytes, path)
    _compact_registry(state)
    return state


_owned_program_locks: ContextVar[dict[str, str] | None] = ContextVar(
    "owned_program_locks", default=None
)


def control_receipt_refs(state: Record) -> list[tuple[Record, str | None]]:
    """Enumerate semantic controls; an attachment's suffix never grants authority."""
    refs = list(coverage._acceptance_refs(state))
    task_ids = {str(task["id"]) for task in sequence(state["tasks"], "tasks")}
    for value in sequence(state["tasks"], "tasks"):
        task = record(value, "task")
        identifier = str(task["id"])
        for value in sequence(task.get("attempts", []), "attempts"):
            evidence = sequence(record(value, "attempt").get("evidence", []), "evidence")
            if evidence:
                refs.append((record(evidence[0], "revalidation reference"), identifier))
        for value in sequence(task.get("deliveries", []), "deliveries"):
            refs.append((record(record(value, "delivery")["receipt"], "receipt"), identifier))
    for value in sequence(state.get("events", []), "events"):
        event = record(value, "event")
        identifier = str(event.get("id")) if event.get("id") in task_ids else None
        for field in ("receipt", "review"):
            value = event.get(field)
            if isinstance(value, dict) and set(value) == {"path", "sha256"}:
                refs.append((value, identifier))
    for value in sequence(
        record(state.get("verification", {}), "verification").get("records", []),
        "verification records",
    ):
        item = record(value, "verification record")
        refs.append(
            (
                record(item["receipt"], "verification receipt"),
                str(record(item["input"], "input")["task_id"]),
            )
        )
    unique: dict[str, tuple[Record, str | None]] = {}
    for ref, identifier in refs:
        unique.setdefault(canonical(ref).decode(), (ref, identifier))
    return list(unique.values())


def verify_provenance(
    program: Program, state: Record, budget: ReadBudget | None = None
) -> list[Record]:
    """Retain exact transitive provenance only through explicitly typed control fields."""
    reader = budget if budget is not None else ReadBudget(64 * 1024 * 1024)
    facts: list[Record] = []
    validated_manifests: set[tuple[str, str]] = set()
    verification_reads = verification._reads(reader)
    verification_controls = {
        digest(record(value, "verification record")["receipt"]): record(
            value, "verification record"
        )
        for value in sequence(
            record(state.get("verification", {}), "verification").get("records", []), "records"
        )
    }
    first_controls: dict[bytes, Record] = {}
    for value in sequence(
        record(state.get("verification", {}), "verification").get("records", []), "records"
    ):
        control = record(value, "verification record")
        first_controls.setdefault(canonical(control["receipt"]), control)
    round_indexes: dict[str, dict[bytes, Record]] = {}
    for ref, identifier in control_receipt_refs(state):
        captured = reader.capture(program.directory, str(ref["path"]), 8 * 1024 * 1024)
        require(
            captured.sha256 == ref["sha256"], "Control receipt changed during provenance validation"
        )
        receipt = reader.json(captured)
        if "evidence_provenance_ref" not in receipt:
            continue
        manifest_ref = record(receipt["evidence_provenance_ref"], "provenance reference")
        require(set(manifest_ref) == {"path", "sha256"}, "Invalid provenance reference")
        program.verify_refs([manifest_ref], reader)
        manifest = reader.json(
            reader.capture(program.directory, str(manifest_ref["path"]), 8 * 1024 * 1024)
        )
        expected_task = identifier or "_coordination"
        expected_attempt = receipt.get("attempt", 1 if identifier is None else None)
        if expected_attempt is None and identifier is not None:
            matching = first_controls.get(canonical(ref))
            if matching is not None:
                expected_attempt = record(matching["context"], "context").get("attempt")
        require(
            manifest.get("program_id") == state["program_id"]
            and manifest.get("task_id") == expected_task
            and manifest.get("attempt") == expected_attempt,
            "Provenance consuming subject mismatch",
        )
        require(
            str(manifest_ref["path"]).startswith(
                f"evidence/{expected_task}/attempt-{expected_attempt}/"
            ),
            "Provenance manifest escaped consuming attempt",
        )
        manifest_key = (str(manifest_ref["path"]), str(manifest_ref["sha256"]))
        if manifest_key not in validated_manifests:
            flow_module("evidence_reuse").validate_manifest(program, manifest, budget=reader)
            validated_manifests.add(manifest_key)
        current = None
        if identifier is not None:
            task = program.task(state, identifier)
            latest = program.current_attempt(task)
            current = latest["number"] == expected_attempt
            source = digest(verification_reads.snapshot(program, strings(latest["scope"], "scope")))
            rounds = sequence(latest["rounds"], "rounds")
            if identifier not in round_indexes:
                indexed: dict[bytes, Record] = {}
                for value in rounds:
                    entry = record(value, "round")
                    for kind in ("receipt", "review"):
                        if kind in entry:
                            indexed[canonical(entry[kind])] = entry
                round_indexes[identifier] = indexed
            matched = round_indexes[identifier].get(canonical(ref))
            if matched is not None:
                current = current and source == matched["source_digest"] and matched == rounds[-1]
            verification_control = verification_controls.get(digest(ref))
            if verification_control is not None:
                try:
                    applicable, _ = verification._applicable(
                        program, state, verification_control, verification_reads
                    )
                    current = bool(current and applicable)
                except (ProgramError, OSError, planning.PlanningError, coverage.CoverageError):
                    current = None
        facts.append(
            {
                "manifest_ref": manifest_ref,
                "manifest": manifest,
                "control_ref": ref,
                "task_id": expected_task,
                "attempt": expected_attempt,
                "current": current,
            }
        )
    verification_reads.recheck(program)
    return facts


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


def _compact_registry(state: Record) -> bool:
    """Validate only the explicit registry marker; generic artifacts stay unchanged."""
    if "registry_format" not in state:
        return False
    marker = state["registry_format"]
    require(
        isinstance(marker, dict)
        and set(marker) == {"schema_version", "encoding"}
        and type(marker["schema_version"]) is int
        and marker["schema_version"] == 1
        and marker["encoding"] == "compact-ascii",
        "Unsupported registry format marker",
    )
    return True


def registry_body(state: Record) -> bytes:
    """One versioned registry serializer; never use it for immutable artifacts."""
    if not _compact_registry(state):
        return canonical(state)
    try:
        return (
            json.dumps(
                state, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False
            )
            + "\n"
        ).encode()
    except (ValueError, RecursionError) as exc:
        raise ProgramError(f"Invalid registry JSON: {exc}") from exc


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


def _safe_relative(name: str) -> str:
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


@lru_cache(maxsize=4096)
def _cached_safe_relative(name: str) -> str:
    return _safe_relative(name)


def safe_relative(name: str) -> str:
    # Only bounded plain strings retain lexical results; filesystem facts are never cached.
    if type(name) is str and len(name) <= 1024:
        return _cached_safe_relative(name)
    return _safe_relative(name)


def safe_path(root: Path, name: str) -> Path:
    return _checked_safe_path(root, name)


def _checked_safe_path(root: Path, name: str, components: _ClosingComponents | None = None) -> Path:
    # Lexically excluded names are represented by explicit scope exclusions.
    safe_relative(name)
    try:
        return _admit_safe_path(root, name, components)
    except (ProgramError, OSError):
        observe_path_error(root, "path_admission_unavailable")
        raise


def _component_check(path: Path) -> tuple[bool, bool]:
    """Return the link result and whether a fresh metadata image was available."""
    try:
        metadata = path.lstat()
    except (OSError, ValueError):
        return path.is_symlink() or getattr(path, "is_junction", lambda: False)(), False
    linked = stat.S_ISLNK(metadata.st_mode) or (
        getattr(path, "is_junction", None) is not None
        and (
            getattr(metadata, "st_file_attributes", 0)
            & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
        )
        != 0
        and getattr(metadata, "st_reparse_tag", None)
        == getattr(stat, "IO_REPARSE_TAG_MOUNT_POINT", None)
    )
    return linked, True


def _component_linked(path: Path) -> bool:
    """Inspect fresh metadata, retaining the runtime predicates' error semantics."""
    return _component_check(path)[0]


_ContainmentFlavor = Literal["windows", "posix"]
_CONTAINMENT_MAX_SPELLING = 2048
_CONTAINMENT_MAX_COMPONENTS = 64
_CONTAINMENT_MAX_DESCRIPTORS = 4096


def _containment_flavor(value: PurePath) -> _ContainmentFlavor | None:
    if type(value) in (PureWindowsPath, WindowsPath):
        return "windows"
    if type(value) in (PurePosixPath, PosixPath):
        return "posix"
    return None


@lru_cache(maxsize=_CONTAINMENT_MAX_DESCRIPTORS)
def _absolute_path_components(flavor: _ContainmentFlavor, spelling: str) -> tuple[str, ...] | None:
    # The caller bounds spelling; exact public parsers retain anchor/root semantics.
    value = PureWindowsPath(spelling.lower()) if flavor == "windows" else PurePosixPath(spelling)
    if not value.is_absolute():
        return None
    parts = value.parts
    return parts if len(parts) <= _CONTAINMENT_MAX_COMPONENTS else None


def _is_path_within(path: PurePath, other: PurePath) -> bool:
    """Use exact lexical components for known absolute builtins; delegate every other case."""
    kind = _containment_flavor(path)
    if kind is None or kind != _containment_flavor(other):
        return path.is_relative_to(other)
    spelling, parent_spelling = str(path), str(other)
    if (
        len(spelling) > _CONTAINMENT_MAX_SPELLING
        or len(parent_spelling) > _CONTAINMENT_MAX_SPELLING
    ):
        return path.is_relative_to(other)
    parts = _absolute_path_components(kind, spelling)
    parent = _absolute_path_components(kind, parent_spelling)
    if parts is None or parent is None:
        return path.is_relative_to(other)
    return len(parts) >= len(parent) and parts[: len(parent)] == parent


def _admit_safe_path(root: Path, name: str, components: _ClosingComponents | None = None) -> Path:
    relative = safe_relative(name)
    path = root / relative
    cursor = root
    for part in PurePosixPath(relative).parts:
        cursor /= part
        require(
            not (
                components.linked(cursor) if components is not None else _component_linked(cursor)
            ),
            f"Linked path rejected: {name}",
        )
    resolved = path.resolve()
    require(_is_path_within(resolved, root.absolute()), f"Path outside root: {name}")
    # Canonical candidate equality also proves its root/ancestors are unredirected.
    # Reject late inward links too; mere containment could accept that race window.
    require(resolved == path.absolute(), f"Linked or noncanonical path rejected: {name}")
    collector = _active_collector()
    if collector is not None:
        collector.observe_admission(root, name)
    return path


def bounded_bytes(path: Path, limit: int) -> bytes:
    """Bound allocations even if a regular file grows between metadata and reading."""
    try:
        chunks: list[bytes] = []
        remaining = limit + 1
        with path.open("rb") as stream:
            while remaining > 0:
                chunk = stream.read(min(64 * 1024, remaining))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
        content = b"".join(chunks)
        if len(content) > limit:
            observe_path_error(path, "bounded_read_unavailable")
        require(len(content) <= limit, f"File exceeds bounded read: {path.name}")
    except OSError:
        observe_path_error(path, "bounded_read_unavailable")
        raise
    _observe_bytes(path, content, limit)
    return content


@dataclass(frozen=True, slots=True)
class CapturedBytes:
    """Immutable byte image; it is not authority to read its pathname in a later epoch."""

    path: Path
    content: bytes
    sha256: str


@dataclass(frozen=True, slots=True)
class _ProvenanceRead:
    path: Path
    content: bytes
    limit: int


@dataclass(slots=True)
class ReadBudget:
    """One observation ledger; mutable admission and pure-result indexes die with this epoch."""

    remaining: int = 72 * 1024 * 1024
    cached: dict[Path, bytes] | None = None
    _images: dict[Path, CapturedBytes] = field(default_factory=dict, init=False)
    _limits: dict[Path, int] = field(default_factory=dict, init=False)
    _admissions: dict[tuple[str, str], CapturedBytes] = field(default_factory=dict, init=False)
    _documents: dict[Path, tuple[CapturedBytes, Record]] = field(default_factory=dict, init=False)
    _provenance_reads: dict[Path, _ProvenanceRead] | None = field(default=None, init=False)
    _held_initial: _InitialImages | None = field(default=None, init=False)

    def _held_alias(self, root: Path, name: str) -> tuple[Path, bytes, str] | None:
        held = self._held_initial
        key = held.key(root, name) if held is not None else None
        return held.aliases.get(key) if key is not None else None

    def _observed_alias(self, root: Path, name: str) -> CapturedBytes | None:
        """A held alias is useful only to an explicitly enrolled initial reader."""
        observed = self._admissions.get((str(root.absolute()), name))
        if observed is not None:
            return observed
        held = self._held_alias(root, name)
        return CapturedBytes(*held) if held is not None else None

    def _import_initial(self, root: Path, name: str, limit: int) -> CapturedBytes | None:
        held = self._held_alias(root, name)
        if held is None:
            return None
        path, content, checksum = held
        require(path == root / safe_relative(name), "Held input alias changed")
        # Charge this consumer independently even though the immutable image is shared.
        self._charge_provenance((_ProvenanceRead(path, content, limit),))
        image = self._images.get(path)
        if image is None or image.content is not self.cached[path]:
            image = CapturedBytes(path, self.cached[path], checksum)
            self._images[path] = image
        self._admissions[(str(root.absolute()), name)] = image
        self._remember_provenance(path, image.content, limit)
        return image

    def _remember_provenance(self, path: Path, content: bytes, limit: int) -> None:
        if self._provenance_reads is not None:
            previous = self._provenance_reads.get(path)
            if previous is None or limit < previous.limit:
                self._provenance_reads[path] = _ProvenanceRead(path, content, limit)

    def _charge_provenance(self, reads: tuple[_ProvenanceRead, ...]) -> None:
        """Replay only exact checked provenance bytes into this consumer's own ledger."""
        if self.cached is None:
            self.cached = {}
        for checked in reads:
            path, content, limit = checked.path, checked.content, checked.limit
            if path in self.cached:
                require(self.cached[path] == content, "Prepared provenance observation changed")
                bound = limit
            else:
                bound = min(limit, self.remaining)
            if len(content) > bound:
                observe_path_error(path, "bounded_read_unavailable")
            require(len(content) <= bound, f"File exceeds bounded read: {path.name}")
            if path not in self.cached:
                _observe_bytes(path, content, bound)
                self.remaining -= len(content)
                self.cached[path] = content
            self._limits[path] = min(limit, self._limits.get(path, limit))
            _observe_bytes(path, content, limit, f"reader-{id(self)}")

    def read(self, path: Path, limit: int) -> bytes:
        if self.cached is None:
            self.cached = {}
        if path in self.cached:
            content = self.cached[path]
            if len(content) > limit:
                observe_path_error(path, "bounded_read_unavailable")
            require(len(content) <= limit, f"File exceeds bounded read: {path.name}")
            self._limits[path] = min(limit, self._limits.get(path, limit))
            _observe_bytes(path, content, limit, f"reader-{id(self)}")
            self._remember_provenance(path, content, limit)
            return content
        content = bounded_bytes(path, min(limit, self.remaining))
        self.remaining -= len(content)
        self.cached[path] = content
        self._limits[path] = min(limit, self._limits.get(path, limit))
        _observe_bytes(path, content, limit, f"reader-{id(self)}")
        self._remember_provenance(path, content, limit)
        return content

    def _image(self, path: Path, limit: int) -> CapturedBytes:
        content = self.read(path, limit)
        image = self._images.get(path)
        if image is None or image.content is not content:
            image = CapturedBytes(path, content, sha(content))
            self._images[path] = image
        return image

    def capture(self, root: Path, name: str, limit: int) -> CapturedBytes:
        """Admit one exact logical path; every consumer still checks its expected hash/subject."""
        key = (str(root.absolute()), name)
        image = self._admissions.get(key)
        if image is not None:
            if len(image.content) > limit:
                observe_path_error(image.path, "bounded_read_unavailable")
            require(len(image.content) <= limit, f"File exceeds bounded read: {image.path.name}")
            self._limits[image.path] = min(limit, self._limits.get(image.path, limit))
            _observe_bytes(image.path, image.content, limit, f"reader-{id(self)}")
            self._remember_provenance(image.path, image.content, limit)
            return image
        initial = self._import_initial(root, name, limit)
        if initial is not None:
            return initial
        return self._capture_admitted(root, name, safe_path(root, name), limit)

    def _capture_admitted(self, root: Path, name: str, path: Path, limit: int) -> CapturedBytes:
        """Finish a core source walk's existing safe_path admission without traversing it twice."""
        key = (str(root.absolute()), name)
        image = self._admissions.get(key)
        if image is not None:
            if len(image.content) > limit:
                observe_path_error(image.path, "bounded_read_unavailable")
            require(len(image.content) <= limit, f"File exceeds bounded read: {path.name}")
            self._limits[path] = min(limit, self._limits.get(path, limit))
            _observe_bytes(path, image.content, limit, f"reader-{id(self)}")
            self._remember_provenance(path, image.content, limit)
            return image
        require(path == root / safe_relative(name), "Captured path differs from logical admission")
        initial = self._import_initial(root, name, limit)
        if initial is not None:
            return initial
        require(observed_is_file(path), f"Missing frozen evidence: {name}")
        image = self._image(path, limit)
        self._admissions[key] = image
        held = self._held_initial
        alias = held.key(root, name) if held is not None else None
        if alias is not None:
            held.remember(alias, image)
        return image

    def json(self, captured: CapturedBytes) -> Record:
        """Strictly decode checked bytes once and isolate each mutable consumer value."""
        require(self._images.get(captured.path) is captured, "Foreign captured-byte observation")
        prior = self._documents.get(captured.path)
        if prior is None or prior[0] is not captured:
            prior = (captured, _parse_json(captured.content, captured.path))
            self._documents[captured.path] = prior
        return deepcopy(prior[1])

    def recheck(self, root: Path, limit: int | None = None) -> None:
        """Independently re-admit every observed path and compare freshly captured final bytes."""
        fresh = ReadBudget(sum(len(raw) for raw in (self.cached or {}).values()))
        admitted: set[Path] = set()

        def bound(path: Path) -> int:
            original = self._limits.get(path, 8 * 1024 * 1024)
            return original if limit is None else min(original, limit)

        def recapture(base: Path, name: str, path: Path) -> CapturedBytes:
            try:
                return fresh.capture(base, name, bound(path))
            except ProgramError as exc:
                raise ProgramError(
                    f"Observed source/evidence changed during snapshot; refresh. Recheck failed: {exc}"
                ) from exc

        for (logical_root, name), captured in self._admissions.items():
            current = recapture(Path(logical_root), name, captured.path)
            require(
                current.content == captured.content,
                "Observed source/evidence changed during snapshot; refresh",
            )
            admitted.add(captured.path)
        for path, raw in (self.cached or {}).items():
            if path not in admitted:
                require(path.is_relative_to(root), "Observed input outside recheck root")
                current = recapture(root, path.relative_to(root).as_posix(), path)
                require(
                    current.content == raw,
                    "Observed source/evidence changed during snapshot; refresh",
                )


def _initial_read_budget(remaining: int = 72 * 1024 * 1024) -> ReadBudget:
    """Enroll a known initial consumer, never a default or closing reader."""
    reader = ReadBudget(remaining)
    held = _initial_images.get()
    if held is not None and _active_collector() is held.collector:
        reader._held_initial = held
    return reader


@dataclass(frozen=True, slots=True)
class _ValidatedState:
    """Strict first-pass facts for the identical state retained by one read-only host."""

    state: Record
    governance: coordination._CheckedGovernance
    presentation: presentation._CheckedLedger
    batch_history: _CheckedHistory | None
    retained_provenance: tuple[Record, ...] | None = None
    provenance_reads: tuple[_ProvenanceRead, ...] | None = None


@dataclass(frozen=True, slots=True)
class _PreparedState:
    state: Record
    checked: _ValidatedState
    body: bytes
    views: dict[str, str]
    normalized_body: bytes


_prepared_save_facts: ContextVar[list[_PreparedState] | None] = ContextVar(
    "prepared_save_facts", default=None
)


@dataclass(frozen=True, slots=True)
class _ProposedCapture:
    prepared: _PreparedState
    original: RegistryObservation


@dataclass(frozen=True, slots=True)
class _StagedModel:
    model_id: str
    model: Record
    prepared: _PreparedState


@dataclass(frozen=True, slots=True)
class _TaskIndex:
    """Derived lookup data for one captured state; never retained across reads or mutations."""

    tasks: dict[str, Record]
    positions: dict[str, int]
    groups: dict[str, frozenset[str]]
    scopes: dict[str, tuple[tuple[str, str, str], ...]]
    group_owners: dict[str, set[str]]
    path_owners: dict[str, set[str]]
    descendants: dict[str, set[str]]

    @classmethod
    def capture(cls, tasks: list[Record]) -> _TaskIndex:
        by_id: dict[str, Record] = {}
        positions: dict[str, int] = {}
        groups: dict[str, frozenset[str]] = {}
        scopes: dict[str, tuple[tuple[str, str, str], ...]] = {}
        group_owners: dict[str, set[str]] = {}
        path_owners: dict[str, set[str]] = {}
        descendants: dict[str, set[str]] = {}
        for position, task in enumerate(tasks):
            identifier = string(task["id"], "task ID")
            by_id[identifier] = task
            positions[identifier] = position
            groups[identifier] = frozenset(strings(task["conflict_groups"], "groups"))
            selected = (
                strings(Program.current_attempt(task)["scope"], "scope")
                if task["status"] in OWNED_STATES
                else strings(task["candidate_paths"], "candidate paths")
            )
            scopes[identifier] = tuple(
                (path, path.casefold(), path.casefold().rstrip("/") + "/") for path in selected
            )
            if task["status"] not in OWNED_STATES:
                continue
            for group in groups[identifier]:
                group_owners.setdefault(group, set()).add(identifier)
            for _, folded, _ in scopes[identifier]:
                path_owners.setdefault(folded.rstrip("/"), set()).add(identifier)
                for offset, character in enumerate(folded):
                    if character == "/":
                        descendants.setdefault(folded[:offset].rstrip("/"), set()).add(identifier)
        return cls(by_id, positions, groups, scopes, group_owners, path_owners, descendants)

    def task(self, identifier: str) -> Record:
        require(identifier in self.tasks, f"Unknown task: {identifier}")
        return self.tasks[identifier]

    def conflicts(self, identifier: str) -> list[Record]:
        """Keep task order and original directional reasons while pruning unrelated owners."""
        candidates: set[str] = set()
        for group in self.groups[identifier]:
            candidates.update(self.group_owners.get(group, ()))
        for _, folded, _ in self.scopes[identifier]:
            candidates.update(self.path_owners.get(folded.rstrip("/"), ()))
            candidates.update(self.descendants.get(folded.rstrip("/"), ()))
            for offset, character in enumerate(folded):
                if character == "/":
                    candidates.update(self.path_owners.get(folded[:offset].rstrip("/"), ()))
        candidates.discard(identifier)
        result: list[Record] = []
        for other in sorted(candidates, key=self.positions.__getitem__):
            reasons = set(self.groups[identifier] & self.groups[other])
            for left, left_folded, left_prefix in self.scopes[identifier]:
                for right, right_folded, right_prefix in self.scopes[other]:
                    if (
                        left_folded == right_folded
                        or left_folded.startswith(right_prefix)
                        or right_folded.startswith(left_prefix)
                    ):
                        reasons.add(f"scope: {left} / {right}")
            if reasons:
                result.append({"id": other, "reasons": sorted(reasons)})
        return result


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


def _parse_json(content: bytes, path: Path) -> Record:
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
            content.decode("utf-8"),
            object_pairs_hook=unique,
            parse_constant=finite_json,
            parse_float=finite_float,
        )
    except (UnicodeError, ValueError, RecursionError) as exc:
        raise ProgramError(f"Invalid JSON in {path.name}: {exc}") from exc
    return record(value, path.name)


def read_json(path: Path, limit: int = 8 * 1024 * 1024, budget: ReadBudget | None = None) -> Record:
    if budget is None:
        return _parse_json(bounded_bytes(path, limit), path)
    return budget.json(budget._image(path, limit))


def read_json_frozen(
    path: Path, limit: int = 1024 * 1024, budget: ReadBudget | None = None
) -> tuple[Record, str]:
    """Return strict metadata and the digest of exactly the same bounded byte image."""
    budget = budget if budget is not None else ReadBudget(limit)
    captured = budget._image(path, limit)
    return read_json(path, limit=limit, budget=budget), captured.sha256


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
        if temporary is not None and observed_exists(temporary):
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
                not observed_exists(self.path("RELOCATED.json"))
                and not observed_exists(self.path("RELOCATION_INCOMPLETE.json")),
                "Succession recovery cannot bypass relocation guards",
            )
        elif relocation_recovery:
            require(
                observed_is_file(self.path("RELOCATION_INCOMPLETE.json"))
                and not observed_exists(self.path("RELOCATED.json")),
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
            lock_digest = sha(bounded_bytes(path, 64 * 1024))
            owned = dict(_owned_program_locks.get() or {})
            owned[str(self.directory.absolute())] = lock_digest
            token = _owned_program_locks.set(owned)
            try:
                yield
            finally:
                _owned_program_locks.reset(token)
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
        if observed_exists(marker):
            destination = read_json(marker, limit=64 * 1024).get("destination")
            raise ProgramError(f"Program archived after relocation; explicitly use {destination}")
        require(
            not observed_exists(self.path("RELOCATION_INCOMPLETE.json")),
            "Relocation publication incomplete; inspect its recovery receipt",
        )

    def current_head(self) -> str:
        collector = _active_collector()
        try:
            head = git(self.repo, ["rev-parse", "HEAD"])
        except (ProgramError, OSError):
            if collector is not None:
                collector.ineligible("git_head_unavailable")
            raise
        if collector is not None:
            collector.observe_head(self.repo, head)
        return head

    def source_digest(self, scope: list[str]) -> str:
        return digest(self.source_snapshot(scope))

    def pending_questions(
        self, state: Record, objective_id: str, task_ids: list[str]
    ) -> list[Record]:
        return questions.pending_questions(state, objective_id, task_ids)

    def source_snapshot(
        self,
        scopes: list[str],
        budget: ReadBudget | None = None,
        *,
        _boundaries: list[Json] | None = None,
    ) -> Record:
        """Retain failure evidence when an ordinary advisory caller catches a partial scope."""
        try:
            return self._source_snapshot(scopes, budget, _boundaries=_boundaries)
        except (ProgramError, OSError):
            observe_path_error(self.repo, "source_scope_unavailable")
            raise

    def _source_snapshot(
        self,
        scopes: list[str],
        budget: ReadBudget | None = None,
        *,
        _boundaries: list[Json] | None = None,
    ) -> Record:
        require(bool(scopes), "At least one explicit scope is required")
        snapshot: Record = {}
        boundaries: list[Json] = []
        total_bytes = 0
        reader = budget if budget is not None else ReadBudget(128 * 1024 * 1024)

        def fingerprint_file(path: Path) -> str:
            nonlocal total_bytes
            require(len(snapshot) < 10000, "Explicit source scope exceeds fingerprint budget")
            captured = reader._capture_admitted(
                self.repo,
                path.relative_to(self.repo).as_posix(),
                path,
                128 * 1024 * 1024 - total_bytes,
            )
            total_bytes += len(captured.content)
            return captured.sha256

        for scope in sorted(scopes):
            require(
                not any(part.casefold() == ".work" for part in PurePosixPath(scope).parts),
                "Private .work paths cannot be source scopes",
            )
            safe_relative(scope)
            observed = reader._observed_alias(self.repo, scope)
            path = observed.path if observed is not None else safe_path(self.repo, scope)
            require(
                path != self.repo and not _is_path_within(self.directory, path),
                "Root/program-wide claims are invalid",
            )
            if observed is not None:
                snapshot[scope] = fingerprint_file(path)
            elif not observed_exists(path):
                snapshot[scope] = "missing"
            elif observed_is_file(path):
                snapshot[scope] = fingerprint_file(path)
            else:
                snapshot[scope + "/"] = "directory"
                for folder, dirs, files in os.walk(path, followlinks=False):
                    folder_path = Path(folder)
                    for name in sorted(dirs):
                        if name.casefold() in SKIP_DIRS:
                            relative = (folder_path / name).relative_to(self.repo).as_posix()
                            boundaries.append({"path": relative, "kind": "excluded_directory"})
                    dirs[:] = sorted(name for name in dirs if name.casefold() not in SKIP_DIRS)
                    for name in list(dirs):
                        relative = (folder_path / name).relative_to(self.repo).as_posix()
                        try:
                            safe_path(self.repo, relative)
                        except ProgramError:
                            boundaries.append({"path": relative, "kind": "excluded_path"})
                            dirs.remove(name)
                    for name in sorted(files):
                        relative = (folder_path / name).relative_to(self.repo).as_posix()
                        observed = reader._observed_alias(self.repo, relative)
                        if observed is None:
                            try:
                                candidate = safe_path(self.repo, relative)
                            except ProgramError:
                                boundary = (
                                    str(PurePosixPath(relative).parent / ".env*")
                                    if name.casefold().startswith(".env")
                                    else relative
                                )
                                boundaries.append({"path": boundary, "kind": "excluded_path"})
                                continue
                            require(
                                observed_is_file(candidate),
                                f"Not a regular scoped file: {relative}",
                            )
                        else:
                            candidate = observed.path
                        snapshot[relative] = fingerprint_file(candidate)
        boundaries.sort(
            key=lambda item: (
                str(record(item, "scope boundary")["path"]),
                str(record(item, "scope boundary")["kind"]),
            )
        )
        if _boundaries is not None:
            _boundaries.extend(boundaries)
        collector = _active_collector()
        if collector is not None:
            if len(boundaries) > 10000:
                collector.ineligible("scope_boundary_bound")
            collector.observe_scope(
                self.repo,
                scopes,
                snapshot,
                boundaries,
                byte_budget=128 * 1024 * 1024,
                entry_limit=10000,
            )
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
            require(
                observed_is_file(path) and observed_size(path) > 0,
                f"Missing/empty evidence: {name}",
            )
            require(observed_size(path) <= 8 * 1024 * 1024, f"Evidence exceeds 8 MiB: {name}")
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
            captured = budget.capture(self.directory, name, 8 * 1024 * 1024)
            require(
                captured.sha256 == ref.get("sha256"),
                f"Frozen evidence changed: {name}",
            )

    def read_registry_observation(self, *, budget: ReadBudget | None = None) -> RegistryObservation:
        """Read the physical canonical registry; generic artifact helpers stay raw."""
        path = self.path("backlog.json")
        require(observed_is_file(path), "Program registry is unavailable")
        limit = 8 * 1024 * 1024 + 80
        raw = (
            bounded_bytes(path, limit)
            if budget is None
            else budget.capture(self.directory, "backlog.json", limit).content
        )
        try:
            return flow_module("storage").decode_registry(raw)
        except flow_module("storage").RegistryFormatError as exc:
            raise ProgramError(f"Invalid registry storage: {exc.code}") from exc

    def read_state_bytes(self, *, budget: ReadBudget | None = None) -> bytes:
        """Export exact logical BODY without rewriting its whitespace or history."""
        return self.read_registry_observation(budget=budget).body_bytes

    def load(self) -> Record:
        self.require_current_location()
        state = parse_registry_state(
            self.read_registry_observation(), path=self.path("backlog.json")
        )
        self.validate(state)
        return state

    def validate(
        self, state: Record, verify_artifacts: bool = True, *, prospective: bool = False
    ) -> _ValidatedState:
        _compact_registry(state)
        budget = _initial_read_budget() if verify_artifacts else None
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
        checked_governance = coordination.validate_governance(state)
        phases.validate(state)
        coverage.validate(state)
        checked_presentation = presentation.validate(state)
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
        verification.validate(state)
        retained_provenance = None
        provenance_reads = None
        if verify_artifacts:
            verification.verify_history(self, state, budget)
            require(budget is not None, "Missing strict validation byte ledger")
            previous_reads = budget._provenance_reads
            budget._provenance_reads = {}
            try:
                retained_provenance = tuple(deepcopy(verify_provenance(self, state, budget)))
                provenance_reads = tuple(budget._provenance_reads.values())
            finally:
                budget._provenance_reads = previous_reads
        checked_history = None
        if any(
            record(value, "event").get("action") == "record_batch"
            for value in sequence(state["events"], "events")
        ):
            batches = flow_module("batch")
            checked_history = batches._check_history(state)
            if verify_artifacts:
                self.verify_refs(batches._history_references(checked_history), budget)
        if "review_handoffs" in state:
            handoffs = flow_module("review_handoffs")
            handoffs.validate(state)
            if verify_artifacts:
                self.verify_refs(handoffs.evidence_references(state), budget)
                authoring.verify_review_input_refs(self, state, budget)
        return _ValidatedState(
            state,
            checked_governance,
            checked_presentation,
            checked_history,
            retained_provenance,
            provenance_reads,
        )

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
            not observed_exists(self.path("backlog.json")),
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
                observed_is_file(path)
                and bounded_bytes(path, 8 * 1024 * 1024) == data.encode("utf-8"),
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
        verification.validate_receipt_refs(self, state, receipt, args.id)
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
        verification.validate_receipt_refs(self, state, review, args.id)
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
        if verdict == "accepted":
            verification.validate_completion(self, state, args.id)
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
        return self._assess_delivery_metadata(task, attempt, receipt, budget=budget)[0]

    def _assess_delivery_metadata(
        self, task: Record, attempt: Record, receipt: Record, *, budget: ReadBudget | None = None
    ) -> tuple[str, Record | None]:
        """Retain the actual provider assessment produced by ordinary metadata validation."""
        assessment: Record | None = None
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
        if (
            receipt.get("provider_observation") is not None
            or receipt.get("provider_context") is not None
        ):
            assessment = authoring.provider_execution(self, receipt, budget)
            if (
                assessment["adopted"]
                and assessment["status"] != "passed"
                and status == "ci_recorded_success"
            ):
                status = "ci_failed" if assessment["status"] == "failed" else "ci_pending"
        return status, assessment

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
        checked = self.validate(state)
        encoded = registry_body(state)
        require(
            len(encoded) <= 8 * 1024 * 1024,
            "Canonical registry exceeds 8 MiB; existing state preserved",
        )
        derived = self.views(state)
        require(
            all(len(text.encode("utf-8")) <= 8 * 1024 * 1024 for text in derived.values()),
            "Derived document exceeds 8 MiB; existing state preserved",
        )
        observer_module = flow_module("read_model") if _active_collector() is not None else None
        token = observer_module.collect(None) if observer_module is not None else nullcontext()
        with token:
            for name in ("backlog.json", *derived):
                destination = self.path(name)
                require(
                    not observed_exists(destination) or observed_is_file(destination),
                    f"Publication output is not a regular file: {name}",
                )
                for parent in destination.parents:
                    require(
                        not observed_exists(parent) or observed_is_dir(parent),
                        f"Publication output parent is not a directory: {name}",
                    )
                    if parent == self.directory:
                        break
        sink = _prepared_save_facts.get()
        if sink is not None:
            normalized = canonical(state) if _compact_registry(state) else encoded
            sink.append(_PreparedState(state, checked, encoded, derived, normalized))
        return encoded

    def save(
        self,
        state: Record,
        *,
        before_publish: Callable[[], None] | None = None,
    ) -> None:
        verification_guard = verification.publication_guard(self, state)
        self._publish(state, verification_guard, before_publish=before_publish)

    def _publish(
        self,
        state: Record,
        verification_guard: Callable[[], None] | None,
        *,
        before_publish: Callable[[], None] | None = None,
        _registry_backup: tuple[str, bytes] | None = None,
    ) -> None:
        """Shared canonical publication after ordinary or exact projected verification."""
        self.require_current_location()
        reliance_guard = coverage.publication_guard(self, state)
        provider_guard = authoring.delivery_publication_guard(self, state)
        initial = (
            self.read_registry_observation()
            if observed_is_file(self.path("backlog.json"))
            else None
        )
        original = (
            parse_registry_state(initial, path=self.path("backlog.json"))
            if initial is not None
            else None
        )
        if original is not None and ("review_handoffs" in original or "review_handoffs" in state):
            flow_module("review_handoffs").validate_append(original, state)
        if initial is not None and initial.model_id is not None:
            encoded, staged_model = _prepare_model_publication(self, state, initial)
        else:
            encoded = self.prepare_save(state)
            staged_model = None
        if before_publish is not None:
            before_publish()
        if reliance_guard is not None:
            reliance_guard()
        if verification_guard is not None:
            verification_guard()
        if provider_guard is not None:
            provider_guard()
        authoring.recheck_new_handoff_inputs(self, original, state)
        _assert_registry_storage(self, initial)
        if staged_model is not None:
            _close_staged_model(self, staged_model)
            _assert_registry_storage(self, initial)
            _assert_owned_lock(self)
            require(
                flow_module("storage").decode_registry(encoded).body_bytes == registry_body(state),
                "Proposed BODY changed before publication",
            )
        if _registry_backup is not None:
            _assert_registry_backup(self, *_registry_backup)
            _assert_registry_storage(self, initial)
        outcome = _MUTATION_OUTCOME.get()
        if outcome is not None:
            published_revision = flow_module("storage").decode_registry(encoded).body_sha256
            original_tasks = (
                {
                    str(record(value, "task")["id"]): value
                    for value in sequence(original["tasks"], "original tasks")
                }
                if original is not None
                else {}
            )
            outcome.update(
                program=self,
                before_revision=initial.body_sha256 if initial is not None else None,
                proposed_revision=published_revision,
                changed_task_ids=[
                    str(record(value, "task")["id"])
                    for value in sequence(state["tasks"], "tasks")
                    if original_tasks.get(str(record(value, "task")["id"])) != value
                ],
            )
        atomic_write(self.path("backlog.json"), encoded)
        if outcome is not None:
            outcome.update(status="committed", revision=published_revision)
        try:
            self.render(state)
        except (OSError, ProgramError) as exc:
            if outcome is not None:
                outcome.update(status="committed_view_failed", view_recovery_required=True)
            raise ProgramError(
                f"Registry committed at revision {phases.revision(state)}; derived rendering failed. Run render to recover views: {exc}"
            ) from exc

    def render(self, state: Record) -> None:
        for name, text in self.views(state).items():
            atomic_write(self.path(name), text.encode("utf-8"))

    def status(
        self, state: Record, next_only: bool = False, *, _index: _TaskIndex | None = None
    ) -> Record:
        tasks = [record(value, "task") for value in sequence(state["tasks"], "tasks")]
        active = [task for task in tasks if task["status"] in OWNED_STATES]
        index = _index if _index is not None else _TaskIndex.capture(tasks)
        goal = state.get("objective")
        members = (
            set(strings(record(goal, "objective")["task_ids"], "members"))
            if goal is not None and record(goal, "objective").get("membership_revision") is not None
            else None
        )
        candidates: list[Json] = []
        for task in planning.task_order(state):
            if task["status"] != "planned" or task.get("disposition") in {"split", "retired"}:
                continue
            if members is not None and task["id"] not in members:
                continue
            blocked = [
                dependency
                for dependency in strings(task["depends_on"], "depends_on")
                if index.task(dependency)["status"] != "done"
            ]
            collisions = index.conflicts(string(task["id"], "task ID"))
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


def read_snapshot(
    repo: Path,
    program_dir: Path,
    *,
    closure_plan: str | None = None,
    execution_capture: str | None = None,
    evidence_preparation: str | None = None,
    batch_plan: str | None = None,
) -> Record:
    return _read_projection(
        repo,
        program_dir,
        closure_plan=closure_plan,
        execution_capture=execution_capture,
        evidence_preparation=evidence_preparation,
        batch_plan=batch_plan,
    )


def read_brief(repo: Path, program_dir: Path, task_id: str | None = None) -> Record:
    return _read_projection(repo, program_dir, brief_only=True, task_id=task_id)


def _read_projection(
    repo: Path,
    program_dir: Path,
    *,
    brief_only: bool = False,
    task_id: str | None = None,
    closure_plan: str | None = None,
    execution_capture: str | None = None,
    evidence_preparation: str | None = None,
    batch_plan: str | None = None,
) -> Record:
    if (
        not brief_only
        and task_id is None
        and all(
            value is None
            for value in (closure_plan, execution_capture, evidence_preparation, batch_plan)
        )
    ):
        cached = _read_materialized_projection(repo, program_dir)
        if cached is not None:
            return cached
    observations = _SourceObservations()
    token = _source_observations.set(observations)
    try:
        return _capture_projection(
            repo,
            program_dir,
            observations,
            brief_only,
            task_id,
            closure_plan,
            execution_capture,
            evidence_preparation,
            batch_plan,
        )
    finally:
        _source_observations.reset(token)


def _capture_projection(
    repo: Path,
    program_dir: Path,
    source_observations: _SourceObservations,
    brief_only: bool,
    task_id: str | None,
    closure_plan: str | None = None,
    execution_capture: str | None = None,
    evidence_preparation: str | None = None,
    batch_plan: str | None = None,
    *,
    _proposed: _ProposedCapture | None = None,
) -> Record:
    """Return bounded recorded state without locks or writes; selected plans inspect local Git.

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
    initial_observation = program.read_registry_observation()
    if _proposed is None:
        state = parse_registry_state(initial_observation, path=backlog)
        captured_revision = initial_observation.body_sha256
    else:
        require(
            initial_observation.raw_bytes == _proposed.original.raw_bytes,
            "Registry changed before model preparation",
        )
        state = _proposed.prepared.state
        require(
            _proposed.prepared.checked.state is state
            and _proposed.prepared.checked.retained_provenance is not None
            and _proposed.prepared.checked.provenance_reads is not None
            and canonical(state) == _proposed.prepared.normalized_body,
            "Prepared state changed before projection",
        )
        captured_revision = sha(_proposed.prepared.body)
    _assert_registry_storage(program, initial_observation)
    tasks = [record(value, "task") for value in sequence(state.get("tasks"), "tasks")]
    require(len(tasks) <= 2000, "Snapshot exceeds 2000 tasks")
    try:
        checked_state = (
            program.validate(state, verify_artifacts=False)
            if _proposed is None
            else _proposed.prepared.checked
        )
    except (
        coordination.GovernanceError,
        phases.PhaseError,
        planning.PlanningError,
        questions.QuestionError,
    ) as exc:
        raise ProgramError(str(exc)) from exc
    # Capture source identities separately from their recorded hashes: an already
    # drifted source must not change again while its advisory projection is built.
    source_inputs = _initial_read_budget(64 * 1024 * 1024)
    unavailable_sources: set[Path] = set()
    source_names = {str(item["path"]) for item in planning.descriptors(state)}
    if state.get("planning_path"):
        source_names.add(str(state["planning_path"]))
    source_names.update(str(item["planning"]["path"]) for item in planning.intakes(state).values())
    for name in sorted(source_names):
        path = safe_path(repo, name)
        if observed_is_file(path):
            source_inputs.read(path, 8 * 1024 * 1024)
        else:
            unavailable_sources.add(path)
    try:
        warnings = planning.observe_sources(program, state)
    except planning.PlanningError as exc:
        raise ProgramError(str(exc)) from exc
    refs: dict[str, str] = {}
    observations: dict = {"submissions": {}, "blocks": {}, "recovery": [], "publication": []}
    publication_inputs = _initial_read_budget(64 * 1024 * 1024)
    absent_publication: set[Path] = set()
    for name in sorted(planning.PUBLICATION_MARKERS | {planning.TRACKING}):
        path = program.path(name)
        if observed_is_file(path):
            raw = publication_inputs.read(path, 8 * 1024 * 1024)
            observations["publication"].append(
                {"path": name, "sha256": sha(raw), "status": "observed"}
            )
        else:
            absent_publication.add(path)
    review_sources = _initial_read_budget(128 * 1024 * 1024)
    all_refs = (
        list(coordination.evidence_references(state))
        + list(questions.evidence_references(state))
        + phases.evidence_references(state)
        + planning.evidence_references(state)
        + coverage.evidence_references(state)
        + authoring.delivery_evidence_references(state)
        + presentation.evidence_references(state)
        + verification.evidence_references(state)
    )
    if "review_handoffs" in state:
        all_refs += flow_module("review_handoffs").evidence_references(state)
    if any(
        record(value, "event").get("action") == "record_batch"
        for value in sequence(state["events"], "events")
    ):
        all_refs += flow_module("batch")._history_references(checked_state.batch_history)
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
    evidence_budget = _initial_read_budget(64 * 1024 * 1024)
    unavailable_evidence: set[Path] = set()
    for name, expected in refs.items():
        path = program.path(name)
        if not observed_is_file(path):
            warnings.append(f"Frozen evidence unavailable: {name}")
            unavailable_evidence.add(path)
            continue
        captured = evidence_budget._capture_admitted(program.directory, name, path, 8 * 1024 * 1024)
        if captured.sha256 != expected:
            warnings.append(f"Frozen evidence changed: {name}")
    try:
        for name, text in program.views(state).items():
            path = program.path(name)
            if _proposed is not None:
                collector = _active_collector()
                require(
                    collector is not None, "Proposed projection requires its dependency collector"
                )
                require(_proposed.prepared.views.get(name) == text, "Proposed derived view changed")
                collector.expect_view(program.directory, name, text.encode("utf-8"))
            elif (
                not observed_is_file(path)
                or observed_size(path) > 8 * 1024 * 1024
                or bounded_bytes(path, 8 * 1024 * 1024) != text.encode("utf-8")
            ):
                warnings.append(f"Derived view drift: {name}; director may run render.")
    except OSError:
        observe_path_error(program.directory, "derived_views_unavailable")
        warnings.append("Derived views unavailable; recorded registry remains readable.")
    result_tasks: list[Json] = []
    if execution_capture is None:
        full_verification = verification.projection(program, state, evidence_budget, limit=None)
    else:
        capture_name = safe_relative(execution_capture)
        raw_capture = evidence_budget.read(program.path(capture_name), 8 * 1024 * 1024)
        full_verification = verification.projection(
            program,
            state,
            evidence_budget,
            limit=None,
            selected_capture_ref={"path": capture_name, "sha256": sha(raw_capture)},
        )
    verification_view = verification.bound_projection(full_verification)
    delivery_histories = _delivery_history_projection(state, captured_revision)
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
                    if observed_is_file(path) and observed_size(path) <= 1024 * 1024:
                        captured = evidence_budget.capture(
                            program.directory, str(ref["path"]), 1024 * 1024
                        )
                        if captured.sha256 == ref["sha256"]:
                            summary[kind + "_record"] = evidence_budget.json(captured)
                rounds.append(summary)
            history["rounds"] = rounds
            sequence(item["attempts"], "attempts").append(history)
        result_tasks.append(item)
    governance = coordination._snapshot_from_checked(state, checked_state.governance)
    categories = sequence(governance.get("categories", []), "categories")
    category_by_task: dict[str, Json] = {}
    for category_value in categories:
        category = record(category_value, "category")
        for identifier in sequence(category.get("task_ids", []), "category task_ids"):
            category_by_task.setdefault(string(identifier, "category task ID"), category["id"])
    for value in result_tasks:
        task = record(value, "task")
        task["category_id"] = category_by_task.get(
            string(task["id"], "task ID"), task["workstream"]
        )
    result: Record = {
        "snapshot_version": 1,
        "revision": captured_revision,
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
        "verification": verification_view,
        "coverage": coverage.projection(
            program, state, evidence_budget, verification_facts=full_verification
        ),
        "presentation": presentation._projection_from_checked(
            program,
            state,
            checked_state.presentation,
            budget=evidence_budget,
            revision=captured_revision,
        ),
    }
    for task_index, item in enumerate(result_tasks):
        task_item = record(item, "task")
        task_item["verification"] = deepcopy(
            record(verification_view.get("tasks", {}), "verification tasks").get(
                str(task_item["id"])
            )
        )
        original_task = tasks[task_index]
        current_delivery = (
            program.current_delivery(original_task) if original_task["status"] == "done" else {}
        )
        deliveries = [current_delivery] if current_delivery.get("metadata") is not None else []
        provider_ref = (
            record(record(deliveries[-1], "delivery")["metadata"], "metadata").get(
                "provider_observation"
            )
            if deliveries
            else None
        )
        provider_available = True
        if provider_ref is not None:
            frozen_provider = record(provider_ref, "provider reference")
            provider_path = program.path(str(frozen_provider["path"]))
            provider_available = (
                provider_path not in unavailable_evidence
                and sha((evidence_budget.cached or {}).get(provider_path, b""))
                == frozen_provider["sha256"]
            )
        task_item["provider_execution"] = (
            authoring.provider_execution(
                program,
                record(record(deliveries[-1], "delivery")["metadata"], "metadata"),
                evidence_budget,
            )
            if deliveries and provider_available
            else {
                "status": "unavailable" if not provider_available else "not_assessed",
                "remote_verified_by_tool": False,
            }
        )
        record(task_item["provider_execution"], "provider execution").update(
            {
                "task_id": task_item["id"],
                "reference": {
                    "path": "backlog.json",
                    "revision": captured_revision,
                    "pointer": f"/tasks/{task_index}/deliveries"
                    if deliveries
                    else "/verification/records",
                    "task_id": task_item["id"],
                },
                "provider_reference": deepcopy(provider_ref),
            }
        )
        if not deliveries:
            reference = record(task_item["provider_execution"], "provider execution")["reference"]
            task_item["provider_execution"] = {
                **authoring.early_provider_execution(
                    task_item["verification"], record(reference, "provider reference")
                ),
                "task_id": task_item["id"],
                "reference": reference,
            }
    handoff_producer = flow_module("review_handoffs")
    full_handoffs = authoring.assess_handoffs(
        program, state, source_budget=source_inputs, evidence_budget=evidence_budget
    )
    queue_producer = flow_module("queue")
    full_reviewer_load = queue_producer.project_capacity(
        state, full_handoffs, full_verification, limit=None
    )
    full_handoffs = queue_producer.guide_handoffs(
        state, full_handoffs, full_verification, reviewer_load=full_reviewer_load
    )
    result["review_handoffs"] = handoff_producer.bound_projection(full_handoffs, limit=50)
    result["reviewer_load"] = deepcopy(full_reviewer_load)
    reviewer_rows = result["reviewer_load"]["rows"]
    reviewer_rows.update(
        items=reviewer_rows["items"][:50],
        shown=min(50, reviewer_rows["total"]),
        omitted=max(0, reviewer_rows["total"] - 50),
    )
    latest_handoffs: Record = {}
    for value in full_handoffs["rows"]["items"]:
        item = record(value, "handoff row")
        identifier = str(item["task_id"])
        prior = latest_handoffs.get(identifier)
        if prior is None or item["recorded_sequence"] > prior["recorded_sequence"]:
            latest_handoffs[identifier] = item
    for value in result_tasks:
        item = record(value, "snapshot task")
        item["review_handoff"] = deepcopy(latest_handoffs.get(str(item["id"])))
    capture_facts: list[Record] = []
    capture_refs: Record = {}
    for value in sequence(
        record(state.get("verification", {}), "verification").get("records", []),
        "verification records",
    ):
        observed = record(record(value, "verification record")["input"], "verification input")
        capture_ref = observed.get("run_capture_ref")
        if capture_ref is not None:
            frozen_ref = record(capture_ref, "run capture reference")
            capture_refs[str(frozen_ref["path"])] = frozen_ref
    for capture_ref in capture_refs.values():
        try:
            capture = flow_module("execution")._read_capture(program, capture_ref, evidence_budget)
            capture_facts.append({"capture": capture, "reference": deepcopy(capture_ref)})
        except (ProgramError, OSError):
            warnings.append("Retained execution timing could not be verified")
    objective_binding = record(state.get("objective", {}), "objective")
    timing_binding = {"program_id": state["program_id"]}
    if objective_binding:
        timing_binding.update(
            {
                "objective_id": objective_binding["id"],
                "membership_revision": objective_binding.get("membership_revision"),
            }
        )
    result["timing"] = flow_module("timing").project(
        state,
        handoff_facts=full_handoffs,
        verification_facts={
            "schema_version": 1,
            "records": deepcopy(
                record(state.get("verification", {}), "verification").get("records", [])
            ),
            "applicability": {
                str(record(lane, "lane")["request_id"]): record(lane, "lane").get(
                    "applicability", "unavailable"
                )
                for value in record(
                    full_verification.get("tasks", {}), "verification tasks"
                ).values()
                for lane in sequence(
                    record(record(value, "task verification")["lanes"], "lanes")["items"], "lanes"
                )
            },
        },
        capture_facts=capture_facts,
        source_binding=timing_binding,
    )
    try:
        provenance = checked_state.retained_provenance
        provenance_reads = checked_state.provenance_reads
        if _proposed is not None and provenance is not None and provenance_reads is not None:
            # Reuse semantic facts, while preserving this projection's distinct byte bounds.
            # The writer still closes the complete model image after normal callbacks.
            evidence_budget._charge_provenance(provenance_reads)
            retained = deepcopy(list(provenance))
        else:
            retained = verify_provenance(program, state, evidence_budget)
        evidence_view = flow_module("evidence_reuse").project_retained(state, retained)
    except (ProgramError, OSError, planning.PlanningError, coverage.CoverageError):
        # Ordinary snapshots preserve recovery context on damaged retained artifacts.
        # Explicit selections and canonical publication remain strict; no old-ready fallback.
        warnings.append("Retained evidence provenance could not be verified")
        evidence_view = flow_module("evidence_reuse").summarize_preparation(
            {
                "schema_version": 1,
                "kind": "retained_evidence",
                "operation": "retained_evidence",
                "status": "unavailable",
                "reasons": ["unavailable_evidence"],
            }
        )
    if evidence_preparation is not None:
        selected = read_json(
            program.path(safe_relative(evidence_preparation)), budget=evidence_budget
        )
        assessed_selection = flow_module("evidence_reuse").validate_selection(
            program, state, selected, budget=evidence_budget
        )
        evidence_view = record(assessed_selection["summary"], "selected preparation summary")
        program.verify_refs(
            sequence(assessed_selection["refs"], "selected preparation refs"), evidence_budget
        )
    if evidence_view is not None:
        result["evidence_preparation"] = evidence_view
    batch_producer = flow_module("batch")
    batch_view = (
        batch_producer._project_latest_checked(checked_state.batch_history)
        if checked_state.batch_history is not None
        else batch_producer.project_latest_batch(state)
    )
    if batch_plan is not None:
        selected = read_json(program.path(safe_relative(batch_plan)), budget=evidence_budget)
        batch_view = flow_module("batch").project_selection(
            program, state, selected, authoring.BatchHost(program, identity_budget=evidence_budget)
        )
        selected_kind = selected.get("kind")
        if selected_kind is None:
            program.verify_refs(sequence(selected["refs"], "selected batch refs"), evidence_budget)
        elif selected_kind == "batch_publication_result":
            plan_ref = record(selected["plan_ref"], "selected plan reference")
            program.verify_refs([plan_ref], evidence_budget)
            plan_value = read_json(program.path(str(plan_ref["path"])), budget=evidence_budget)
            program.verify_refs(
                sequence(plan_value["refs"], "selected batch refs"), evidence_budget
            )
        else:
            program.verify_refs(
                [record(selected["request_ref"], "selected request reference")], evidence_budget
            )
    if batch_view is not None:
        result["record_batch"] = batch_view
    # Complete assessed task facts join complete lanes before the queue orders and bounds once.
    task_facts: Record = {}
    for item in result_tasks:
        task_item = record(item, "snapshot task")
        identifier = str(task_item["id"])
        observed = observations["submissions"].get(identifier, {"status": "not_assessed"})
        submission_fact = {
            **observed,
            "status": "current" if observed["status"] == "observed" else observed["status"],
        }
        attempts = sequence(task_item["attempts"], "snapshot attempts")
        rounds = record(attempts[-1], "latest attempt").get("rounds", []) if attempts else []
        latest = record(rounds[-1], "latest round") if rounds else {}
        receipt = record(latest.get("receipt_record", {}), "current receipt")
        task_facts[identifier] = {
            "submission": submission_fact,
            "checks": deepcopy(receipt.get("checks", [])),
        }
    full_verification["task_facts"] = task_facts
    integrated_review = record(result["coverage"]["integrated_review"], "integrated review")
    full_verification["integrated_review"] = integrated_review
    # Queue assessment needs full inputs; ordinary detached row display omits only
    # the new raw capture/manifest facts, as the bounded verification view does.
    queue_view = deepcopy(
        flow_module("queue").project(
            state,
            full_verification,
            handoff_facts=full_handoffs,
            integrated_review=integrated_review,
        )
    )
    for row_value in sequence(record(queue_view["rows"], "queue rows")["items"], "queue items"):
        context = record(row_value, "queue row").get("context")
        if isinstance(context, dict):
            context.pop("input_capture", None)
            context.pop("input_manifest", None)
    result["review_queue"] = queue_view
    result["closure"] = flow_module("closure").readiness(
        program,
        state,
        prevalidated=True,
        verification_facts=full_verification,
        integrated_review=integrated_review,
    )
    closure_view = record(result["closure"], "closure projection")
    closure_view["reference"] = {
        "path": "backlog.json",
        "revision": captured_revision,
        "pointer": "/objective",
    }
    if closure_plan is not None:
        plan_name = safe_relative(closure_plan)
        try:
            selected, plan_hash = read_json_frozen(program.path(plan_name), budget=evidence_budget)
            plan_view = flow_module("closure").project_plan(
                program,
                state,
                selected,
                authoring.ClosureHost(program, identity_budget=evidence_budget),
            )
            plan_view["reference"] = {"path": plan_name, "sha256": plan_hash}
            plan_view["observed_revision"] = captured_revision
            closure_view["plan"] = plan_view
        except (ProgramError, coordination.GovernanceError, OSError) as exc:
            selected_path = program.path(plan_name)
            if not observed_is_file(selected_path):
                unavailable_evidence.add(selected_path)
            closure_view["plan"] = {
                "schema_version": 1,
                "status": "unavailable",
                "preparation": {"status": "unavailable"},
                "preflight": {"status": "not_assessed", "current": False},
                "publication": {"status": "blocked"},
                "next_actor_id": coordination.current_director_id(state),
                "next_action": "inspect_integrity",
                "reasons": [str(exc)],
                "accepted": False,
                "remote_verified_by_tool": False,
                "reference": {"path": plan_name},
                "observed_revision": captured_revision,
            }
    lock = program.path(".feature-program.lock")
    lock_before: bytes | None = None
    lock_unavailable: str | None = None
    if _proposed is not None:
        observe_path_probe(lock, "exists", "false")
        observe_path_probe(lock, "kind", "missing")
    elif observed_exists(lock):
        try:
            lock_before = bounded_bytes(lock, 64 * 1024)
            read_json(lock, limit=64 * 1024, budget=ReadBudget(cached={lock: lock_before}))
            lock_status = "present"
        except (ProgramError, OSError) as exc:
            lock_status = "unavailable"
            lock_unavailable = str(exc)
            observe_path_error(lock, "lock_metadata_unavailable")
        observations["recovery"].append(
            {
                "path": ".feature-program.lock",
                "status": lock_status,
                "reason": "Recorded lock exists; liveness and takeover are unknown"
                if lock_status == "present"
                else "Lock metadata unavailable: " + str(lock_unavailable),
            }
        )
    result["integrity"] = {
        "status": "stale" if warnings else "fresh",
        "warnings": sorted(set(warnings)),
    }
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
        budget.recheck(repo)
    require(
        not any(
            observed_is_file(safe_path(repo, path.relative_to(repo).as_posix()))
            for path in unavailable_sources | unavailable_evidence | absent_publication
        ),
        "Unavailable source/evidence appeared during snapshot; refresh",
    )
    require(
        program.read_registry_observation().storage_sha256 == initial_observation.storage_sha256,
        "Registry changed during snapshot; refresh",
    )
    source_observations.verify()
    lock = program.path(".feature-program.lock")
    if _proposed is not None:
        pass  # Own lock is checked physically outside the virtual capture.
    elif lock_before is not None:
        require(
            observed_is_file(lock) and bounded_bytes(lock, 64 * 1024) == lock_before,
            "Lock changed during snapshot; refresh",
        )
    elif lock_unavailable is None:
        require(not observed_exists(lock), "Lock appeared during snapshot; refresh")
    program.require_current_location()
    require(
        program.read_registry_observation().storage_sha256 == initial_observation.storage_sha256,
        "Registry changed during snapshot; refresh",
    )
    return output


@dataclass(slots=True)
class _ClosingComponents:
    """One observer's metadata reuse; every seen name must finish with a fresh check."""

    seen: dict[str, Path] = field(default_factory=dict)
    nonlinked: set[str] = field(default_factory=set)

    def linked(self, path: Path) -> bool:
        key = str(path)
        self.seen.setdefault(key, path)
        if key in self.nonlinked:
            return False
        linked, captured = _component_check(path)
        if captured and not linked:
            self.nonlinked.add(key)
        return linked

    def finish(self) -> None:
        for path in self.seen.values():
            try:
                require(not _component_linked(path), "Closing component changed; refresh")
            except (ProgramError, OSError):
                observe_path_error(path, "path_admission_unavailable")
                raise


class _ClosingObserver:
    """One fresh physical image; all original logical admissions are still replayed."""

    def __init__(self, program: Program):
        self.program = program
        self.reader = ReadBudget(1024 * 1024 * 1024)
        self.allowed = {program.repo.absolute(), program.directory.absolute(), ROOT, ROOT / "tools"}
        self.admitted: dict[tuple[Path, str], Path] = {}
        self.normalized_roots: dict[Path, Path] = {}
        self.probes: dict[tuple[Path, str], str] = {}
        self.components = _ClosingComponents()
        self._observed_head: str | None = None

    def _admitted_path(self, root: Path, name: str) -> Path:
        """Retain one checked Path per original logical admission in this fresh image."""
        key = (root, name)
        path = self.admitted.get(key)
        if path is not None:
            return path
        absolute = self.normalized_roots.get(root)
        if absolute is None:
            absolute = root.absolute()
            if absolute not in self.allowed:
                bases = [base for base in self.allowed if absolute.is_relative_to(base)]
                require(bool(bases), "Unsupported dependency root")
                base = max(bases, key=lambda candidate: len(candidate.parts))
                # A narrower logical root needs one namespace admission. Every
                # distinct name below still rechecks that root's actual resolution.
                _checked_safe_path(base, absolute.relative_to(base).as_posix(), self.components)
            self.normalized_roots[root] = absolute
        path = _checked_safe_path(absolute, name, self.components)
        self.admitted[key] = path
        return path

    def finish(self) -> None:
        self.components.finish()
        if self._observed_head is not None:
            with flow_module("read_model").collect(None):
                require(
                    self.program.current_head() == self._observed_head,
                    "Git HEAD changed during read-model closing observation",
                )

    def head(self) -> str:
        with flow_module("read_model").collect(None):
            head = self.program.current_head()
        require(bool(re.fullmatch(r"[0-9a-f]{40}", head)), "Invalid current Git HEAD")
        if self._observed_head is not None:
            require(
                head == self._observed_head,
                "Git HEAD changed during read-model closing observation",
            )
        self._observed_head = head
        return head

    def admit(self, root: Path, name: str) -> None:
        self._admitted_path(root, name)

    def file(self, root: Path, name: str, *, limit: int, budget_tag: str | None) -> CapturedBytes:
        path = self._admitted_path(root, name)
        # Stored roots use normalized case. The ordinary source walker uses the
        # host repo spelling as its ledger key; equal Path roots must warm that
        # SAME logical source admission, not create a case-only second epoch.
        capture_root = (
            self.program.repo if root == self.program.repo else self.normalized_roots[root]
        )
        return self.reader._capture_admitted(capture_root, name, path, limit)

    def probe(self, root: Path, name: str, *, operation: str) -> str:
        path = self._admitted_path(root, name)
        key = (path, operation)
        if key not in self.probes:
            self.probes[key] = self._probe(path, operation)
        return self.probes[key]

    def _probe(self, path: Path, operation: str) -> str:
        if operation == "kind":
            try:
                mode = path.stat().st_mode
            except FileNotFoundError:
                return "missing"
            return "file" if stat.S_ISREG(mode) else "directory" if stat.S_ISDIR(mode) else "other"
        if operation == "size":
            return str(observed_size(path))
        require(operation in {"exists", "is_file", "is_dir"}, "Unknown dependency probe")
        value = getattr(path, operation)()
        return "true" if value else "false"

    def scope(
        self, root: Path, selectors: list[str], *, byte_budget: int, entry_limit: int
    ) -> tuple[Record, Json]:
        require(root == self.program.repo, "Source scope requires the bound repository")
        require(
            byte_budget == 128 * 1024 * 1024 and 0 < entry_limit <= 10000,
            "Invalid source scope bounds",
        )
        boundaries: list[Json] = []
        snapshot = self.program.source_snapshot(selectors, self.reader, _boundaries=boundaries)
        require(
            len(snapshot) <= entry_limit and len(boundaries) <= entry_limit,
            "Source scope entry limit changed",
        )
        return snapshot, boundaries

    def exclusions(self, root: Path, groups: Record, *, entry_limit: int) -> list[Json]:
        require(root == self.program.repo and 0 < entry_limit <= 10000, "Invalid exclusion scope")
        return flow_module("inputs")._exclusions(self.program, {"groups": groups})


class _ProspectiveObserver(_ClosingObserver):
    """Close real inputs while admitting only this checked writer's deterministic outputs."""

    def __init__(self, program: Program, prepared: _PreparedState):
        super().__init__(program)
        require(
            prepared.checked.state is prepared.state
            and canonical(prepared.state) == prepared.normalized_body,
            "Proposed publication proof changed",
        )
        self.views = {
            program.path(name): text.encode("utf-8") for name, text in prepared.views.items()
        }
        self.lock = program.path(".feature-program.lock")

    def file(self, root: Path, name: str, *, limit: int, budget_tag: str | None) -> CapturedBytes:
        path = self._admitted_path(root, name)
        if path in self.views:
            content = self.views[path]
            require(len(content) <= limit, "Proposed output exceeds consumer bound")
            return CapturedBytes(path, content, sha(content))
        return super().file(root, name, limit=limit, budget_tag=budget_tag)

    def _probe(self, path: Path, operation: str) -> str:
        if path == self.lock:
            require(
                operation in {"exists", "is_file", "is_dir", "kind"}, "Invalid future lock probe"
            )
            return "missing" if operation == "kind" else "false"
        if path in self.views:
            if operation == "kind":
                return "file"
            if operation == "size":
                return str(len(self.views[path]))
            return "false" if operation == "is_dir" else "true"
        return super()._probe(path, operation)


def _assert_registry_storage(program: Program, original: RegistryObservation | None) -> None:
    if original is None:
        require(
            not observed_exists(program.path("backlog.json")),
            "Registry appeared before publication",
        )
    else:
        require(
            program.read_registry_observation().raw_bytes == original.raw_bytes,
            "Registry storage changed; refresh before publication",
        )


def _assert_owned_lock(program: Program) -> None:
    expected = (_owned_program_locks.get() or {}).get(str(program.directory.absolute()))
    require(
        expected is not None, "Enabled storage publication requires the normal owned program lock"
    )
    path = program.path(".feature-program.lock")
    require(sha(bounded_bytes(path, 64 * 1024)) == expected, "Owned program lock changed")


def _prepare_model_publication(
    program: Program,
    state: Record,
    original: RegistryObservation,
    *,
    require_eligible: bool = False,
    preserve_body: bool = False,
) -> tuple[bytes, _StagedModel]:
    """Prepare without swapping canonical state or persisting the current writer lock."""
    _assert_owned_lock(program)
    module = flow_module("read_model")
    intended_body = original.body_bytes if preserve_body else registry_body(state)
    collector = module.Collector(
        program.repo,
        program.directory,
        program_id=str(state["program_id"]),
        body_sha256=sha(intended_body),
        producer=module.producer_identity(program.repo),
        query={"kind": "ordinary_snapshot", "version": 1},
    )
    facts: list[_PreparedState] = []
    token = _prepared_save_facts.set(facts)
    observations = _SourceObservations()
    source_token = _source_observations.set(observations)
    try:
        with (
            _collect_dependencies(collector),
            _hold_initial_images(program, original, collector, proposed_body=intended_body),
        ):
            body = program.prepare_save(state)
            require(
                len(facts) == 1 and facts[0].body == body,
                "Missing exact publication validation proof",
            )
            prepared = facts[0]
            if preserve_body:
                require(
                    registry_body(parse_registry_state(original, path=program.path("backlog.json")))
                    == body,
                    "Exact preserved BODY differs from proposed state",
                )
                prepared = _PreparedState(
                    state, prepared.checked, intended_body, prepared.views, prepared.normalized_body
                )
                body = intended_body
            snapshot = _capture_projection(
                program.repo,
                program.directory,
                observations,
                False,
                None,
                _proposed=_ProposedCapture(prepared, original),
            )
        model = collector.finish(snapshot)
    finally:
        _source_observations.reset(source_token)
        _prepared_save_facts.reset(token)
    if require_eligible:
        require(
            model.get("eligible") is True, "Storage enable requires a complete eligible read model"
        )
    _assert_owned_lock(program)
    _assert_registry_storage(program, original)
    storage = flow_module("storage")
    try:
        # A missing store needs creation only by store_model, never retention/read.
        try:
            storage.retain_models(
                program.repo,
                program.directory,
                current_model_id=original.model_id,
                expected_program_id=str(state["program_id"]),
            )
        except storage.ModelStoreError as exc:
            if (
                require_eligible
                and original.model_id is not None
                and exc.code in {"object_missing", "object_corrupt"}
            ):
                # Only explicit repair may discard already-unservable derived objects.
                # Store validates exact canonical program identity before any deletion.
                _assert_owned_lock(program)
                _assert_registry_storage(program, original)
                storage.retain_models(
                    program.repo,
                    program.directory,
                    current_model_id=None,
                    expected_program_id=str(state["program_id"]),
                )
            elif exc.code != "store_missing":
                raise
        model_id = storage.store_model(program.repo, program.directory, model)
        return storage.encode_registry(body, model_id), _StagedModel(model_id, model, prepared)
    except storage.ModelStoreError as exc:
        raise ProgramError(f"Read-model preparation failed: {exc.code}") from exc


def _close_staged_model(program: Program, staged: _StagedModel) -> None:
    """Run the complete closing image after normal callbacks and before canonical replacement."""
    _assert_owned_lock(program)
    require(
        staged.prepared.checked.state is staged.prepared.state
        and canonical(staged.prepared.state) == staged.prepared.normalized_body,
        "Prepared state changed before final model closure",
    )
    if staged.model.get("eligible") is True:
        observation = flow_module("storage").decode_registry(staged.prepared.body)
        observer = _ProspectiveObserver(program, staged.prepared)
        closed = flow_module("read_model").validate(
            staged.model,
            program,
            observation=observation,
            observer=observer,
        )
        require(closed is not None, "Read-model inputs changed before publication")
        observer.finish()
    program.require_current_location()
    _assert_owned_lock(program)


def _read_materialized_projection(repo: Path, program_dir: Path) -> Record | None:
    repo = repo.absolute()
    directory = program_dir.absolute() if program_dir.is_absolute() else repo / program_dir
    require(
        directory.is_relative_to(repo) and directory != repo,
        "Program must be within configured repository",
    )
    program = Program(repo, safe_path(repo, directory.relative_to(repo).as_posix()))
    program.require_current_location()
    original = program.read_registry_observation()
    if original.model_id is None:
        return None
    storage = flow_module("storage")
    try:
        model = storage.load_model(repo, directory, original.model_id)
    except storage.ModelStoreError:
        return None
    # Derived corruption/currentness is a miss. Canonical and authority failures above remain errors.
    module = flow_module("read_model")
    try:
        observer = _ClosingObserver(program)
        result = module.validate(model, program, observation=original, observer=observer)
        if result is not None:
            observer.finish()
    except (ProgramError, OSError, module.ModelError):
        return None
    if result is None:
        return None
    _assert_registry_storage(program, original)
    program.require_current_location()
    _assert_registry_storage(program, original)
    result["generated_at"] = utc()
    require(len(canonical(result)) <= 8 * 1024 * 1024, "Snapshot output exceeds 8 MiB")
    return result


def storage_enable(program: Program, *, owner: str, reason: str) -> Record:
    """Explicitly enable or repair derived storage without changing logical history."""
    started = perf_counter()
    original = program.read_registry_observation()
    state = parse_registry_state(original, path=program.path("backlog.json"))
    require(
        owner == (coordination.current_director_id(state) or "leader"),
        "Storage enable requires current director",
    )
    string(reason, "storage enable reason")
    encoded, staged = _prepare_model_publication(
        program, state, original, require_eligible=True, preserve_body=True
    )
    backup: Record | None = None
    if original.model_id is None:
        name = f"recovery/storage/{original.storage_sha256}/original-backlog.json"
        path = program.path(name)
        if observed_exists(path):
            require(
                bounded_bytes(path, 8 * 1024 * 1024 + 80) == original.raw_bytes,
                "Storage backup collision",
            )
        else:
            atomic_write(path, original.raw_bytes)
        backup = {"path": name, "sha256": original.storage_sha256}
    _close_staged_model(program, staged)
    _assert_registry_storage(program, original)
    atomic_write(program.path("backlog.json"), encoded)
    try:
        program.render(state)
    except (OSError, ProgramError) as exc:
        raise ProgramError(
            f"Registry storage committed at BODY {original.body_sha256}; derived rendering failed. Run render or explicit storage-enable to recover views: {exc}"
        ) from exc
    return {
        "status": "enabled" if original.model_id is None else "repaired",
        "body_sha256": original.body_sha256,
        "previous_storage_sha256": original.storage_sha256,
        "storage_sha256": sha(encoded),
        "model_id": staged.model_id,
        "backup": backup,
        "preparation_seconds": perf_counter() - started,
    }


def _assert_registry_backup(program: Program, name: str, content: bytes) -> None:
    # Re-admit every backup observation, including the one after model closure.
    path = program.path(name)
    require(observed_is_file(path), "Registry compaction backup is not a regular file")
    require(
        bounded_bytes(path, 8 * 1024 * 1024 + 80) == content,
        "Registry compaction backup changed",
    )


def registry_compact(
    program: Program, *, owner: str, reason: str, expected_revision: str
) -> Record:
    """Explicitly migrate enabled registry JSON without rewriting evidence/history."""
    _assert_owned_lock(program)
    program.require_current_location()
    original = program.read_registry_observation()
    state = parse_registry_state(original, path=program.path("backlog.json"))
    require(
        owner == (coordination.current_director_id(state) or "leader"),
        "Registry compaction requires current director",
    )
    string(reason, "registry compaction reason")
    require(
        phases.revision(state) == expected_revision,
        "Stale registry compaction revision; inspect commitment before retry",
    )
    require(original.model_id is not None, "Registry compaction requires enabled storage")
    require(
        not _compact_registry(state),
        "Registry already compact; use render or storage-enable to recover, never replay migration",
    )
    program.validate(state)
    backup_name = f"recovery/storage/compact-ascii/{original.storage_sha256}/original-backlog.json"
    backup_path = program.path(backup_name)
    if observed_exists(backup_path):
        _assert_registry_backup(program, backup_name, original.raw_bytes)
    else:
        atomic_write(backup_path, original.raw_bytes)
    proposal = deepcopy(state)
    proposal["registry_format"] = {"schema_version": 1, "encoding": "compact-ascii"}

    def unchanged() -> None:
        _assert_owned_lock(program)
        _assert_registry_storage(program, original)
        current = parse_registry_state(
            program.read_registry_observation(), path=program.path("backlog.json")
        )
        require(
            phases.revision(current) == expected_revision, "Registry compaction revision changed"
        )
        require(
            owner == (coordination.current_director_id(current) or "leader"),
            "Registry compaction authority changed",
        )
        _assert_registry_backup(program, backup_name, original.raw_bytes)

    # The ordinary publication path validates all provenance and builds/closes a NEW model
    # against the compact proposed BODY; storage-enable's preserve_body path is not used.
    program._publish(
        proposal,
        verification.publication_guard(program, proposal),
        before_publish=unchanged,
        _registry_backup=(backup_name, original.raw_bytes),
    )
    current = program.read_registry_observation()
    require(current.body_bytes == registry_body(proposal), "Committed compact registry differs")
    return {
        "status": "compacted",
        "registry_format": proposal["registry_format"],
        "previous_revision": expected_revision,
        "revision": phases.revision(proposal),
        "previous_body_sha256": original.body_sha256,
        "body_sha256": current.body_sha256,
        "previous_storage_sha256": original.storage_sha256,
        "storage_sha256": current.storage_sha256,
        "backup": {"path": backup_name, "sha256": original.storage_sha256},
        "before_body_bytes": len(original.body_bytes),
        "body_bytes": len(current.body_bytes),
        "history_changed": False,
    }


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
    require(not observed_exists(destination), "Relocation destination already exists; no overwrite")
    original = program.read_registry_observation().raw_bytes
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
                    observed_is_file(path.with_name("RELOCATED.json")),
                    "Relocate active nested programs separately first",
                )
            require(observed_is_file(path), "Relocation accepts regular files only")
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
            content = budget.read(
                program.path(name),
                8 * 1024 * 1024
                + (80 if name == "backlog.json" or name.endswith("/original-backlog.json") else 0),
            )
            files[name] = sha(content)
            atomic_write(target.path(name), content)
        require(
            sha(program.read_registry_observation().raw_bytes) == sha(original),
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
        encoded = registry_body(state)
        require(len(encoded) <= 8 * 1024 * 1024, "Canonical registry exceeds 8 MiB")
        derived = target.views(state)
        for name, content in files.items():
            require(
                sha(
                    bounded_bytes(
                        target.path(name),
                        8 * 1024 * 1024
                        + (
                            80
                            if name == "backlog.json" or name.endswith("/original-backlog.json")
                            else 0
                        ),
                    )
                )
                == content,
                f"Copied artifact changed: {name}",
            )
        require(
            not observed_exists(target.path(backup_path))
            and not observed_exists(target.path(manifest_path)),
            "Recovery archive collision",
        )
        atomic_write(target.path(backup_path), original)
        atomic_write(target.path("backlog.json"), encoded)
        for name, content in derived.items():
            atomic_write(target.path(name), content.encode("utf-8"))
        target.validate(
            parse_registry_state(
                target.read_registry_observation(), path=target.path("backlog.json")
            )
        )
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
            if observed_is_file(path) and path.name != ".feature-program.lock"
        )
        require(fresh_names == sorted(names), "Source inventory changed during relocation")
        fresh_budget = ReadBudget(256 * 1024 * 1024)
        for name, expected in files.items():
            require(
                sha(
                    fresh_budget.read(
                        program.path(name),
                        8 * 1024 * 1024
                        + (
                            80
                            if name == "backlog.json" or name.endswith("/original-backlog.json")
                            else 0
                        ),
                    )
                )
                == expected,
                f"Source artifact changed during relocation: {name}",
            )
        target.validate(
            parse_registry_state(
                target.read_registry_observation(), path=target.path("backlog.json")
            )
        )
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
    if not observed_exists(program.path("RELOCATION_INCOMPLETE.json")) and located is not None:
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
        sha(program.read_registry_observation().raw_bytes) == marker.get("registry_sha256"),
        "Published registry changed",
    )
    require(
        sha(
            bounded_bytes(
                program.path(string(marker.get("backup_path"), "backup path")), 8 * 1024 * 1024 + 80
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
    state = parse_registry_state(
        program.read_registry_observation(), path=program.path("backlog.json")
    )
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
    if observed_exists(source.path("RELOCATED.json")):
        archive = read_json(source.path("RELOCATED.json"), limit=64 * 1024)
        require(
            archive.get("destination") == marker["destination"]
            and archive.get("registry_sha256") == marker.get("registry_sha256"),
            "Source archive does not match publication",
        )
    else:
        require(
            sha(source.read_registry_observation().raw_bytes)
            == marker.get("original_registry_sha256"),
            "Original source changed; recovery cannot archive it",
        )
        source.load()
        source_files = record(marker.get("source_files"), "source manifest")
        require(
            sorted(
                path.relative_to(source.directory).as_posix()
                for path in source.directory.rglob("*")
                if observed_is_file(path) and path.name != ".feature-program.lock"
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
            state = parse_registry_state(
                program.read_registry_observation(), path=program.path("backlog.json")
            )
            operation_id = planning.publication_register(
                source,
                "relocation",
                {**marker, "program_id": state["program_id"]},
                program,
                legacy_observed=True,
            )
            planning.publication_begin(program, operation_id)
            located = planning.publication_find(program, "relocation", operation_id)
        if not observed_exists(source.path("RELOCATED.json")):
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
        "WORKER.md": "# Worker assignment and evidence\n\nRead the task packet and current decisions. Own only the explicit claimed paths and attempt evidence; preserve others' edits. Revalidate complete acceptance at actual HEAD and scoped digest before claim. Capture executed commands/outcomes honestly. Compilation and native execution are distinct. Block only affected work. Submit immutable execution receipts for review; never mark done from a chat message.\n\nBefore replacement, preserve outgoing goal, constraints, HEAD, scope/digest, changed paths, evidence, decisions, unresolved findings and next action. The director records explicit release; the incoming worker acknowledges the handoff and freshly revalidates before claiming a new attempt. Timeout alone never authorizes takeover.\n\nUse a source-bound verification request to name the check, verifier, input scope, resource boundary, evidence destination and next trigger. Capture actual command or manual procedure, inputs, output and outcome as verification runs. A request or started record is not a passed result. Submit preparation resolves current context and matching observations; supply actual criterion acceptance and use the ordinary submit transition.\n",
        "REVIEW.md": "# Repeated review contract\n\nCompare the entire original acceptance against the exact submitted receipt/source hashes and real evidence. Record accepted or rejected review with precise findings. Leader/self review needs an honest reason, never a claim of independence. Rework retains frozen rounds and uses unique filenames for new evidence. Completed attempts remain historical; regressions require a reasoned leader reopening and new revalidation. Caller-recorded CI is distinct from verified remote observations and merge.\n\nReview the exact frozen candidate and every assigned criterion. Independent code review, QA and PR CI may proceed concurrently when inputs and resources permit. Return all actionable findings discovered in the bounded pass, with affected checks and correction owner; report uncovered criteria as incomplete. Retain earlier failures and obtain fresh review for changed source.\n",
        "EVIDENCE.md": "# Evidence protocol\n\nEvidence belongs under evidence/TASK/attempt-N/ and becomes immutable when admitted. Use template TASK --kind revalidation|execution|review|delivery for failing-safe JSON skeletons. Replace every placeholder with actual facts; absent, skipped or unavailable verification never means passed. Native checks cannot be replaced by compilation. Do not store credentials, raw user data, private configuration contents or huge logs.\n\nKeep request, execution observation and acceptance separate. Reuse only compatible complete source/test/config/environment/obligation inputs with immutable result references and an explicit reason; unknown inputs require verification. Record waits only from observed compatible endpoints, never task status age. Required current PR/beta execution and integrated acceptance remain separate obligations.\n",
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
        not observed_exists(program.path("backlog.json"))
        and all(not observed_exists(program.path(name)) for name in files),
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
    snapshot = commands.add_parser(
        "snapshot", help="Print bounded read-only dashboard JSON; never creates a lock"
    )
    snapshot.add_argument(
        "--evidence-preparation", help="Explicit immutable evidence preparation result JSON"
    )
    snapshot.add_argument("--batch-plan", help="Explicit immutable batch plan/result/failure JSON")
    snapshot.add_argument(
        "--closure-plan", help="Explicit program-relative immutable closure plan/result JSON"
    )
    snapshot.add_argument(
        "--execution-capture", help="Explicit program-relative sealed actual execution capture JSON"
    )
    enable = commands.add_parser(
        "storage-enable", help="Explicitly enable or repair the derived read model"
    )
    enable.add_argument("--owner", required=True)
    enable.add_argument("--reason", required=True)
    compact = commands.add_parser(
        "registry-compact",
        help="Explicitly migrate enabled registry to versioned compact ASCII JSON",
    )
    compact.add_argument("--owner", required=True)
    compact.add_argument("--reason", required=True)
    compact.add_argument("--expected-revision", required=True)
    commands.add_parser(
        "storage-export", help="Export exact logical JSON BODY bytes without writes"
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
    submit.add_argument("--handoff", help="Optional program-relative named review handoff request")
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
    for name in OUTCOME_COMMANDS:
        commands.choices[name].add_argument(
            "--outcome-path", help="Retain actual mutation publication facts without a snapshot"
        )
    return result


def parse_command_args(argv: list[str]) -> argparse.Namespace:
    """Parse host arguments strictly and preserve explicitly delimited live argv verbatim."""
    command_parser = parser()
    args, remaining = command_parser.parse_known_args(argv)
    live_delimited = "--" in argv
    if args.command == "verification-run" and live_delimited:
        # Python 3.11 leaves the trailing nargs='*' positional unconsumed when
        # options follow the task ID. Never ask argparse to parse the live tail.
        boundary = argv.index("--")
        args = command_parser.parse_args(argv[:boundary])
        if args.argv:
            command_parser.error("Live command arguments must follow --")
        args.argv = argv[boundary + 1 :]
    elif remaining:
        command_parser.error("unrecognized arguments: " + " ".join(remaining))
    args.live_delimited = live_delimited
    return args


def main(argv: list[str] | None = None) -> int:
    actual_argv = sys.argv[1:] if argv is None else argv
    args = parse_command_args(actual_argv)
    destination = getattr(args, "outcome_path", None)
    if destination is None:
        return _main(args)
    capture: dict = {"status": "not_committed", "view_recovery_required": False}
    token = _MUTATION_OUTCOME.set(capture)
    try:
        repo = args.repo.absolute()
        requested = Path(args.program_dir)
        directory = requested.absolute() if requested.is_absolute() else repo / requested
        require(
            directory.is_relative_to(repo) and directory != repo,
            "Program must be within configured repository",
        )
        program = Program(repo, safe_path(repo, directory.relative_to(repo).as_posix()))
        name = safe_relative(destination)
        require(name.startswith("evidence/"), "Mutation outcome must be program evidence")
        path = program.path(name)
        require(not observed_exists(path), "Mutation outcome destination already exists")
        result = _main(args)
        status = str(capture["status"])
        # If atomic replacement raised after changing the file, inspect identity;
        # never interpret a nonzero CLI return as permission to replay a mutation.
        if "proposed_revision" in capture and status == "not_committed":
            actual = parse_registry_state(
                program.read_registry_observation(), path=program.path("backlog.json")
            )
            revision = phases.revision(actual)
            capture["revision"] = revision
            if revision == capture["proposed_revision"]:
                status = "committed_view_failed"
                capture["view_recovery_required"] = True
            elif revision != capture.get("before_revision"):
                status = "unknown"
        outcome: Record = {
            "schema_version": 1,
            "kind": "mutation_outcome",
            "operation": args.command,
            "publication": {
                "status": status,
                "before_revision": capture.get("before_revision"),
                "revision": capture.get("revision"),
                "view_recovery_required": capture["view_recovery_required"],
            },
            "changed_task_ids": capture.get("changed_task_ids", []),
            "command_exit_code": result,
            "acceptance_complete": False,
            "next_action": "recover_views"
            if capture["view_recovery_required"]
            else "inspect_current_state"
            if status == "unknown"
            else "continue"
            if status in {"committed", "already_committed"}
            else "correct_input",
        }
        authoring.freeze_document(program, name, outcome)
        return result
    except (ProgramError, OSError) as exc:
        print(
            f"feature-program: mutation outcome retention failed; publication={capture['status']}; inspect current state before retry: {exc}",
            file=sys.stderr,
        )
        return 1
    finally:
        _MUTATION_OUTCOME.reset(token)


def _main(args: argparse.Namespace) -> int:
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
            # Emit exactly the canonical bytes checked by the authoring output bound.
            result = authoring.read_command(program, args)
            sys.stdout.buffer.write(canonical(result))
            return (
                1
                if (args.command == "closure-prepare" and result.get("status") == "failed")
                or result.get("kind") == "batch_preparation_failure"
                else 0
            )
        if args.command == "verification-run":
            result = authoring.run_verification(program, args)
            sys.stdout.buffer.write(canonical(result))
            return 0
        if args.command == "closure-resume":
            result = authoring.resume_closure(program, args)
            sys.stdout.buffer.write(canonical(result))
            return 0
        if args.command == "snapshot":
            print(
                canonical(
                    read_snapshot(
                        repo,
                        program.directory,
                        closure_plan=args.closure_plan,
                        execution_capture=args.execution_capture,
                        evidence_preparation=args.evidence_preparation,
                        batch_plan=args.batch_plan,
                    )
                ).decode(),
                end="",
            )
            return 0
        if args.command == "storage-export":
            program.require_current_location()
            sys.stdout.buffer.write(program.read_state_bytes())
            return 0
        if args.command == "storage-enable":
            with program.lock():
                enabled = storage_enable(program, owner=args.owner, reason=args.reason)
            sys.stdout.buffer.write(canonical(enabled))
            return 0
        if args.command == "registry-compact":
            with program.lock():
                compacted = registry_compact(
                    program,
                    owner=args.owner,
                    reason=args.reason,
                    expected_revision=args.expected_revision,
                )
            sys.stdout.buffer.write(canonical(compacted))
            return 0
        if args.command == "brief":
            # The bound covers emitted bytes, including on Windows: text-mode
            # newline translation would otherwise expand the canonical LF output.
            sys.stdout.buffer.write(canonical(read_brief(repo, program.directory, args.task)))
            return 0
        if args.command in {"coverage", "coverage-binding"}:
            initial = program.read_registry_observation()
            state = parse_registry_state(initial, path=program.path("backlog.json"))
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
                program.read_registry_observation().raw_bytes == initial.raw_bytes,
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
        if args.command == "batch-publish":
            result = authoring.publish_batch_command(program, args)
            print(canonical(result).decode(), end="")
            outcome = record(result.get("outcome", result), "publication result")
            return 0 if outcome.get("status") in {"committed", "already_committed"} else 1
        with program.lock():
            state = program.initialize(args) if args.command == "init" else program.load()
            if args.command == "review-handoff-record":
                prepared = authoring.prepare(program, state, args, materialize_handoff=True)
                changed = phases.revision(prepared.state) != phases.revision(state)
                if changed:
                    program.save(prepared.state, before_publish=prepared.before_publish)
                else:
                    outcome = _MUTATION_OUTCOME.get()
                    if outcome is not None:
                        revision = phases.revision(state)
                        outcome.update(
                            status="already_committed",
                            revision=revision,
                            before_revision=revision,
                            changed_task_ids=[],
                        )
                print(
                    canonical(
                        {
                            "schema_version": 1,
                            "status": "recorded" if changed else "already_recorded",
                            "revision": phases.revision(prepared.state),
                            "native_dispatched": False,
                        }
                    ).decode(),
                    end="",
                )
                return 0
            if args.command == "verification-record":
                prepared = verification.prepare_record(
                    program, state, args.input, observed_head=program.current_head()
                )
                program.save(prepared.state, before_publish=prepared.before_publish)
                print("verification-record: OK; recorded claims only, no check was executed")
                return 0
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
                    prepared = authoring.prepare(
                        program,
                        state,
                        args,
                        observed_head=program.current_head(),
                        materialize_handoff=True,
                    )
                    program.save(prepared.state, before_publish=prepared.before_publish)
                    if args.handoff is not None:
                        task = program.task(prepared.state, args.id)
                        attempt = program.current_attempt(task)
                        submission = record(attempt["submission"], "submission")
                        handoff_id = submission["handoff_id"]
                        intent = next(
                            record(value, "intent")
                            for value in reversed(
                                sequence(
                                    record(prepared.state["review_handoffs"], "handoffs")[
                                        "records"
                                    ],
                                    "records",
                                )
                            )
                            if record(value, "intent")["kind"] == "intent"
                            and record(value, "intent")["handoff_id"] == handoff_id
                        )
                        print(
                            canonical(
                                {
                                    "schema_version": 1,
                                    "status": "submitted",
                                    "task_id": args.id,
                                    "attempt": attempt["number"],
                                    "submission_round": submission["round"],
                                    "handoff_id": handoff_id,
                                    "assignment_generation": intent["assignment_generation"],
                                    "subject": intent["subject"],
                                    "packet_ref": submission["packet_ref"],
                                    "reviewer_id": intent["reviewer_id"],
                                    "eligibility": intent["eligibility"],
                                    "next_actor_id": intent["director_id"],
                                    "next_action": "dispatch_exact_packet",
                                    "submitted_observation": {
                                        "observed_at": datetime.now(timezone.utc).isoformat(),
                                        "clock_id": "local_system_utc",
                                        "timestamp_source": "live_postcondition_observation",
                                        "boundary": "successful_submit_result_observed",
                                    },
                                    "native_dispatched": False,
                                }
                            ).decode(),
                            end="",
                        )
                        return 0
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
        provider_delivery.DeliveryError,
        OSError,
    ) as exc:
        print(f"feature-program: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
