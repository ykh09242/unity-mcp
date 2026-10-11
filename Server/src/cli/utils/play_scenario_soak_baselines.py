"""Immutable references to explicitly selected successful saved Player soaks."""

from contextlib import contextmanager
from datetime import datetime, timezone
from hashlib import sha256
import os
from pathlib import Path
import stat
import tempfile
import time
from typing import Final, Iterator, Literal, assert_never

from pydantic import JsonValue

from cli.utils.play_scenario_soak import JOURNAL_LIMIT, read_soak_bytes, read_soak_json
from cli.utils.play_scenario_soak_baseline_models import (
    BaselineEntry,
    BaselineEvent,
    BaselineFile,
    BaselinePin,
    BaselineRegistry,
)
from cli.utils.play_scenario_soak_compare_evidence import (
    SavedSoak,
    read_saved_soak,
    require_compatible,
)

REGISTRY_LIMIT: Final = 2 * 1024 * 1024
REQUIRED_FILES: Final = (
    "request.json",
    "definition.json",
    "process.json",
    "run.json",
    "soak.json",
    "client-soak.json",
    "iterations.jsonl",
)


class BaselineError(ValueError):
    """A registry or referenced evidence cannot safely establish a baseline."""


def registry_path(selected: Path) -> Path:
    """An explicitly selected existing directory uses its baselines.json file."""
    _unlinked(selected)
    return selected / "baselines.json" if selected.is_dir() else selected.absolute()


def _unlinked(path: Path) -> None:
    for ancestor in (path, *path.parents):
        if ancestor.exists() or ancestor.is_symlink():
            metadata = ancestor.lstat()
            if ancestor.is_symlink() or getattr(metadata, "st_file_attributes", 0) & getattr(
                stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400
            ):
                raise BaselineError("Registry and evidence paths must have unlinked ancestors")


def read_registry(selected: Path) -> BaselineRegistry:
    """Read only the selected registry, rejecting ambiguous or oversized JSON."""
    path = registry_path(selected)
    try:
        raw = read_soak_json(path, REGISTRY_LIMIT)
        if set(raw) != set(BaselineRegistry.model_fields):
            raise BaselineError("Registry fields disagree with schema version 1")
        if type(raw["schema_version"]) is not int or raw["schema_version"] != 1:
            raise BaselineError("Registry schema version must be integer 1")
        _require_depth(raw)
        return BaselineRegistry.model_validate(raw)
    except (OSError, ValueError, RecursionError) as exc:
        raise BaselineError(f"Invalid baseline registry: {str(exc)[:512]}") from exc


def _require_depth(raw: JsonValue) -> None:
    """Keep the JSON nesting bound identical to the Editor registry reader."""
    pending = [(raw, 0)]
    while pending:
        value, depth = pending.pop()
        if depth > 16:
            raise BaselineError("Registry JSON nesting exceeds its bound")
        match value:
            case dict():
                pending.extend((child, depth + 1) for child in value.values())
            case list():
                pending.extend((child, depth + 1) for child in value)
            case str() | int() | float() | bool() | None:
                pass
            case unreachable:
                assert_never(unreachable)


def _known(history: SavedSoak) -> None:
    environment = history.environment.environment
    if environment is None:
        raise BaselineError("Baseline selection requires a recorded schema-1 environment")
    environment.require_budget_support()
    if require_compatible(history, history):
        raise BaselineError("Baseline selection requires all environment fields to be known")


def _capture(directory: Path, name: str) -> BaselineEntry:
    _unlinked(directory)
    root = directory.absolute()
    saved = read_saved_soak(root)
    _known(saved)
    summary = read_soak_json(root / "soak.json")
    retained = summary["retained_report_files"]
    if not isinstance(retained, list) or any(not isinstance(item, str) for item in retained):
        raise BaselineError("Invalid retained evidence filenames")
    # Admission has already checked the native report links. The model additionally
    # constrains filenames before opening them, keeping hashing within this root.
    files = []
    for filename in sorted((*REQUIRED_FILES, *retained)):
        item = BaselineFile(name=filename, sha256="0" * 64)
        encoded = read_soak_bytes(root / item.name, JOURNAL_LIMIT)
        files.append(BaselineFile(name=item.name, sha256=sha256(encoded).hexdigest()))
    digest = sha256(
        "".join(item.name + ":" + item.sha256 + "\n" for item in files).encode()
    ).hexdigest()
    # Re-admit after hashing so a concurrent evidence replacement cannot silently
    # associate a stale parsed identity with freshly hashed files.
    if read_saved_soak(root).provenance() != saved.provenance():
        raise BaselineError("Evidence changed during baseline registration")
    return BaselineEntry(
        name=name,
        directory=str(root),
        snapshot_file=str(root / "soak.json"),
        registered_utc=datetime.now(timezone.utc).isoformat(),
        evidence_sha256=digest,
        files=files,
        provenance=saved.provenance(),
    )


