"""Server-owned graph sessions. Tool input never selects a user or database."""

from __future__ import annotations

import asyncio
from contextlib import ExitStack, asynccontextmanager, contextmanager, nullcontext
import contextvars
from dataclasses import dataclass, field
import getpass
import json
import os
from pathlib import Path
import re
import sys
import time

from core.config import config
from services.credential_lease import (
    CredentialLease,
    CredentialLeaseError,
    bind_credential_lease,
    current_credential_lease,
    require_credential_lease,
)
from services.operation_permissions import OperationPermissionError, authorize_operation
from services.reference_graph.analysis import ANALYSIS_VERSION, validate_rule
from services.reference_graph.collection_protocol import integer, text
from services.reference_graph.external import ExternalSource
from services.reference_graph.reconcile import Reconciler, RequestedAssetChanged, drained_thread
from services.reference_graph.response_budget import require_response
from services.reference_graph.store import (
    Edge,
    GraphScope,
    RevisionConflict,
    _hash,
    _json,
    check_owned_path,
)
from services.reference_graph.workspace import GraphWorkspace
from services.tools import get_unity_instance_from_context
from transport.legacy.unity_connection import async_send_command_with_retry
from transport.unity_transport import send_with_unity_instance
from transport.command_timeout import configured_wait_timeout


class ScopeChanged(RevisionConflict):
    """A watcher cannot follow a replacement Editor into another graph."""


class ReferenceCollectionError(OSError):
    """The native collector could not read the requested evidence."""


def _is_unity_asset_page(page: dict) -> bool:
    # Empty pages also have no external grants, manual endpoints or runtime paths to check.
    return not page.get("coverage") and all(
        edge["provider"] == "unity"
        and edge["owner"].startswith("asset:")
        and isinstance(edge.get("target"), str)
        and edge["target"].startswith("asset:")
        for edge in page["edges"]
    )


def state_root() -> Path:
    override = os.environ.get("UNITY_MCP_REFERENCE_GRAPH_DIR")
    if override:
        if not Path(override).is_absolute():
            raise ValueError("Reference graph directory must be absolute")
        return Path(override)
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData/Local")))
    elif sys.platform == "darwin":
        base = Path.home() / "Library/Application Support"
    else:
        base = Path(os.environ.get("XDG_STATE_HOME", str(Path.home() / ".local/state")))
    if not base.is_absolute():
        raise ValueError("OS state directory must be absolute")
    return base / "unity-mcp/reference-graphs"


def local_user() -> str:
    # Resolved only from the server process, never request state or a tool field.
    uid = str(os.getuid()) if hasattr(os, "getuid") else getpass.getuser()
    return "local:" + _hash((uid, str(Path.home().absolute())))


def _native_matches_progress(progress: dict, editor: dict) -> bool:
    return bool(
        progress.get("initialized", False)
        and progress.get("epoch") == editor.get("epoch")
        and progress.get("after") == editor.get("revision")
        and progress.get("editor", {}).get("compilation_context")
        == editor.get("compilation_context")
        and progress.get("phase") == "idle"
        and not any(
            editor.get(key)
            for key in ("compiling", "importing", "compilation_failed", "addressables_event_error")
        )
    )


def _partial_proof_current(entry: Entry, editor: dict, fence: tuple) -> bool:
    proof = entry.engine.partial_proof
    return bool(
        proof
        and entry.engine._gate.locked()
        and proof[0] == fence[1:]
        and all(
            editor.get(key) == proof[1].get(key)
            for key in (
                "epoch",
                "revision",
                "ignore_state",
                "ignore_revision",
                "compilation_context",
            )
        )
        and not any(
            editor.get(key)
            for key in ("compiling", "importing", "compilation_failed", "addressables_event_error")
        )
    )


def _catalog_matches_editor(progress: dict, editor: dict) -> bool:
    return bool(
        progress.get("initialized")
        and not progress.get("census")
        and progress.get("epoch") == editor.get("epoch")
        and progress.get("after") == editor.get("revision")
    )


def _collection_hint(graph: dict, matches_editor: bool) -> str | None:
    progress = graph["progress"]
    phase = progress.get("phase")
    if (
        phase == "error"
        or graph["worker_error"]
        or graph["errors"]
        or graph["failed_assets"]
        or progress.get("external_errors")
        or graph["providers"]["addressables"]["errors"]
    ):
        return "Collection reported an error. References may be missing. Check graph status before retrying."
    if graph.get("analysis_pending"):
        return (
            "Reference analysis is being refreshed. More references may appear after it finishes."
        )
    if phase == "idle" and matches_editor and graph["queue_idle"] and not graph["dirty_assets"]:
        return None
    if phase == "waiting_for_editor":
        return "Collection is waiting for the Editor. More references may appear after it resumes."
    if phase in {"not_initialized", "changes_pending", "idle"}:
        return "Reference collection is pending. More references may appear after it finishes."
    return "Reference collection is in progress. More references may appear. Run the query again after collection finishes."


async def native_send(instance: str, params: dict) -> dict:
    result = await send_with_unity_instance(
        async_send_command_with_retry,
        instance,
        "manage_reference_graph",
        params,
        retry_on_reload=False,
    )
    if hasattr(result, "model_dump"):
        result = result.model_dump()
    if (
        not isinstance(result, dict)
        or result.get("success") is not True
        or not isinstance(result.get("data"), dict)
    ):
        reason = (
            result.get("error", "Reference collector unavailable")
            if isinstance(result, dict)
            else "Invalid collector response"
        )
        if reason == "reference_scope_changed":
            raise ScopeChanged("The selected Editor instance or project changed")
        if params.get("action") == "inventory" and (
            reason == "reference_inventory_snapshot_invalid"
            or reason == "reference_collection_unavailable"
            # Earlier Editors report an invalid inventory cursor only in the message.
            and result.get("message")
            in (
                "Inventory snapshot expired; restart the census.",
                "Inventory continuation requires its snapshot.",
                "Inventory offset is past the snapshot.",
            )
        ):
            raise RevisionConflict(
                str(result.get("message") or "Inventory snapshot is no longer valid")
            )
        if reason == "reference_collection_unavailable":
            if (
                "asset_guard" in params
                and result.get("message") == "Unsupported reference collection fields."
            ):
                raise RequestedAssetChanged
            raise ReferenceCollectionError(str(result.get("message", reason)))
        raise RuntimeError(str(reason))
    return result["data"]


@dataclass
class Entry:
    workspace: GraphWorkspace
    instance: str
    collect: object
    check: object
    engine: Reconciler
    task: asyncio.Task | None = None
    wake: asyncio.Event = field(default_factory=asyncio.Event)
    force: bool = False
    error: str | None = None
    leases: list[tuple[CredentialLease, float]] = field(default_factory=list, repr=False)
    last_used: float = field(default_factory=time.monotonic)
    active_requests: int = 0
    request_tasks: set[asyncio.Task] = field(default_factory=set, repr=False)
    requests_drained: asyncio.Event = field(default_factory=asyncio.Event)
    retired: bool = False
    watching: bool = True
    combined_ignore_status: bool = False
    combined_ignore_needed: bool = False
    drain: asyncio.Task | None = None

    def __post_init__(self):
        self.requests_drained.set()


