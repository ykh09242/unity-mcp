"""Entry-owned, nonrecursive file hints. Only a complete scan can establish a checkpoint."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import closing, suppress
import contextvars
from dataclasses import dataclass, field
from pathlib import Path
import threading
import time
import unicodedata
import uuid

from watchfiles import Change, watch

from services.reference_graph.collection_protocol import CollectionBudgetExceeded
from services.reference_graph.store import check_owned_path


MAX_DIRECTORIES = 128
MAX_POPULATION = 8192
MAX_PROVIDERS = 8
MAX_PATHS = 512
MAX_PATH_BYTES = 128 * 1024
VERIFY_SECONDS = 60


def initialize(connection) -> None:
    connection.execute(
        "CREATE TABLE IF NOT EXISTS graph_external_watch("
        "provider TEXT PRIMARY KEY, incarnation TEXT NOT NULL, signature TEXT NOT NULL,"
        "sequence INTEGER NOT NULL, gap INTEGER NOT NULL, verified_at REAL)"
    )
    connection.execute(
        "CREATE TABLE IF NOT EXISTS graph_external_events("
        "provider TEXT NOT NULL, path TEXT NOT NULL, sequence INTEGER NOT NULL,"
        "PRIMARY KEY(provider,path))"
    )


@dataclass
class WatchScope:
    # A literal key preserves separately admitted entries on case-sensitive volumes.
    directories: dict[str, tuple[int, int]] = field(default_factory=dict)
    rules: dict[str, bytes | None] = field(default_factory=dict)
    population: int = 0
    available: bool = True

    def visit(self, directory: Path, entries: int) -> None:
        self.population += entries
        if self.population > MAX_POPULATION or len(self.directories) >= MAX_DIRECTORIES:
            self.available = False
        if self.available:
            info = check_owned_path(directory).stat()
            self.directories[str(directory)] = info.st_dev, info.st_ino


@dataclass
class Batch:
    key: tuple[str, str]
    signature: str
    sequence: int
    paths: tuple[str, ...]
    full: bool
    gap: bool

    @classmethod
    def capture(cls, workspace, provider) -> Batch | None:
        # Even a retired incarnation's known hints are useful during a full scan.
        # Skipping traversal additionally requires a matching, gap-free live registration.
        with workspace.store._connect() as db:
            row = db.execute(
                "SELECT incarnation,signature,sequence,verified_at,gap "
                "FROM graph_external_watch WHERE provider=?",
                (provider,),
            ).fetchone()
            if row is None:
                return None
            paths, size = [], 0
            for (path,) in db.execute(
                "SELECT CASE WHEN length(CAST(path AS BLOB))<=? THEN path END "
                "FROM graph_external_events WHERE provider=? ORDER BY path LIMIT ?",
                (MAX_PATH_BYTES, provider, MAX_PATHS + 1),
            ):
                if path is None or len(paths) == MAX_PATHS:
                    return None
                size += len(path.encode("utf-8"))
                if size > MAX_PATH_BYTES:
                    return None
                paths.append(path)
        full = row[3] is None or any(path.endswith("//") for path in paths)
        return cls((provider, row[0]), row[1], row[2], tuple(paths), full, bool(row[4]))

    def acknowledge(self, workspace, worker, *, verified=False):
        if not verified and not self.paths:
            return
        with workspace._write(worker) as db:
            # The checkpoint and the event cutoff were captured in the same snapshot.
            # A later change to the same filename must survive completion.
            db.execute(
                "DELETE FROM graph_external_events WHERE provider=? AND sequence<=? AND EXISTS "
                "(SELECT 1 FROM graph_external_watch WHERE provider=? AND incarnation=?)",
                (self.key[0], self.sequence, *self.key),
            )
            if verified:
                db.execute(
                    "UPDATE graph_external_watch SET verified_at=? "
                    "WHERE provider=? AND incarnation=? AND gap=0",
                    (time.time(), *self.key),
                )


@dataclass
class ScanPlan:
    signature: str | None = None
    registration: Registration | None = None
    batch: Batch | None = None
    scope: WatchScope | None = None
    files: list[str] | None = None
    previous: dict | None = None

    @property
    def full(self) -> bool:
        return self.files is None


class Registration:
    def __init__(self, workspace, provider, signature, root, scope, watch, clock, accepts):
        self.workspace = workspace
        self.provider, self.signature, self.root = provider, signature, root
        self.scope, self.watch, self.clock = scope, watch, clock
        self.accepts = accepts
        self.directory_hints = {}
        for literal in scope.directories:
            directory = Path(literal)
            key = unicodedata.normalize("NFD", literal.casefold())
            self.directory_hints.setdefault(key, []).append(
                directory.relative_to(root).as_posix() + "//"
            )
        self.token = uuid.uuid4().hex
        self.key = (provider, self.token)
        self.ready, self.stop = threading.Event(), threading.Event()
        self.deadline = 0.0
        self.thread = threading.Thread(
            target=contextvars.Context().run,
            args=(self._run,),
            name="graph-external-watch",
            daemon=True,
        )

    def start(self, worker):
        with self.workspace._write(worker) as db:
            db.execute(
                "INSERT INTO graph_external_watch VALUES (?,?,?,0,0,NULL) ON CONFLICT(provider) "
                "DO UPDATE SET incarnation=excluded.incarnation,signature=excluded.signature,"
                "gap=0,verified_at=NULL",
                (*self.key, self.signature),
            )
        self.thread.start()

    def wait_ready(self, check):
        while not self.ready.wait(0.05):
            check()

    def close(self):
        self.stop.set()
        if self.thread.ident is not None:
            self.thread.join()

    def _run(self):
        try:
            with closing(
                self.watch(
                    *map(Path, self.scope.directories),
                    recursive=False,
                    watch_filter=None,
                    ignore_permission_denied=False,
                    stop_event=self.stop,
                    yield_on_timeout=True,
                    rust_timeout=250,
                    debounce=100,
                    step=25,
                )
            ) as changes:
                for events in changes:
                    if self.stop.is_set():
                        break
                    if events and not self._record(events):
                        break
                    # The generator is lazy. Its first yield follows native registration.
                    self.ready.set()
        except Exception:
            # Any backend or database failure breaks continuity, including quota/IO errors.
            pass
        finally:
            self.stop.set()
            self.ready.set()
            # Process memory is already invalid, even if this write also fails.
            with suppress(Exception), self.workspace.store._connect(write=True) as db:
                db.execute(
                    "UPDATE graph_external_watch SET gap=1 WHERE provider=? AND incarnation=?",
                    self.key,
                )

    def _event_hints(self, change, path):
        if str(path) in self.scope.directories:
            # A modified watched directory needs an identity check on the next poll.
            return (
                (path.relative_to(self.root).as_posix() + "/",)
                if change == Change.modified
                else None
            )
        if path.name.casefold() == ".gitignore":
            return None
        if change == Change.added:
            self.scope.population += 1
        if self.scope.population > MAX_POPULATION:
            return None
        if str(path.parent) not in self.scope.directories:
            # Invalidate only plausible, already registered parents.
            key = unicodedata.normalize("NFD", str(path.parent).casefold())
            return self.directory_hints.get(key, (".///",))
        relative = path.relative_to(self.root).as_posix()
        with suppress(ValueError):
            if self.accepts(relative):
                return (relative,)
        # A denied or unsupported spelling can be an admitted file's short name.
        # Persist only its registered parent; canonical traversal controls reads.
        return (path.parent.relative_to(self.root).as_posix() + "//",)

    def _record(self, events):
        if len(events) > MAX_PATHS:
            return False
        paths, size = set(), 0
        for change, raw in events:
            hints = self._event_hints(change, Path(raw))
            if hints is None:
                return False
            for hint in hints:
                if hint not in paths:
                    size += len(hint.encode("utf-8"))
                    if size > MAX_PATH_BYTES or len(paths) == MAX_PATHS:
                        return False
                    paths.add(hint)
        if not paths:
            return True
        with self.workspace.store._connect(write=True) as db:
            row = db.execute(
                "UPDATE graph_external_watch SET sequence=sequence+1 "
                "WHERE provider=? AND incarnation=? AND gap=0 RETURNING sequence",
                self.key,
            ).fetchone()
            if row is None:
                return False
            db.executemany(
                "INSERT INTO graph_external_events VALUES (?,?,?) ON CONFLICT(provider,path) "
                "DO UPDATE SET sequence=excluded.sequence",
                ((self.provider, path, row[0]) for path in paths),
            )
            count, size = db.execute(
                "SELECT count(*),sum(length(CAST(path AS BLOB))) FROM graph_external_events "
                "WHERE provider=?",
                (self.provider,),
            ).fetchone()
            if count > MAX_PATHS or size > MAX_PATH_BYTES:
                db.execute("DELETE FROM graph_external_events WHERE provider=?", (self.provider,))
                db.execute(
                    "UPDATE graph_external_watch SET gap=1 WHERE provider=?", (self.provider,)
                )
                return False
        return True

    def batch(self) -> Batch | None:
        if self.stop.is_set():
            return None
        batch = Batch.capture(self.workspace, self.provider)
        if batch is None or batch.key != self.key or batch.signature != self.signature or batch.gap:
            return None
        batch.full |= self.clock() >= self.deadline
        return batch

    def acknowledge(self, worker, batch: Batch, *, full: bool):
        batch.acknowledge(self.workspace, worker, verified=full)
        if full:
            self.deadline = self.clock() + VERIFY_SECONDS


class ExternalChanges:
    def __init__(self, workspace, *, watch=watch, clock=time.monotonic):
        self.workspace, self.watch, self.clock = workspace, watch, clock
        self.registrations: dict[str, Registration] = {}
        self._lock = threading.Lock()

    def close(self, provider=None):
        with self._lock:
            names = list(self.registrations) if provider is None else [provider]
            for name in names:
                registration = self.registrations.get(name)
                if registration:
                    # The handle stays owned until physically joined. A concurrent
                    # credential/entry drain must wait for this source drain as well.
                    registration.close()
                    del self.registrations[name]

    def plan(self, provider, signature, adapter, *, require_full, check) -> ScanPlan:
        registration = self.registrations.get(provider)
        batch = (
            registration.batch() if registration and signature == registration.signature else None
        )
        if batch and not require_full and not batch.full:
            try:
                literals = []
                directories = registration.scope.directories
                for path in batch.paths:
                    check()
                    if path.endswith("/"):
                        directory = adapter.root / path[:-1]
                        if (
                            str(directory) not in directories
                            or adapter.directory_identity(directory) != directories[str(directory)]
                        ):
                            raise ValueError("External directory identity changed")
                    else:
                        literals.append(path)
                previous = self.workspace.external_inputs(
                    provider, admission=True, paths=tuple(literals)
                )
                files = adapter.changed_files(literals, directories, previous, check)
                return ScanPlan(signature, registration, batch, files=files, previous=previous)
            except (OSError, ValueError):
                # A directory, spelling, or admission change needs fresh traversal.
                batch = None
        if registration and batch is None:
            self.close(provider)
            registration = None
        return ScanPlan(
            signature, registration, batch or Batch.capture(self.workspace, provider), WatchScope()
        )

    def finish(self, plan, worker, provider, adapter, make_adapter, check):
        scope, registration = plan.scope, plan.registration
        if scope and scope.available:
            for path in scope.directories:
                check()
                try:
                    scope.rules[path] = adapter.watch_rule(Path(path))
                except (OSError, ValueError, CollectionBudgetExceeded):
                    scope.available = False
                    break
        if scope and (
            not registration
            or scope.directories != registration.scope.directories
            or scope.rules != registration.scope.rules
            or not scope.available
        ):
            # Discovery preceded registration. The next complete scan verifies this
            # incarnation while it is already collecting events.
            check()
            if plan.batch:
                plan.batch.acknowledge(self.workspace, worker)
            self.register(worker, provider, plan.signature, make_adapter(), scope, check)
            check()
        elif registration:
            registration.acknowledge(worker, plan.batch, full=plan.full)

    def register(self, worker, provider, signature, adapter, scope, check: Callable[[], None]):
        self.close(provider)
        if not scope.available or len(self.registrations) >= MAX_PROVIDERS:
            return
        for literal, identity in scope.directories.items():
            check()
            path = Path(literal)
            try:
                if adapter.directory_identity(path) != identity:
                    return
                if adapter.watch_rule(path) != scope.rules[literal]:
                    return
            except (OSError, ValueError, CollectionBudgetExceeded):
                # A registration-only check can fail even after a successful sweep.
                return
        registration = Registration(
            self.workspace,
            provider,
            signature,
            adapter.root,
            scope,
            self.watch,
            self.clock,
            adapter.watch_filter,
        )
        try:
            with self._lock:
                registration.start(worker)
                self.registrations[provider] = registration
            registration.wait_ready(check)
        except BaseException:
            self.close(provider)
            raise