def validate_entry(entry: BaselineEntry) -> SavedSoak:
    """Re-admit evidence and compare its immutable manifest before every use."""
    current = _capture(Path(entry.directory), entry.name)
    if (
        current.evidence_sha256 != entry.evidence_sha256
        or current.files != entry.files
        or current.provenance != entry.provenance
        or current.snapshot_file != entry.snapshot_file
    ):
        raise BaselineError(f"Registered baseline evidence changed: {entry.name}")
    return read_saved_soak(Path(entry.directory))


@contextmanager
def _locked(path: Path) -> Iterator[None]:
    """Serialize cooperating writers; never recover or remove another writer's lock."""
    lock = path.with_name(path.name + ".lock")
    deadline = time.monotonic() + 5
    while True:
        try:
            descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            break
        except FileExistsError as exc:
            if time.monotonic() >= deadline:
                raise BaselineError(
                    "Registry is locked; inspect the explicit sidecar lock before retrying"
                ) from exc
            time.sleep(0.02)
    try:
        with os.fdopen(descriptor, "w") as stream:
            stream.write(str(os.getpid()))
        yield
    finally:
        lock.unlink()


def _persist(path: Path, registry: BaselineRegistry, before: bytes | None) -> None:
    encoded = registry.model_dump_json(indent=2).encode() + b"\n"
    if len(encoded) > REGISTRY_LIMIT:
        raise BaselineError("Baseline registry exceeds its byte bound")
    with tempfile.NamedTemporaryFile(
        dir=path.parent, prefix=path.name + ".", delete=False
    ) as stream:
        temporary = Path(stream.name)
        stream.write(encoded)
        stream.flush()
        os.fsync(stream.fileno())
    try:
        observed = read_soak_bytes(path, REGISTRY_LIMIT) if path.exists() else None
        if observed != before:
            raise BaselineError("Registry changed outside its writer lock; refusing a lost update")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def update_registry(
    selected: Path, event: BaselineEvent, entry: BaselineEntry | None = None
) -> BaselineRegistry:
    """Apply an explicitly reasoned mutation under a lock with revision protection."""
    path = registry_path(selected)
    if not path.parent.is_dir():
        raise BaselineError("Select an existing registry parent directory")
    with _locked(path):
        before = read_soak_bytes(path, REGISTRY_LIMIT) if path.exists() else None
        registry = read_registry(path) if before is not None else BaselineRegistry()
        if event.revision != registry.revision + 1:
            raise BaselineError(
                "Registry revision changed; reload and retry the explicit operation"
            )
        entries = registry.entries + ([entry] if entry is not None else [])
        updated = BaselineRegistry(
            revision=event.revision,
            entries=entries,
            pins=[BaselinePin(name=name) for name in event.pins],
            history=[*registry.history, event],
        )
        referenced = next(item for item in updated.entries if item.name == event.name)
        validate_entry(referenced)
        _persist(path, updated, before)
        return updated


def register_baseline(selected: Path, name: str, directory: Path) -> BaselineRegistry:
    """Register a unique name without copying, changing or replacing evidence."""
    entry = _capture(directory, name)
    path = registry_path(selected)
    registry = read_registry(path) if path.exists() else BaselineRegistry()
    pins = [pin.name for pin in registry.pins]
    event = BaselineEvent(
        revision=registry.revision + 1,
        action="register",
        name=name,
        previous_pins=pins,
        pins=pins,
        at_utc=entry.registered_utc,
        reason="Explicit saved soak registration",
    )
    return update_registry(path, event, entry)


def set_baseline_pin(selected: Path, name: str, change: tuple[bool, str]) -> BaselineRegistry:
    """Pin or unpin one immutable name, retaining the supplied reason and prior pins."""
    registry = read_registry(selected)
    entry = next((entry for entry in registry.entries if entry.name == name), None)
    if entry is None:
        raise BaselineError(f"Unknown baseline: {name}")
    validate_entry(entry)
    previous = [pin.name for pin in registry.pins]
    pinned, reason = change
    if (name in previous) == pinned:
        raise BaselineError("Pin state is already the requested value")
    pins = sorted([*previous, name]) if pinned else [item for item in previous if item != name]
    action: Literal["pin", "unpin"] = "pin" if pinned else "unpin"
    event = BaselineEvent(
        revision=registry.revision + 1,
        action=action,
        name=name,
        previous_pins=previous,
        pins=pins,
        at_utc=datetime.now(timezone.utc).isoformat(),
        reason=reason,
    )
    return update_registry(selected, event)


def select_baseline(selected: Path, candidate: Path, name: str | None = None) -> BaselineEntry:
    """Choose one compatible pinned reference; ambiguous or unknown conditions fail."""
    registry = read_registry(selected)
    right = read_saved_soak(candidate)
    _known(right)
    pinned = {pin.name for pin in registry.pins}
    choices = [
        entry
        for entry in registry.entries
        if entry.name in pinned and (name is None or entry.name == name)
    ]
    compatible = []
    for entry in choices:
        left = validate_entry(entry)
        try:
            unknown = require_compatible(left, right)
        except ValueError:
            continue
        if not unknown:
            compatible.append(entry)
    if len(compatible) != 1:
        raise BaselineError("Select exactly one compatible pinned baseline by name")
    return compatible[0]
