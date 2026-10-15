"""Explicit dependency images for an immutable ordinary-snapshot read model.

The registry remains authoritative. A model is usable only after one fresh complete
closing observation and the host's independent final registry-selection barrier.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
from dataclasses import dataclass
import hashlib
import importlib
import json
import os
from pathlib import Path
import re
import stat
import sys
from typing import Final, Iterator, Protocol, TypeAlias

Json: TypeAlias = str | int | float | bool | None | list["Json"] | dict[str, "Json"]
Record: TypeAlias = dict[str, Json]
MODEL_LIMIT: Final = 32 * 1024 * 1024
ENTRY_LIMIT: Final = 100000
SCOPE_LIMIT: Final = 10000
QUERY: Final = {"kind": "ordinary_snapshot", "version": 1}
HASH: Final = re.compile(r"[a-f0-9]{64}")
DEPENDENCIES: Final = ("admissions", "files", "probes", "scopes", "exclusions")


class ModelError(ValueError):
    """A finite, malformed derived-model boundary; never a canonical-state error."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


class ProgramAccess(Protocol):
    """Canonical location only; the caller owns registry admission and final CAS."""

    repo: Path
    directory: Path


class Observation(Protocol):
    body_bytes: bytes
    body_sha256: str


class CapturedFile(Protocol):
    content: bytes
    sha256: str


class ClosingObserver(Protocol):
    """One fresh physical ledger, retaining all original logical admissions."""

    def admit(self, root: Path, name: str) -> None: ...
    def file(
        self, root: Path, name: str, *, limit: int, budget_tag: str | None
    ) -> CapturedFile: ...
    def probe(self, root: Path, name: str, *, operation: str) -> str: ...
    def scope(
        self, root: Path, selectors: list[str], *, byte_budget: int, entry_limit: int
    ) -> tuple[Record, Json]: ...
    def exclusions(self, root: Path, groups: Record, *, entry_limit: int) -> list[Json]: ...


def _bytes(value: Json) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    ).encode("utf-8")