@dataclass
class _Route:
    users: int = 0
    sequence: int = 0
    observed: int = -1
    scope: str | None = None

    def observe(self, ticket: int, scope: str) -> None:
        if ticket > self.observed:
            self.observed, self.scope = ticket, scope
        self.check(scope)

    def check(self, scope: str) -> None:
        if self.scope != scope:
            raise ScopeChanged("A newer request resolved a replacement Editor; retry the request")


class ReferenceGraphService:
    POLL_SECONDS = 5
    RETRY_SECONDS = 30
    PARK_FAILURES = 3
    IDLE_SECONDS = 300

    def __init__(self, root: Path | None = None, *, send=native_send):
        self.root = root
        self.send = send
        self.entries: dict[str, Entry] = {}
        self.tasks: set[asyncio.Task] = set()
        self.closed = False
        self._close_task: asyncio.Task | None = None
        self.lock = asyncio.Lock()
        self._coordination: dict[str, tuple[asyncio.Lock, int]] = {}
        self._routes: dict[str, _Route] = {}
        self._resolving = 0
        self._resolve_tasks: set[asyncio.Task] = set()
        self._resolved = asyncio.Event()
        self._resolved.set()

    @staticmethod
    def _now() -> float:
        return time.monotonic()

    @asynccontextmanager
    async def _coordinate(self, key: str):
        # Count waiters as well as holders so cancellation cannot split a lock.
        lock, users = self._coordination.get(key, (asyncio.Lock(), 0))
        self._coordination[key] = (lock, users + 1)
        try:
            async with lock:
                yield
        finally:
            _, users = self._coordination[key]
            if users == 1:
                del self._coordination[key]
            else:
                self._coordination[key] = (lock, users - 1)

    @contextmanager
    def _route(self, user: str, instance: str):
        key = _hash((user, instance))
        route = self._routes.setdefault(key, _Route())
        route.users += 1
        route.sequence += 1
        try:
            yield route, route.sequence
        finally:
            route.users -= 1
            if not route.users:
                self._routes.pop(key)

    async def resolve(self, ctx, action: str, *, combined: bool = False) -> tuple[Entry, dict]:
        if self.closed:
            raise RuntimeError("Reference graph service is stopping")
        self._resolving += 1
        request = asyncio.current_task()
        self._resolve_tasks.add(request)
        self._resolved.clear()
        try:
            entry, status = await self._resolve(ctx, action, combined=combined)
            if entry.workspace.restart_policy_pending and action not in {
                "status",
                "policy",
                "set_policy",
                "import_config",
            }:
                await drained_thread(entry.workspace.require_restart_policy)
            return entry, status
        finally:
            self._resolving -= 1
            self._resolve_tasks.discard(request)
            if not self._resolving:
                self._resolved.set()

    async def _resolve(self, ctx, action: str, *, combined: bool = False) -> tuple[Entry, dict]:
        instance = await get_unity_instance_from_context(ctx)
        if not isinstance(instance, str) or not instance:
            raise ValueError("Select a Unity instance before using its reference graph")
        lease = await require_credential_lease() if config.http_remote_hosted else None
        if lease is not None:
            authorize_operation(lease.permissions, "manage_reference_graph", {"action": action})
        user = "remote:" + lease.user_id if lease else local_user()
        with self._route(user, instance) as (route, ticket):
            return await self._resolve_scope(
                instance,
                lease,
                user,
                route,
                ticket,
                getattr(ctx, "reference_graph_expected_scope", None),
                combined=combined,
            )

    async def _resolve_scope(
        self,
        instance: str,
        lease: CredentialLease | None,
        user: str,
        route: _Route,
        ticket: int,
        expected_scope=None,
        *,
        combined: bool = False,
    ) -> tuple[Entry, dict]:

        async def request_check():
            if lease is not None:
                await lease.check()

        await request_check()
        # The optional request contains no cached scope, snapshot, or policy.
        candidate = (
            next(
                (
                    entry
                    for entry in self.entries.values()
                    if not entry.retired
                    and entry.instance == instance
                    and entry.workspace.store.scope.user_id == user
                    and entry.combined_ignore_status
                    and entry.combined_ignore_needed
                ),
                None,
            )
            if combined
            else None
        )
        params = {"action": "status"}
        if candidate is not None:
            params["validate_ignore_current"] = True
        try:
            status = await self.send(instance, params)
        except ReferenceCollectionError:
            if candidate is None:
                raise
            # A route may have been replaced by an older Editor.
            status = await self.send(instance, {"action": "status"})
        await request_check()
        scope = GraphScope(user, status.get("instance_id"), status.get("project_id"))
        if expected_scope is not None and expected_scope != (scope.instance_id, scope.project_id):
            raise ScopeChanged("The operations panel belongs to another Editor or project")
        route.observe(ticket, scope.key)
        # Installation and retirement for one scope must not hold the global
        # registry lock while SQLite, native collection or cancellation drains.
        async with self._coordinate(scope.key):
            route.check(scope.key)
            async with self.lock:
                if self.closed:
                    raise RuntimeError("Reference graph service is stopping")
                drains = []
                for old_key, old_entry in list(self.entries.items()):
                    if old_entry.workspace.store.scope.user_id == scope.user_id and (
                        old_key != scope.key
                        and old_entry.instance == instance
                        or old_key == scope.key
                        and old_entry.instance != instance
                    ):
                        drains.append(self._retire(old_entry))
            if drains:
                await self._join(drains)
            entry = self.entries.get(scope.key)
            if entry is None:
                from services.reference_graph.restart import open_workspace

                workspace = await drained_thread(open_workspace, self.root or state_root(), scope)
                await request_check()
                async with self.lock:
                    if self.closed:
                        raise RuntimeError("Reference graph service is stopping")
                    route.check(scope.key)
                    entry = self._entry(workspace, instance)
                    self.entries[scope.key] = entry
            await request_check()
            route.check(scope.key)
            if entry.retired or self.entries.get(scope.key) is not entry:
                raise RevisionConflict(
                    "Reference graph request ownership changed; retry the request"
                )
            now = self._now()
            entry.last_used = now
            entry.combined_ignore_status = (
                type(status.get("ignore_current_validation")) is int
                and status.get("ignore_current_validation") == 1
                and status.get("ignore_watchers") is False
            )
            entry.leases = [
                (candidate, used)
                for candidate, used in entry.leases
                if candidate is not lease
                and not candidate.retired
                and now - used < self.IDLE_SECONDS
            ]
            if lease is not None:
                entry.leases.append((lease, now))
        return entry, status

    def _entry(self, workspace: GraphWorkspace, instance: str) -> Entry:
        scope = workspace.store.scope

        def active_lease():
            if entry.retired or self.closed:
                raise RevisionConflict(
                    "Reference graph request ownership changed; retry the request"
                )
            if not scope.user_id.startswith("remote:"):
                return None
            candidate = current_credential_lease()
            if candidate is None or scope.user_id != "remote:" + candidate.user_id:
                raise CredentialLeaseError()
            return candidate

        async def check():
            candidate = active_lease()
            if candidate is not None:
                await candidate.check()
            active_lease()

        def check_now():
            candidate = active_lease()
            if candidate is not None:
                candidate.check_now()

        async def send_requested_page(parameters: dict, lease: CredentialLease | None):
            from services.operation_policy import OperationMode
            from services.registry import operation_invocation as invocation

            target = await invocation.capture_target(
                instance, user_id=lease.user_id if lease else None
            )
            if target is None:
                raise RequestedAssetChanged
            admission = invocation.admission_for(target, OperationMode.EXCLUSIVE, expires_at=None)
            # A public policy replacement may already own the Editor. Wait for
            # Editor admission before checking the durable lease and path, then
            # dispatch under this same owner. Hold no SQLite lock while waiting.
            async with invocation.operation_session(admission):
                with ExitStack() as gate:
                    await drained_thread(gate.enter_context, workspace.requested_page_guard())
                    await check()
                    proof = entry.engine.partial_proof
                    eligible = await drained_thread(
                        workspace.requested_asset,
                        entry.engine.worker,
                        (parameters["guid"],),
                        proof[0][0] if proof else None,
                    )
                    if not eligible or eligible["path"] != parameters["asset_guard"]["path"]:
                        raise RequestedAssetChanged
                    return await self.send(instance, parameters)

        async def collect(action: str, **values):
            await check()
            parameters = {
                "action": action,
                **{key: value for key, value in values.items() if value is not None},
            }
            parameters.update(
                expected_instance_id=scope.instance_id, expected_project_id=scope.project_id
            )
            lease = active_lease()
            with bind_credential_lease(lease):
                if "asset_guard" in parameters:
                    data = await send_requested_page(parameters, lease)
                else:
                    data = await self.send(instance, parameters)
            await check()
            if (
                data.get("instance_id") != scope.instance_id
                or data.get("project_id") != scope.project_id
            ):
                raise ScopeChanged("The selected Unity instance or project changed")
            return data

        entry = Entry(
            workspace,
            instance,
            collect,
            check,
            Reconciler(
                workspace,
                collect,
                check,
                check_now,
                lambda configuration: self._operator_grant(scope, configuration),
            ),
        )
        return entry

    def start(self, entry: Entry, *, force: bool = False, wake: bool = True) -> None:
        if self.closed:
            raise RuntimeError("Reference graph service is stopping")
        if entry.retired or self.entries.get(entry.workspace.store.scope.key) is not entry:
            raise RevisionConflict("Reference graph request ownership changed; retry the request")
        entry.force |= force
        entry.watching = True
        if wake:
            entry.wake.set()
        if entry.task is None or entry.task.done():
            # Do not retain an HTTP request, SDK session or execution receipt.
            entry.task = asyncio.create_task(self._watch(entry), context=contextvars.Context())
            self.tasks.add(entry.task)
            entry.task.add_done_callback(lambda task: self._finished(entry, task))

    def _finished(self, entry: Entry, task: asyncio.Task) -> None:
        self.tasks.discard(task)
        if not task.cancelled():
            task.exception()
        if entry.task is task:
            entry.task = None
            if not entry.active_requests:
                self._retire(entry)

    async def _owner(self, entry: Entry) -> CredentialLease | None:
        if not entry.workspace.store.scope.user_id.startswith("remote:"):
            return None
        # Candidates are opaque handles, never saved authorization. Check each
        # exact generation again before pinning it for a single attempt.
        entry.leases = [(lease, used) for lease, used in entry.leases if not lease.retired]
        if entry.leases:
            latest = entry.leases[-1][0]
            now = self._now()
            entry.leases = [
                (lease, used)
                for lease, used in entry.leases
                if lease is latest or now - used < self.IDLE_SECONDS
            ]
        for lease, _ in reversed(entry.leases):
            try:
                await lease.check()
                authorize_operation(lease.permissions, "manage_reference_graph", {"action": "sync"})
                return lease
            except (CredentialLeaseError, OperationPermissionError):
                continue
        raise CredentialLeaseError()

    async def _attempt(self, entry: Entry, lease: CredentialLease | None, force: bool):
        async with lease.owner_lifetime() if lease else nullcontext():
            await entry.check()
            return await entry.engine.reconcile(force_external=force)

    async def _watch(self, entry: Entry) -> None:
        failures, caught_up = 0, False
        while not self.closed and not entry.retired:
            if not entry.active_requests and (
                failures >= self.PARK_FAILURES
                or caught_up
                and self._now() - entry.last_used >= self.IDLE_SECONDS
            ):
                return
            entry.wake.clear()
            force, entry.force = entry.force, False
            try:
                lease = await self._owner(entry)
            except CredentialLeaseError:
                return
            # Revocation cancels this attempt's owner, not the coordinating
            # watcher. Its thread/lease cleanup finishes before another owner.
            attempt = asyncio.create_task(self._attempt(entry, lease, force))
            try:
                caught_up = await asyncio.shield(attempt)
                failures, entry.error = 0, None
            except asyncio.CancelledError:
                if asyncio.current_task().cancelling():
                    if not attempt.cancelling():
                        attempt.cancel()
                    await self._join([attempt])
                    raise
                entry.error = "Reference reconciliation owner was cancelled"
                failures += 1
            except ScopeChanged:
                return
            except (OSError, ValueError, RuntimeError) as error:
                entry.error = type(error).__name__ + ": " + str(error)
                failures += 1
            delay = min(self.RETRY_SECONDS, self.POLL_SECONDS * 2 ** min(failures, 16))
            try:
                await asyncio.wait_for(entry.wake.wait(), delay)
            except TimeoutError:
                pass

    @staticmethod
    async def _join(tasks):
        """Drain owned work even if the caller is cancelled during cleanup."""
        joined = asyncio.gather(*tasks, return_exceptions=True)
        cancelled = False
        while not joined.done():
            try:
                await asyncio.shield(joined)
            except asyncio.CancelledError:
                cancelled = True
        joined.result()
        if cancelled:
            raise asyncio.CancelledError

    async def _stop(self, entry: Entry) -> None:
        if entry.task is not None:
            task = entry.task
            entry.task = None
            if not task.cancelling():
                task.cancel()
            await self._join([task])

    def _retire(self, entry: Entry) -> asyncio.Task:
        if entry.drain is not None:
            return entry.drain
        entry.retired = True
        entry.engine.stopped.set()
        if self.entries.get(entry.workspace.store.scope.key) is entry:
            self.entries.pop(entry.workspace.store.scope.key)
        if entry.task is not None and not entry.task.cancelling():
            entry.task.cancel()
        for request in entry.request_tasks:
            if request is not asyncio.current_task() and not request.cancelling():
                request.cancel()
        entry.drain = asyncio.create_task(self._drain(entry), context=contextvars.Context())
        self.tasks.add(entry.drain)
        entry.drain.add_done_callback(self._drained)
        return entry.drain

    def _drained(self, task: asyncio.Task) -> None:
        self.tasks.discard(task)
        if not task.cancelled():
            task.exception()

    async def _drain(self, entry: Entry) -> None:
        await self._stop(entry)
        await entry.requests_drained.wait()
        async with entry.engine._gate:
            entry.engine.clear_caches()
            entry.leases.clear()

    @property
    def shutdown_status(self) -> dict:
        task = self._close_task
        state = "running"
        if task is not None:
            state = "draining"
            if task.done():
                state = "failed" if task.cancelled() or task.exception() is not None else "drained"
        return {
            "state": state,
            "pending_resolutions": self._resolving,
            "pending_drains": sum(not task.done() for task in self.tasks),
        }

    async def aclose(self) -> bool:
        """Observe shutdown without abandoning the actual thread/database owners."""
        if self._close_task is None:
            self.closed = True
            self._close_task = asyncio.create_task(
                self._close_owned(), context=contextvars.Context()
            )
            self._close_task.add_done_callback(self._observe_close)
        task = self._close_task
        # A completed owner may belong to a previous lifespan's closed loop.
        # Read its retained result without scheduling another callback there.
        if task.done():
            task.result()
            return True
        timeout = configured_wait_timeout("UNITY_MCP_REFERENCE_GRAPH_SHUTDOWN_TIMEOUT_SECONDS", 10)
        # wait does not cancel its input when this observer expires or is cancelled.
        done, _ = await asyncio.wait({task}, timeout=timeout)
        if task not in done:
            return False
        task.result()
        return True

    @staticmethod
    def _observe_close(task: asyncio.Task) -> None:
        if not task.cancelled():
            task.exception()

    async def _close_owned(self) -> None:
        async with self.lock:
            for entry in list(self.entries.values()):
                self._retire(entry)
            for request in self._resolve_tasks:
                if request is not asyncio.current_task() and not request.cancelling():
                    request.cancel()
        await self._resolved.wait()
        while self.tasks:
            await self._join(list(self.tasks))
            # gather may complete synchronously for done tasks (Python 3.14+).
            # Let their registry callbacks run before taking the next snapshot.
            await asyncio.sleep(0)

    @staticmethod
    def _grant(
        scope: GraphScope, configuration: dict, policy: dict | None = None
    ) -> ExternalSource:
        ReferenceGraphService._operator_grant(scope, configuration)
        policy = policy or {}
        return ExternalSource(
            configuration,
            respect_gitignore=policy.get("respect_gitignore", True),
            exclude=policy.get("exclude", []),
        )

    @staticmethod
    def _operator_grant(scope: GraphScope, configuration: dict) -> None:
        # Filesystem authority belongs to the server operator, including local
        # transports. Tool-authored source configuration never grants a root.
        if scope.user_id.startswith("remote:"):
            grants = json.loads(os.environ.get("UNITY_MCP_REFERENCE_GRAPH_EXTERNAL_ROOTS", "{}"))
            allowed = grants.get(scope.user_id[7:], []) if isinstance(grants, dict) else []
        elif scope.user_id.startswith("local:"):
            allowed = json.loads(os.environ.get("UNITY_MCP_REFERENCE_GRAPH_LOCAL_ROOTS", "[]"))
        else:
            allowed = []
        if not isinstance(allowed, list):
            raise PermissionError("No operator-authorized external root")
        candidate = configuration.get("root")
        if not isinstance(candidate, str) or not Path(candidate).is_absolute():
            raise PermissionError("An authorized absolute external root is required")
        candidate = Path(candidate)
        for root in allowed:
            if not isinstance(root, str) or not Path(root).is_absolute():
                continue
            if not candidate.is_relative_to(Path(root)):
                continue
            parent = check_owned_path(Path(root)).resolve(strict=True)
            if check_owned_path(candidate).resolve(strict=True).is_relative_to(parent):
                return
        raise PermissionError(
            "External root requires operator authorization for this authenticated user"
        )

    def _validate_external(
        self, workspace: GraphWorkspace, result: dict, owner: str | None = None
    ) -> bool:
        self._validate_manual_endpoints(
            workspace,
            (
                node
                for edge in result.get("edges", [])
                if edge["provider"] == "manual"
                for node in (edge["owner"], edge["target"])
            ),
        )
        pairs = {
            (edge["provider"], edge["owner"])
            for edge in result.get("edges", [])
            if edge["provider"].startswith("external:")
        }
        pairs.update(
            (item["provider"], owner)
            for item in result.get("coverage", [])
            if item["provider"].startswith("external:")
        )
        metadata, policy, admission = workspace.external_validation(pairs, admission=True)
        for edge in result.get("edges", []):
            if edge["provider"].startswith("runtime:"):
                provenance = edge.get("provenance", {})
                for path in (
                    edge.get("source_path", ""),
                    provenance.get("path", ""),
                    provenance.get("target_path", ""),
                ):
                    if path and not admission(path):
                        raise RevisionConflict(
                            "Historical runtime evidence is excluded by the current collection policy"
                        )
        stale = set()
        adapters = {}
        repositories = {}
        for provider, identity in pairs:
            cached = metadata.get((provider, identity))
            if cached is None or not cached["enabled"]:
                raise RevisionConflict("External evidence changed; synchronize again")
            if provider not in adapters:
                configuration = json.loads(cached["config"])
                adapters[provider] = (
                    self._grant(workspace.store.scope, configuration, policy),
                    configuration,
                )
                repositories[provider] = adapters[provider][0].repository_revision()
            adapter = adapters[provider][0]
            stamp = _json(adapter.stamp(cached["path"]))
            provenance = json.loads(cached["provenance"] or "{}")
            if stamp != cached["stamp"] or provenance.get("repository") != repositories[provider]:
                stale.add((provider, identity))
        # Permission objects are request-local. Recheck operator revocation
        # after file access as well as at the next delivery fence.
        for _, configuration in adapters.values():
            self._operator_grant(workspace.store.scope, configuration)
        for edge in result.get("edges", []):
            if (edge["provider"], edge["owner"]) in stale:
                edge["stale"] = True
        for item in result.get("coverage", []):
            if (item["provider"], owner) in stale:
                item["state"] = "pending"
        if workspace.store.revision() != result["revision"]:
            raise RevisionConflict("Graph changed before delivery; restart the page")
        return bool(stale)

    def _validate_manual_endpoints(self, workspace: GraphWorkspace, owners) -> None:
        owners = {owner for owner in owners if owner.startswith("external:")}
        if not owners:
            return
        rows = workspace.external_nodes(owners)
        if {row["owner"] for row in rows} != owners:
            raise RevisionConflict(
                "Manual endpoint admission is unavailable; synchronize its source"
            )
        policy = workspace.policy(bounded=True)
        adapters = {}
        for row in rows:
            if not row["enabled"]:
                raise RevisionConflict("Manual endpoint source is no longer enabled")
            if row["provider"] not in adapters:
                adapters[row["provider"]] = self._grant(
                    workspace.store.scope, json.loads(row["config"]), policy
                )
            if not adapters[row["provider"]].admitted(row["path"]):
                raise RevisionConflict(
                    "Manual endpoint is excluded by the current collection policy"
                )

    def _validate_checked_sources(
        self, workspace: GraphWorkspace, expected: dict, revision: int
    ) -> None:
        # A checked absence is evidence too: authorize every participating root,
        # including providers that supplied no rows to this particular page.
        sources = [
            source
            for source in workspace.store.sources(bounded=True)
            if source["enabled"] and source["provider"].startswith("external:")
        ]
        versions = {source["provider"]: source["version"] for source in sources}
        expected_versions = {
            provider: state.get("source_version")
            for provider, state in expected.items()
            if provider.startswith("external:")
        }
        if versions != expected_versions:
            raise RevisionConflict("External sources changed before checked delivery")
        for source in sources:
            adapter = self._grant(
                workspace.store.scope, source["config"], workspace.policy(bounded=True)
            )
            if adapter.repository_revision() != expected[source["provider"]].get("repository"):
                raise RevisionConflict("Repository identity changed before checked delivery")
        for source in sources:
            self._operator_grant(workspace.store.scope, source["config"])
        if workspace.store.revision() != revision:
            raise RevisionConflict("Graph changed before checked delivery; restart the page")

    def _validate_exploration(self, workspace: GraphWorkspace, result: dict) -> bool:
        """Reauthorize current and historical evidence without retaining grants."""
        wanted = result.get("sources", {})
        sources = {source["provider"]: source for source in workspace.store.sources(bounded=True)}
        adapters = {}
        for provider, version in wanted.items():
            source = sources.get(provider)
            if source is None or not source["enabled"] or source["version"] != version:
                raise RevisionConflict("Exploration source grant changed; start a new query")
            if provider.startswith("external:"):
                adapters[provider] = self._grant(
                    workspace.store.scope, source["config"], workspace.policy(bounded=True)
                )
        if result.get("historical"):
            self._validate_manual_endpoints(workspace, result.get("_history_nodes", []))
            for provider, path in result["_history_access"]:
                # The captured bytes may differ today. Require access to the
                # original path, never assert historical evidence is current.
                if provider.startswith("runtime:"):
                    if not workspace.admitted(path):
                        raise RevisionConflict(
                            "Historical runtime evidence is excluded by the current collection policy"
                        )
                else:
                    adapters[provider].stamp(path)
            changed = False
        else:
            changed = self._validate_external(workspace, result)
        for provider in adapters:
            self._operator_grant(workspace.store.scope, sources[provider]["config"])
        if workspace.store.revision() != result["revision"]:
            raise RevisionConflict("Graph changed before exploration delivery; restart the page")
        return changed

    def _validate_receipt_sources(self, workspace: GraphWorkspace, expected: dict) -> None:
        """Reauthorize receipt metadata without fencing unrelated graph publication."""
        sources = {
            source["provider"]: source
            for source in workspace.store.sources(bounded=True)
            if source["enabled"] and source["provider"].startswith("external:")
        }
        if {provider: source["version"] for provider, source in sources.items()} != expected:
            raise RevisionConflict("Mutation source grants changed; restart receipt preparation")
        for source in sources.values():
            self._grant(workspace.store.scope, source["config"], workspace.policy(bounded=True))
            self._operator_grant(workspace.store.scope, source["config"])

    async def execute(self, ctx, action: str, options: dict) -> dict:
        fields = {
            "overview": set(),
            "export_config": {"kind", "after", "limit"},
            "import_config": {"kind", "id", "config", "expected_version", "enabled", "reauthorize"},
            "cleanup": {"before", "limit"},
            "retry": set(),
            "status": set(),
            "sync": {"force"},
            "query": {"node", "direction", "limit", "cursor", "consistency"},
            "diagnostics": {"node", "offset", "limit", "revision", "consistency"},
            "explain": {"id", "consistency"},
            "impact": {"node", "direction", "limit", "cursor", "max_depth", "consistency"},
            "path": {"node", "target", "direction", "limit", "cursor", "consistency"},
            "snapshot": {"name", "limit", "cursor", "consistency"},
            "diff": {"before", "after", "limit", "cursor"},
            "release_query": {"id"},
            "delete_snapshot": {"id"},
            "begin_mutation": {"paths", "guids"},
            "add_mutation_targets": {"id", "paths", "guids"},
            "finish_mutation": {"id", "outcome"},
            "await_mutation": {"id", "wait_seconds"},
            "mutation_diff": {"id", "limit", "cursor"},
            "release_mutation": {"id"},
            "runtime_sessions": {"after", "limit"},
            "runtime_start": {"id"},
            "runtime_poll": {"id", "limit"},
            "runtime_status": {"id"},
            "runtime_stop": {"id", "limit"},
            "runtime_events": {"id", "after", "limit"},
            "runtime_handles": {"id", "after", "limit"},
            "runtime_release": {"id"},
            "declarations": set(),
            "declare": {"id", "owner", "edge", "expected_version"},
            "remove_declaration": {"id", "expected_version"},
            "sources": set(),
            "set_source": {"id", "config", "enabled", "expected_version"},
            "rules": set(),
            "set_rule": {"id", "config", "expected_version"},
            "policy": set(),
            "set_policy": {"config", "expected_version"},
            "rebuild": set(),
            "watch": {"enabled"},
        }
        if action not in fields or not isinstance(options, dict) or set(options) - fields[action]:
            raise ValueError("Unsupported reference graph action or fields")
        for key in ("config", "edge"):
            if key in options and not isinstance(options[key], dict):
                raise ValueError(key + " must be an object")
        if "limit" in options and (type(options["limit"]) is not int or options["limit"] <= 0):
            raise ValueError("limit must be a positive integer page size")
        if action == "declare" and (
            not isinstance(options.get("owner"), str) or not options["owner"]
        ):
            raise ValueError("A declaration owner id is required")
        entry, editor = await self.resolve(
            ctx,
            action,
            combined=action == "query" and options.get("consistency", "cached") == "cached",
        )
        if action in {"set_policy", "import_config"}:
            entry.combined_ignore_needed = False
        entry.active_requests += 1
        request = asyncio.current_task()
        entry.request_tasks.add(request)
        entry.requests_drained.clear()
        try:
            return await self._execute(entry, editor, action, options, fields[action])
        finally:
            entry.active_requests -= 1
            entry.request_tasks.discard(request)
            entry.last_used = self._now()
            if not entry.active_requests:
                entry.requests_drained.set()
                if not entry.watching:
                    self._retire(entry)

    async def _execute(
        self, entry: Entry, editor: dict, action: str, options: dict, fields: set
    ) -> dict:
        created_queries = []
        try:
            result = await self._execute_owned(
                entry, editor, action, options, fields, created_queries
            )
            await drained_thread(require_response, result)
            return result
        except BaseException:
            # A failed first delivery leaves no ID with which the client can
            # release its scratch. Continuations remain replayable. drained_thread
            # joins the actual producer before this marker, including cancellation.
            for queries, identity in created_queries:
                await drained_thread(queries.abandon, identity)
            raise

    async def _execute_owned(
        self,
        entry: Entry,
        editor: dict,
        action: str,
        options: dict,
        fields: set,
        created_queries: list,
    ) -> dict:
        workspace, store = entry.workspace, entry.workspace.store
        if action == "import_config":
            from services.reference_graph.operations import import_action

            action, options = import_action(options)
            fields = set(options)
        # An authenticated CAS replacement is also the repair path for an old
        # oversized policy. Do not read that policy before replacing it; normal
        # bounded admission checks resume below after the write has succeeded.
        replacing_policy = action == "set_policy"
        policy_choice_required = (
            workspace.restart_policy_pending
            and not replacing_policy
            and await drained_thread(workspace.state, "restart_policy_pending", False)
        )
        if policy_choice_required and action not in {"status", "policy"}:
            await drained_thread(workspace.require_restart_policy)

        def admission_snapshot():
            if editor.get("ignore_watchers") is not False:
                return False, workspace.ignore_current(editor, bounded=True), None
            with store.read_snapshot():
                policy = workspace.policy(bounded=True)
                return (
                    policy["respect_gitignore"],
                    workspace.ignore_current(editor, bounded=True),
                    policy.get("version", 0),
                )

        policy_changed = False
        fallback, admission_current, fallback_policy_version = False, False, None
        if not replacing_policy:
            fallback, admission_current, fallback_policy_version = await drained_thread(
                admission_snapshot
            )
            if not admission_current:
                await drained_thread(workspace.block_admission)
        external_changed = False
        local_query_page = False
        if action in {"declare", "remove_declaration", "set_source", "set_rule", "set_policy"}:
            if type(options.get("expected_version")) is not int or options["expected_version"] < 0:
                raise ValueError("Read current state and supply its nonnegative expected_version")
        if (
            "id" in fields
            and not (action == "runtime_start" and "id" not in options)
            and (not isinstance(options.get("id"), str) or not options["id"])
        ):
            raise ValueError("An explicit stable id is required")
        if action in {"query", "diagnostics", "impact", "path"} and (
            not isinstance(options.get("node"), str) or not options["node"]
        ):
            raise ValueError("A node id is required, for example asset:<Unity GUID>")
        consistency = options.get("consistency", "cached")
        if consistency not in {"cached", "checked"}:
            raise ValueError("consistency must be cached or checked")
        checked_providers = None
        if consistency == "checked":
            if options.get("cursor") or options.get("offset", 0):
                raise ValueError(
                    "Use checked consistency on the first page, then its revision-bound continuation"
                )
            if not await entry.engine.reconcile(force_external=True, wait=False):
                raise RevisionConflict(
                    "Reference collection is in progress or inputs are not synchronized; inspect status and retry"
                )
            editor = await entry.collect("status")
            checked_providers = await drained_thread(workspace.provider_status, bounded=True)
        protected = (
            action
            in {
                "query",
                "diagnostics",
                "explain",
                "impact",
                "path",
                "snapshot",
                "diff",
                "mutation_diff",
                "begin_mutation",
                "add_mutation_targets",
                "await_mutation",
            }
            or action.startswith("runtime_")
            and action not in {"runtime_release", "runtime_sessions", "runtime_status"}
        )

        # A blocked admission or checked reconciliation can change the initial read.
        if not replacing_policy and (not admission_current or consistency == "checked"):
            fallback, admission_current, fallback_policy_version = await drained_thread(
                admission_snapshot
            )
        ignore_verified = consistency == "checked"
        if protected and fallback and admission_current and not ignore_verified:
            ignore_verified = await entry.engine.validate_ignore(editor)
        if action == "query" and consistency == "cached":
            # This is only a transport hint; admission and proof are checked again.
            entry.combined_ignore_needed = bool(fallback and admission_current and ignore_verified)
        admission_pending = False
        if protected and (not admission_current or fallback and not ignore_verified):
            if action == "query" and consistency == "cached":
                # A cached page never runs the full collection under its
                # Editor operation. The worker will reopen admission first.
                admission_pending = True
            else:
                if not await entry.engine.reconcile(wait=False):
                    raise RevisionConflict(
                        "Reference collection is in progress or current ignore rules are not synchronized; retry"
                    )
                editor = await entry.collect("status")
                if not await drained_thread(workspace.ignore_current, editor, bounded=True):
                    raise RevisionConflict(
                        "Current ignore rules are unavailable; graph admission is incomplete"
                    )
                ignore_verified = True

        def read_page(*, partial_static=False, withhold_edges=False):
            return store.query(
                options["node"],
                **{
                    key: value
                    for key, value in options.items()
                    if key not in {"node", "consistency"}
                },
                **({"partial_static": True} if partial_static else {}),
                **({"withhold_edges": True} if withhold_edges else {}),
            )

        def read_result_state(page_fence=None):
            status = store.status()
            progress = workspace.progress(bounded=True)
            providers = workspace.provider_status(bounded=True)
            current = workspace.ignore_current(editor, bounded=True)
            counts = workspace.counts()
            version_pending = workspace.state("analysis_version", bounded=True) != ANALYSIS_VERSION
            analysis_pending = version_pending or workspace.state(
                "analysis_dirty", False, bounded=True
            )
            return (
                status,
                progress,
                providers,
                current,
                counts,
                version_pending,
                analysis_pending,
                page_fence,
            )

        cached_query = action == "query" and consistency == "cached"

        def opening_snapshot():
            with store.read_snapshot():
                fence = workspace.delivery_fence()
                if fallback_policy_version is not None and fallback_policy_version != fence[3]:
                    return None
                if admission_pending and fence[2]:
                    raise RevisionConflict("Graph admission changed; retry the query")
                exclusions = not admission_pending and workspace.has_exclusions(bounded=True)
                progress = workspace.progress(bounded=True) if exclusions else None
                catalog_pending = exclusions and not _catalog_matches_editor(progress, editor)
                page = state = None
                if cached_query and not admission_pending:
                    page = read_page(withhold_edges=catalog_pending)
                    if _is_unity_asset_page(page):
                        state = read_result_state(fence)
                return fence, exclusions, progress, catalog_pending, page, state

        admission_fence = catalog_progress = prepared_page = page_state = None
        exclusions_apply = catalog_pending = False
        if protected:
            opening = await drained_thread(opening_snapshot)
            if opening is None:
                async with self.lock:
                    self.start(entry, wake=True)
                raise RevisionConflict(
                    "Collection policy changed during admission; retry the request"
                )
            (
                admission_fence,
                exclusions_apply,
                catalog_progress,
                catalog_pending,
                prepared_page,
                page_state,
            ) = opening
            if catalog_pending and not cached_query:
                async with self.lock:
                    self.start(entry, wake=True)
                raise RevisionConflict("Reference inventory is catching up; retry collection")
        partial_static = admission_pending and _partial_proof_current(
            entry, editor, admission_fence
        )
        if (
            partial_static
            and fallback
            and not ignore_verified
            and not await entry.engine.validate_ignore(editor)
        ):
            raise RevisionConflict("Ignore rules changed before partial delivery; retry collection")
        if action in {"overview", "export_config", "cleanup", "retry"}:
            from services.reference_graph import operations

            if action == "overview":
                data = await drained_thread(operations.overview, entry)
            elif action == "export_config":
                data = await drained_thread(operations.export_config, workspace, **options)
            elif action == "retry":
                await drained_thread(operations.retry, workspace)
                data = {"scheduled": True}
            else:
                data = await drained_thread(operations.cleanup, workspace, **options)
                data["memory_caches_cleared"] = False
                if not entry.engine._gate.locked():
                    async with entry.engine._gate:
                        entry.engine.clear_caches()
                        data["memory_caches_cleared"] = True
        elif action.startswith("runtime_"):
            from services.reference_graph.runtime_service import execute as execute_runtime

            data = await execute_runtime(self, entry, action, options)
            data["historical"] = True
        elif action in {
            "begin_mutation",
            "add_mutation_targets",
            "finish_mutation",
            "await_mutation",
            "mutation_diff",
            "release_mutation",
        }:
            from services.reference_graph.mutation_service import execute as execute_mutation

            # Even an empty incident capture depends on participating source
            # grants. Recheck before capture and again before delivery.
            sources = await drained_thread(store.sources, bounded=True)
            source_fence = {
                source["provider"]: source["version"]
                for source in sources
                if source["enabled"] and source["provider"].startswith("external:")
            }
            await drained_thread(self._validate_receipt_sources, workspace, source_fence)
            data = await execute_mutation(self, entry, editor, action, options)
            await drained_thread(self._validate_receipt_sources, workspace, source_fence)
            editor = await entry.collect("status")
            if action == "mutation_diff":
                external_changed = await drained_thread(self._validate_exploration, workspace, data)
        elif action == "query":
            if prepared_page is not None:
                data, local_query_page = prepared_page, page_state is not None
            else:
                data = await drained_thread(
                    read_page,
                    partial_static=partial_static,
                    withhold_edges=catalog_pending or admission_pending and not partial_static,
                )
                local_query_page = cached_query and _is_unity_asset_page(data)
            if not local_query_page:
                external_changed = await drained_thread(self._validate_external, workspace, data)
        elif action == "diagnostics":
            data = await drained_thread(
                workspace.diagnostics,
                options["node"],
                **{
                    key: value
                    for key, value in options.items()
                    if key not in {"node", "consistency"}
                },
            )
            external_changed = await drained_thread(
                self._validate_external, workspace, data, options["node"]
            )
        elif action in {
            "explain",
            "impact",
            "path",
            "snapshot",
            "diff",
            "release_query",
            "delete_snapshot",
        }:
            queries = await drained_thread(workspace.queries)
            arguments = {key: value for key, value in options.items() if key != "consistency"}
            if action == "explain":
                data = await drained_thread(queries.explain, options["id"])
            elif action == "release_query":
                data = {"released": await drained_thread(queries.release, options["id"])}
            elif action == "delete_snapshot":
                data = {"deleted": await drained_thread(queries.delete_snapshot, options["id"])}
            else:

                def produce_page():
                    result = getattr(queries, action)(**arguments)
                    if (
                        action in {"impact", "path", "snapshot"}
                        and not options.get("cursor")
                        and "query_id" in result
                    ):
                        created_queries.append((queries, result["query_id"]))
                    return result

                data = await drained_thread(produce_page)
            if action in {"explain", "impact", "path", "snapshot", "diff"}:
                external_changed = await drained_thread(self._validate_exploration, workspace, data)
        elif action == "declarations":
            data = {
                "declarations": await drained_thread(
                    store.declarations, include_deleted=True, bounded=True
                )
            }
        elif action == "declare":
            data = {
                "version": await drained_thread(
                    store.put_declaration,
                    options["id"],
                    options.get("owner"),
                    Edge(**options.get("edge", {})),
                    expected_version=options["expected_version"],
                )
            }
        elif action == "remove_declaration":
            data = {
                "version": await drained_thread(
                    store.remove_declaration,
                    options["id"],
                    expected_version=options["expected_version"],
                )
            }
        elif action == "sources":
            data = {"sources": await drained_thread(store.sources, bounded=True)}
        elif action == "set_source":
            if re.fullmatch(r"[A-Za-z0-9_-]+", options["id"]) is None:
                raise ValueError("Source id accepts letters, digits, underscore and hyphen")
            enabled = options.get("enabled", True)
            if type(enabled) is not bool:
                raise ValueError("enabled must be boolean")
            configuration = options.get("config", {})
            if enabled:
                await drained_thread(self._grant, store.scope, configuration)
            data = {
                "version": await drained_thread(
                    workspace.set_source,
                    "external:" + options["id"],
                    configuration,
                    expected_version=options["expected_version"],
                    enabled=enabled,
                )
            }
        elif action == "rules":
            data = await drained_thread(store.rules_state, bounded=True)
        elif action == "set_rule":
            rule = options.get("config", {})
            if rule != {"enabled": False}:
                validate_rule(rule)
            data = {
                "version": await drained_thread(
                    store.put_rule,
                    options["id"],
                    rule,
                    expected_version=options["expected_version"],
                )
            }
        elif action == "policy":
            data = await drained_thread(workspace.policy, bounded=True)
        elif action == "set_policy":
            version, policy_changed = await drained_thread(
                workspace._set_policy,
                options.get("config", {}),
                expected_version=options["expected_version"],
            )
            data = {"version": version}
        elif action == "rebuild":
            await drained_thread(workspace.rebuild)
            data = {"scheduled": True}
        elif action == "watch":
            if type(options.get("enabled")) is not bool:
                raise ValueError("enabled must be boolean")
            data = {"watching": options["enabled"]}
        else:
            data = {}
        if action == "sync" and type(options.get("force", False)) is not bool:
            raise ValueError("force must be boolean")
        await entry.check()

        def result_snapshot():
            with store.read_snapshot():
                return read_result_state(workspace.delivery_fence() if local_query_page else None)

        (
            status,
            progress,
            providers,
            admission_current,
            counts,
            analysis_version_pending,
            analysis_pending,
            page_fence,
        ) = page_state if page_state is not None else await drained_thread(result_snapshot)
        ignore_changed = not admission_current
        matches_editor = admission_current and _native_matches_progress(progress, editor)
        native_current = (
            matches_editor
            and (not fallback or ignore_verified)
            and status["queue_idle"]
            and not counts["dirty_assets"]
            and not analysis_pending
            and not providers["addressables"]["errors"]
        )
        if local_query_page:
            if data["revision"] != status["revision"]:
                raise RevisionConflict("Graph changed before delivery; restart the page")
        elif action in {"query", "diagnostics"}:
            external_changed |= await drained_thread(
                self._validate_external, workspace, data, options.get("node")
            )
        elif action in {"explain", "impact", "path", "snapshot", "diff", "mutation_diff"}:
            external_changed |= await drained_thread(self._validate_exploration, workspace, data)
        if checked_providers is not None:
            await drained_thread(
                self._validate_checked_sources, workspace, checked_providers, data["revision"]
            )
            providers = await drained_thread(workspace.provider_status, bounded=True)
        external_providers = {
            key: value for key, value in providers.items() if key.startswith("external:")
        }
        if checked_providers is not None and providers != checked_providers:
            raise RevisionConflict(
                "Provider verification changed before delivery; retry the checked query"
            )
        for provider in external_providers.values():
            provider["current"] = (
                consistency == "checked"
                and provider["state"] == "verified_at_scan"
                and not external_changed
            )
        current = (
            not data.get("historical", False)
            and not any(edge["provider"].startswith("runtime:") for edge in data.get("edges", []))
            and native_current
            and all(provider["current"] for provider in external_providers.values())
        )
        if catalog_pending:
            matches_editor = native_current = current = False
        await entry.check()
        async with self.lock:
            if self.closed:
                raise RuntimeError("Reference graph service is stopping")
            if self.entries.get(store.scope.key) is not entry:
                raise RevisionConflict(
                    "Reference graph request ownership changed; retry the request"
                )
            if action == "watch" and not options["enabled"]:
                entry.watching = False
            elif not policy_choice_required and (
                action != "set_policy" or policy_changed or external_changed or ignore_changed
            ):
                if (
                    action == "query"
                    and consistency == "cached"
                    and options.get("direction", "outgoing") == "outgoing"
                    and not options.get("cursor")
                    and entry.engine.supports_asset_guard(editor)
                    and (not matches_editor or counts["dirty_assets"])
                ):
                    entry.engine.request_asset(options["node"])
                self.start(
                    entry,
                    # Cached validation detects stamps or repository identity.
                    # The ordinary scan already handles both incrementally.
                    # Rehash all inputs only for an explicit force/checked request.
                    force=options.get("force", False),
                    wake=catalog_pending
                    or analysis_pending
                    or bool(counts["dirty_assets"])
                    or external_changed
                    or ignore_changed
                    or (
                        not action.startswith("runtime_")
                        and action
                        not in {
                            "status",
                            "overview",
                            "export_config",
                            "cleanup",
                            "query",
                            "diagnostics",
                            "explain",
                            "impact",
                            "path",
                            "snapshot",
                            "diff",
                            "release_query",
                            "delete_snapshot",
                            "begin_mutation",
                            "add_mutation_targets",
                            "await_mutation",
                            "mutation_diff",
                            "release_mutation",
                            "declarations",
                            "sources",
                            "rules",
                            "policy",
                        }
                    ),
                )
        if action == "watch" and not options["enabled"]:
            await self._stop(entry)
            await entry.check()
        if external_changed:
            current = False
            status["queue_idle"] = False
        # Internal bounded authorization inputs are never part of tool output.
        data.pop("_history_access", None)
        data.pop("_history_nodes", None)
        runtime_policy = data.pop("_runtime_policy", None)
        if runtime_policy is not None and runtime_policy != await drained_thread(
            workspace.policy, bounded=True
        ):
            raise RevisionConflict(
                "Runtime collection policy changed before delivery; restart the page"
            )
        if protected:
            produced_fence = (
                page_fence
                if page_fence is not None
                else await drained_thread(workspace.delivery_fence)
            )
            if admission_fence[1:] != produced_fence[1:] or (
                "revision" in data and data["revision"] != produced_fence[0]
            ):
                raise RevisionConflict(
                    "Graph admission changed while producing the result; restart the request"
                )
            latest = None
            combined_final = False
            if not admission_pending or partial_static:
                combined_final = (
                    action == "query"
                    and consistency == "cached"
                    and fallback
                    and type(editor.get("ignore_current_validation")) is int
                    and editor.get("ignore_current_validation") == 1
                )
                if combined_final:
                    try:
                        latest = await entry.collect("status", validate_ignore_current=True)
                    except ReferenceCollectionError:
                        # The selected Editor may have been replaced by an older one.
                        combined_final = False
                    else:
                        if "ignore_current_verified" not in latest:
                            # Some older collectors ignore unknown request fields.
                            combined_final = False
                            latest = None
                if fallback and not await entry.engine.validate_ignore(latest):
                    raise RevisionConflict("Ignore rules changed before delivery; retry collection")
                if not combined_final:
                    latest = await entry.collect("status")
                    if fallback:
                        text(latest.get("epoch"), "epoch")
                        integer(latest.get("revision"), "revision")
                        integer(latest.get("ignore_revision"), "ignore_revision")
            if partial_static:
                if not _partial_proof_current(entry, latest, produced_fence):
                    raise RevisionConflict("Partial graph proof changed; retry the query")
                final_fence = await drained_thread(workspace.delivery_fence)
            elif not admission_pending:
                if (
                    exclusions_apply
                    and not catalog_pending
                    and not _catalog_matches_editor(catalog_progress, latest)
                ):
                    raise RevisionConflict(
                        "Reference inventory changed before delivery; retry collection"
                    )
                matches_editor = matches_editor and _native_matches_progress(progress, latest)
                native_current = native_current and matches_editor
                current = current and native_current

                def final_snapshot():
                    with store.read_snapshot():
                        return (
                            workspace.ignore_current(latest, bounded=True),
                            workspace.delivery_fence(),
                        )

                final_current, final_fence = await drained_thread(final_snapshot)
                if not final_current:
                    await drained_thread(workspace.block_admission)
                    raise RevisionConflict("Ignore rules changed before delivery; retry collection")
            else:
                final_fence = await drained_thread(workspace.delivery_fence)
            if final_fence != produced_fence:
                raise RevisionConflict(
                    "Graph admission changed before delivery; restart the request"
                )
        graph_status = {
            **status,
            **counts,
            "current": bool(current),
            "native_current": bool(native_current),
            "providers": providers,
            "consistency": "checked_snapshot" if consistency == "checked" else "cached",
            "external_inputs_changed": external_changed,
            "analysis_version_pending": analysis_version_pending,
            "analysis_pending": bool(analysis_pending),
            "coverage": "partial_static",
            "progress": progress,
            # A foreground checked query or another worker can finish after this
            # watcher's last failure. Report the error only while work is outstanding.
            "worker_error": None
            if matches_editor and status["queue_idle"] and not counts["dirty_assets"]
            else entry.error,
        }
        if admission_pending:
            graph_status["admission_pending"] = True
            if partial_static:
                graph_status["partial_static"] = True
        if catalog_pending:
            graph_status["catalog_pending"] = True
        if action in {
            "status",
            "overview",
            "query",
            "diagnostics",
            "explain",
            "impact",
            "path",
        } and not data.get("historical"):
            hint = (
                "Collection is in progress. Only verified Unity dependencies are shown; more references may appear."
                if partial_static
                else "Reference inventory is catching up. No references can be returned yet; retry after collection progresses."
                if catalog_pending
                else "Collection policy is being synchronized. No references can be returned yet; retry after collection progresses."
                if admission_pending
                else _collection_hint(graph_status, matches_editor)
            )
            if hint:
                graph_status["hint"] = hint
        if policy_choice_required:
            graph_status.update(
                current=False,
                native_current=False,
                policy_choice_required=True,
                hint=workspace.RESTART_POLICY_HINT,
            )
        return {
            "success": True,
            "data": {**data, "scope": store.scope.key, "graph_status": graph_status},
        }