def _sha(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _root(path: Path) -> str:
    return os.path.normcase(os.path.abspath(path)).replace("\\", "/")


def _require(condition: bool, code: str = "invalid_model") -> None:
    if not condition:
        raise ModelError(code)


def _record(value: Json) -> Record:
    _require(type(value) is dict)
    return value


def _integer(value: Json, *, minimum: int = 0, maximum: int = ENTRY_LIMIT) -> int:
    _require(type(value) is int and minimum <= value <= maximum)
    return value


def _name(value: Json) -> str:
    _require(type(value) is str and bool(value) and "\\" not in value and "\0" not in value)
    _require(not value.startswith("/") and ".." not in value.split("/") and ":" not in value)
    return value


def _hash(value: Json) -> str:
    _require(type(value) is str and HASH.fullmatch(value) is not None)
    return value


def producer_identity(repo: Path) -> Record:
    """Bind actual executing sources, independently of the canonical data repo."""
    # Do not derive this path from repo: a managed checkout may serve another repo.
    producer_root = Path(__file__).resolve().parents[1]
    directory = producer_root / "tools"
    paths = sorted(directory.glob("feature_program*.py"), key=lambda path: path.name)
    _require(bool(paths), "producer_missing")
    complete = len(paths) <= 128
    members: Record = {}
    with collect(None):
        for path in [*paths, directory / "__init__.py"] if complete else []:
            name = path.relative_to(producer_root).as_posix()
            try:
                mode = path.lstat().st_mode
            except FileNotFoundError:
                _require(path.name == "__init__.py", "producer_missing")
                members[name] = {"kind": "missing"}
                continue
            _require(stat.S_ISREG(mode) and not path.is_symlink(), "producer_type")
            content = path.read_bytes()
            _require(len(content) <= 8 * 1024 * 1024, "producer_size")
            members[name] = {"kind": "file", "sha256": _sha(content), "size": len(content)}
    return {
        "root": _root(producer_root),
        "files": members,
        "complete": complete,
        "runtime": {
            "implementation": sys.implementation.name,
            "version": list(sys.version_info[:5]),
            "platform": sys.platform,
            "executable": _root(Path(sys.executable)),
            "parser_version": 1,
            "canonical_version": 1,
        },
    }


_ACTIVE: ContextVar[Collector | None] = ContextVar("feature_program_read_model", default=None)


def active_collector() -> Collector | None:
    """Return only the explicitly active observation; standalone reads stay strict."""
    return _ACTIVE.get()


def current() -> Collector | None:
    """Alias for domain hooks using the same explicit observation context."""
    return active_collector()


@contextmanager
def collect(collector: Collector | None) -> Iterator[None]:
    """Activate or suspend collection, restoring the preceding context on every exit."""
    token = _ACTIVE.set(collector)
    try:
        yield
    finally:
        _ACTIVE.reset(token)


def observe_probe(root: Path, name: str, *, operation: str, outcome: str) -> None:
    """Optional domain hook without changing the underlying filesystem operation."""
    collector = current()
    if collector is not None:
        collector.observe_probe(root, name, operation=operation, outcome=outcome)


class Collector:
    """Accumulate finite explicit observations; no hook reads the filesystem."""

    def __init__(
        self,
        repo: Path,
        program_dir: Path,
        *,
        program_id: str,
        body_sha256: str,
        producer: Record,
        query: Record,
    ):
        self.repo = Path(os.path.abspath(repo))
        self.program_dir = Path(os.path.abspath(program_dir))
        _require(type(program_id) is str and bool(program_id.strip()))
        _require(_bytes(query) == _bytes(QUERY))
        self.program_id = program_id
        self.body_sha256 = _hash(body_sha256)
        self.producer = deepcopy(producer)
        self.query = deepcopy(query)
        self._roots = {_root(self.repo), _root(self.program_dir), str(producer["root"])}
        self._facts: dict[str, dict[bytes, Record]] = {kind: {} for kind in DEPENDENCIES}
        self._images: dict[str, tuple[str, str | None, int | None]] = {}
        self._normalized_roots: dict[Path, str] = {}
        self._path_specs: dict[tuple[Path, str], Record] = {}
        self._physical_names: dict[tuple[str, str], str] = {}
        self._key_bytes: dict[tuple[str, str, str, str | int | None, str | None], bytes] = {}
        self._reasons: set[str] = set()
        self._count = 0
        if producer.get("complete") is not True:
            self.ineligible("producer_membership_bound")

    def ineligible(self, reason: str) -> None:
        """Record a finite reason, never convert an ordinary integrity exception."""
        code = reason if re.fullmatch(r"[a-z][a-z0-9_.-]{0,127}", reason) else "unknown_branch"
        if len(self._reasons) < 64:
            self._reasons.add(code)

    def _path(self, root: Path, name: str) -> Record | None:
        if type(name) is str:
            cached = self._path_specs.get((root, name))
            if cached is not None:
                return cached
        cacheable = root.is_absolute()
        try:
            name = _name(name)
        except ModelError:
            self.ineligible("unrepresentable_path")
            return None
        normalized = self._normalized_roots.get(root) if cacheable else None
        if normalized is None:
            normalized = _root(root)
            if normalized not in self._roots:
                # Root authorization is lexical; actual admissions remain core-owned.
                if not any(Path(normalized).is_relative_to(Path(bound)) for bound in self._roots):
                    self.ineligible("dependency_outside_bound_root")
                    return None
            if cacheable and len(self._normalized_roots) < ENTRY_LIMIT:
                self._normalized_roots[root] = normalized
        spec = {"root": normalized, "name": name}
        if cacheable and len(self._path_specs) < ENTRY_LIMIT:
            self._path_specs[(root, name)] = spec
        return spec

    def _put(self, kind: str, key: Record, value: Record) -> None:
        descriptor = None
        match kind:
            case "admissions":
                descriptor = (kind, key["root"], key["name"], None, None)
            case "files":
                descriptor = (kind, key["root"], key["name"], key["limit"], key["budget_tag"])
            case "probes":
                descriptor = (kind, key["root"], key["name"], key["operation"], None)
        identity = self._key_bytes.get(descriptor) if descriptor is not None else None
        if identity is None:
            identity = _bytes(key)
            if descriptor is not None and len(self._key_bytes) < ENTRY_LIMIT:
                self._key_bytes[descriptor] = identity
        previous = self._facts[kind].get(identity)
        if previous is not None:
            if previous != value:
                self.ineligible("conflicting_observation")
            return
        if self._count >= ENTRY_LIMIT:
            self.ineligible("dependency_entry_bound")
            return
        self._facts[kind][identity] = deepcopy(value)
        self._count += 1

    def observe_admission(self, root: Path, name: str) -> None:
        """Keep every original logical root/name, including independently admitted aliases."""
        spec = self._path(root, name)
        if spec is not None:
            self._put("admissions", spec, spec)

    def observe_file(
        self,
        root: Path,
        name: str,
        *,
        kind: str,
        sha256: str | None,
        size: int | None,
        limit: int,
        budget_tag: str | None,
    ) -> None:
        """Record an actual bounded image, preserving every consumer's own limit."""
        spec = self._path(root, name)
        _integer(limit, maximum=128 * 1024 * 1024)
        _require(budget_tag is None or type(budget_tag) is str)
        _require(kind in {"file", "missing", "directory", "other"})
        if kind == "file":
            _hash(sha256)
            _integer(size, maximum=limit)
        else:
            _require(sha256 is None and size is None)
        if spec is None:
            return
        logical = (str(spec["root"]), name)
        physical = self._physical_names.get(logical)
        if physical is None:
            physical = os.path.normcase(os.path.normpath(os.path.join(*logical))).replace("\\", "/")
            if len(self._physical_names) < ENTRY_LIMIT:
                self._physical_names[logical] = physical
        image = (kind, sha256, size)
        if physical not in self._images and len(self._images) >= ENTRY_LIMIT:
            self.ineligible("dependency_entry_bound")
            return
        if physical in self._images and self._images[physical] != image:
            self.ineligible("conflicting_observation")
        self._images[physical] = image
        key = {**spec, "limit": limit, "budget_tag": budget_tag}
        self._put("files", key, {**key, "kind": kind, "sha256": sha256, "size": size})

    def observe_probe(self, root: Path, name: str, *, operation: str, outcome: str) -> None:
        """Record the actual branch result; negative predicates retain kind separately."""
        spec = self._path(root, name)
        match operation:
            case "exists" | "is_file" | "is_dir":
                _require(outcome in {"true", "false"})
            case "kind":
                _require(outcome in {"missing", "file", "directory", "other"})
            case "size":
                _require(re.fullmatch(r"0|[1-9][0-9]*", outcome) is not None)
            case _:
                raise ModelError("unknown_probe")
        if spec is not None:
            key = {**spec, "operation": operation}
            self._put("probes", key, {**key, "outcome": outcome})

    def observe_scope(
        self,
        root: Path,
        selectors: list[str],
        snapshot: Record,
        exclusions: Json = None,
        *,
        byte_budget: int,
        entry_limit: int,
    ) -> None:
        """Keep complete selectors, membership and budget semantics for fresh discovery."""
        _integer(byte_budget, maximum=128 * 1024 * 1024)
        _integer(entry_limit, minimum=1, maximum=SCOPE_LIMIT)
        _require(type(selectors) is list)
        if (
            len(selectors) > entry_limit
            or len(snapshot) > entry_limit
            or (type(exclusions) is list and len(exclusions) > entry_limit)
        ):
            self.ineligible("scope_entry_bound")
            return
        if any(self._path(root, name) is None for name in selectors):
            return
        key = {
            "root": _root(root),
            "selectors": selectors,
            "byte_budget": byte_budget,
            "entry_limit": entry_limit,
        }
        self._put("scopes", key, {**key, "snapshot": snapshot, "exclusions": exclusions})

    def observe_exclusions(
        self, root: Path, groups: Record, entries: list[Json], *, entry_limit: int
    ) -> None:
        """Keep the separate skipped-boundary walk, without descending excluded roots."""
        _integer(entry_limit, minimum=1, maximum=SCOPE_LIMIT)
        _require(set(groups) == {"source", "test", "config"})
        for selectors in groups.values():
            _require(type(selectors) is list and len(selectors) <= entry_limit)
            for value in selectors:
                selector = _record(value)
                _require(set(selector) == {"path", "kind", "required", "check_ids"})
                _name(selector["path"])
                _require(selector["kind"] in {"file", "directory"})
                _require(type(selector["required"]) is bool)
                _require(type(selector["check_ids"]) is list)
                _require(all(type(check) is str for check in selector["check_ids"]))
                if self._path(root, selector["path"]) is None:
                    return
        _require(type(entries) is list and len(entries) <= entry_limit)
        key = {"root": _root(root), "groups": groups, "entry_limit": entry_limit}
        self._put("exclusions", key, {**key, "entries": entries})

    def expect_view(self, root: Path, name: str, content: bytes) -> None:
        """Record future bytes only after the host actually used its virtual view."""
        if len(content) > 8 * 1024 * 1024:
            self.ineligible("derived_view_byte_bound")
            return
        self.observe_file(
            root,
            name,
            kind="file",
            sha256=_sha(content),
            size=len(content),
            limit=8 * 1024 * 1024,
            budget_tag="derived_view",
        )

    def finish(self, snapshot: Record) -> Record:
        """Freeze a complete image or a bounded nonservable marker tied to exact BODY."""
        result: Record = {
            "schema_version": 1,
            "kind": "feature_program_read_model",
            "program_id": self.program_id,
            "binding": {
                "repo": _root(self.repo),
                "program_dir": _root(self.program_dir),
                "body_sha256": self.body_sha256,
            },
            "query": deepcopy(self.query),
            "producer": deepcopy(self.producer),
            "dependencies": {
                kind: [deepcopy(value) for _, value in sorted(rows.items())]
                for kind, rows in self._facts.items()
            },
            "eligible": not self._reasons,
            "reasons": sorted(self._reasons),
        }
        if not self._reasons:
            result["snapshot"] = deepcopy(snapshot)
        if len(_bytes(result)) > MODEL_LIMIT:
            result["eligible"] = False
            result["reasons"] = ["model_byte_bound"]
            result.pop("snapshot", None)
            result["dependencies"] = {kind: [] for kind in DEPENDENCIES}
        return result


@dataclass(frozen=True, slots=True)
class _Parsed:
    """Call-local, checked replay descriptors; never shared across observations."""

    roots: dict[str, Path]
    files: list[Record]


def _parse(model: Record, program: ProgramAccess, observation: Observation) -> _Parsed:
    """Parse the closed stored graph before any path from it reaches an observer."""
    _require(len(_bytes(model)) <= MODEL_LIMIT)
    _require(type(model.get("eligible")) is bool)
    fields = {
        "schema_version",
        "kind",
        "program_id",
        "binding",
        "query",
        "producer",
        "dependencies",
        "eligible",
        "reasons",
    }
    _require(set(model) == fields | ({"snapshot"} if model["eligible"] else set()))
    _require(type(model["schema_version"]) is int and model["schema_version"] == 1)
    _require(
        model["kind"] == "feature_program_read_model" and _bytes(model["query"]) == _bytes(QUERY)
    )
    binding = _record(model["binding"])
    _require(set(binding) == {"repo", "program_dir", "body_sha256"})
    _require(
        binding
        == {
            "repo": _root(program.repo),
            "program_dir": _root(program.directory),
            "body_sha256": observation.body_sha256,
        }
    )
    _require(_sha(observation.body_bytes) == observation.body_sha256)
    core = importlib.import_module(("tools." if __package__ else "") + "feature_program")
    state = core.parse_registry_state(observation, path=program.directory / "backlog.json")
    _require(type(state) is dict and state.get("program_id") == model["program_id"])
    _require(type(model["program_id"]) is str and bool(model["program_id"].strip()))
    _require(_bytes(model["producer"]) == _bytes(producer_identity(program.repo)))
    _require(type(model["reasons"]) is list and len(model["reasons"]) <= 64)
    _require(
        all(
            type(reason) is str and re.fullmatch(r"[a-z][a-z0-9_.-]{0,127}", reason)
            for reason in model["reasons"]
        )
    )
    _require(bool(model["reasons"]) != bool(model["eligible"]))
    if model["eligible"]:
        _record(model["snapshot"])
    collector = Collector(
        program.repo,
        program.directory,
        program_id=model["program_id"],
        body_sha256=observation.body_sha256,
        producer=model["producer"],
        query=model["query"],
    )
    deps = _record(model["dependencies"])
    _require(set(deps) == set(DEPENDENCIES))
    _require(all(type(rows) is list for rows in deps.values()))
    _require(sum(len(rows) for rows in deps.values()) <= ENTRY_LIMIT)
    fields_by_kind = {
        "admissions": {"root", "name"},
        "files": {"root", "name", "limit", "budget_tag", "kind", "sha256", "size"},
        "probes": {"root", "name", "operation", "outcome"},
        "scopes": {"root", "selectors", "snapshot", "exclusions", "byte_budget", "entry_limit"},
        "exclusions": {"root", "groups", "entries", "entry_limit"},
    }
    roots: dict[str, Path] = {}
    allowed = {Path(root) for root in collector._roots}
    images: dict[str, tuple[Json, Json, Json]] = {}
    files: dict[tuple[str, str], Record] = {}
    for kind, rows in deps.items():
        previous: bytes | None = None
        for value in rows:
            row = _record(value)
            _require(set(row) == fields_by_kind[kind])
            root_name = row["root"]
            _require(type(root_name) is str)
            if root_name not in roots:
                root = Path(root_name)
                _require(_root(root) == root_name)
                _require(root in allowed or any(root.is_relative_to(bound) for bound in allowed))
                roots[root_name] = root
            root = roots[root_name]
            match kind:
                case "admissions":
                    _name(row["name"])
                    key = row
                case "files":
                    name = _name(row["name"])
                    _integer(row["limit"], maximum=128 * 1024 * 1024)
                    _require(row["budget_tag"] is None or type(row["budget_tag"]) is str)
                    _require(row["kind"] in {"file", "missing", "directory", "other"})
                    if row["kind"] == "file":
                        _hash(row["sha256"])
                        _integer(row["size"], maximum=row["limit"])
                    else:
                        _require(row["sha256"] is None and row["size"] is None)
                    # Lexical physical identity is pure; every original admission alias
                    # is still freshly replayed below. No filesystem epoch is reused.
                    expected = (row["kind"], row["sha256"], row["size"])
                    logical = (root_name, name)
                    strongest = files.get(logical)
                    if strongest is None:
                        physical = os.path.normcase(os.path.normpath(os.path.join(root_name, name)))
                        _require(physical not in images or images[physical] == expected)
                        images[physical] = expected
                    else:
                        _require(
                            tuple(strongest[field] for field in ("kind", "sha256", "size"))
                            == expected
                        )
                    if strongest is None or row["limit"] < strongest["limit"]:
                        files[logical] = row
                    key = {name: row[name] for name in ("root", "name", "limit", "budget_tag")}
                case "probes":
                    _name(row["name"])
                    _require(type(row["operation"]) is str and type(row["outcome"]) is str)
                    match row["operation"]:
                        case "exists" | "is_file" | "is_dir":
                            _require(row["outcome"] in {"true", "false"})
                        case "kind":
                            _require(row["outcome"] in {"missing", "file", "directory", "other"})
                        case "size":
                            _require(re.fullmatch(r"0|[1-9][0-9]*", row["outcome"]) is not None)
                        case _:
                            raise ModelError("unknown_probe")
                    key = {name: row[name] for name in ("root", "name", "operation")}
                case "scopes":
                    _require(type(row["selectors"]) is list)
                    _record(row["snapshot"])
                    collector.observe_scope(
                        root,
                        row["selectors"],
                        row["snapshot"],
                        row["exclusions"],
                        byte_budget=row["byte_budget"],
                        entry_limit=row["entry_limit"],
                    )
                    key = {
                        name: row[name]
                        for name in ("root", "selectors", "byte_budget", "entry_limit")
                    }
                case "exclusions":
                    _record(row["groups"])
                    _require(type(row["entries"]) is list)
                    _require(collector._path(root, "scope") is not None)
                    collector.observe_exclusions(
                        root, row["groups"], row["entries"], entry_limit=row["entry_limit"]
                    )
                    key = {name: row[name] for name in ("root", "groups", "entry_limit")}
            identity = _bytes(key)
            _require(previous is None or previous < identity)
            previous = identity
    _require(not collector._reasons)
    return _Parsed(roots, list(files.values()))


def validate(
    model: Record, program: ProgramAccess, *, observation: Observation, observer: ClosingObserver
) -> Record | None:
    """Return a detached DTO only after every dependency closes in one fresh image.

    Changed/malformed derived objects are misses. Observer canonical/integrity errors
    deliberately propagate; the host owns strict fallback and the final raw barrier.
    """
    with collect(None):
        try:
            parsed = _parse(model, program, observation)
        except (ModelError, TypeError, ValueError, OverflowError):
            return None
        if not model["eligible"]:
            return None
        deps = model["dependencies"]
        probes: dict[tuple[str, str, str], str] = {}

        def probe(root: str, name: str, operation: str) -> str:
            key = (root, name, operation)
            if key not in probes:
                probes[key] = observer.probe(parsed.roots[root], name, operation=operation)
            return probes[key]

        for row in deps["admissions"]:
            observer.admit(parsed.roots[row["root"]], row["name"])
        for row in parsed.files:
            root_name, name = row["root"], row["name"]
            root = parsed.roots[root_name]
            if row["kind"] == "file":
                if probe(root_name, name, "kind") != "file":
                    return None
                image = observer.file(root, name, limit=row["limit"], budget_tag=row["budget_tag"])
                if image.sha256 != row["sha256"] or len(image.content) != row["size"]:
                    return None
            elif probe(root_name, name, "kind") != row["kind"]:
                return None
        for row in deps["probes"]:
            if probe(row["root"], row["name"], row["operation"]) != row["outcome"]:
                return None
        for row in deps["scopes"]:
            snapshot, exclusions = observer.scope(
                parsed.roots[row["root"]],
                row["selectors"],
                byte_budget=row["byte_budget"],
                entry_limit=row["entry_limit"],
            )
            if snapshot != row["snapshot"] or exclusions != row["exclusions"]:
                return None
        for row in deps["exclusions"]:
            if (
                observer.exclusions(
                    parsed.roots[row["root"]], row["groups"], entry_limit=row["entry_limit"]
                )
                != row["entries"]
            ):
                return None
        return deepcopy(model["snapshot"])
