"""Server-owned graph sessions. Tool input never selects a user or database."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, nullcontext
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
from services.reference_graph.analysis import validate_rule
from services.reference_graph.external import ExternalSource
from services.reference_graph.reconcile import Reconciler, drained_thread
from services.reference_graph.store import (
    Edge,
    GraphScope,
    GraphStore,
    RevisionConflict,
    _hash,
    _json,
    check_owned_path,
)
from services.reference_graph.workspace import GraphWorkspace
from services.tools import get_unity_instance_from_context
from transport.legacy.unity_connection import async_send_command_with_retry
from transport.unity_transport import send_with_unity_instance


class ScopeChanged(RevisionConflict):
    """A watcher cannot follow a replacement Editor into another graph."""


class ReferenceCollectionError(OSError):
    """The native collector could not read the requested evidence."""


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
        if reason == "reference_collection_unavailable":
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
    drain: asyncio.Task | None = None

    def __post_init__(self):
        self.requests_drained.set()


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
        self.lock = asyncio.Lock()
        self._coordination: dict[str, tuple[asyncio.Lock, int]] = {}
        self._resolving = 0
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

    async def resolve(self, ctx, action: str) -> tuple[Entry, dict]:
        if self.closed:
            raise RuntimeError("Reference graph service is stopping")
        self._resolving += 1
        self._resolved.clear()
        try:
            return await self._resolve(ctx, action)
        finally:
            self._resolving -= 1
            if not self._resolving:
                self._resolved.set()

    async def _resolve(self, ctx, action: str) -> tuple[Entry, dict]:
        instance = await get_unity_instance_from_context(ctx)
        if not isinstance(instance, str) or not instance:
            raise ValueError("Select a Unity instance before using its reference graph")
        lease = await require_credential_lease() if config.http_remote_hosted else None
        if lease is not None:
            authorize_operation(lease.permissions, "manage_reference_graph", {"action": action})
        user = "remote:" + lease.user_id if lease else local_user()

        async def request_check():
            if lease is not None:
                await lease.check()

        await request_check()
        status = await self.send(instance, {"action": "status"})
        await request_check()
        scope = GraphScope(user, status.get("instance_id"), status.get("project_id"))
        # Installation and retirement for one scope must not hold the global
        # registry lock while SQLite, native collection or cancellation drains.
        async with self._coordinate(scope.key):
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
                workspace = await drained_thread(
                    lambda: GraphWorkspace(GraphStore.open(self.root or state_root(), scope))
                )
                await request_check()
                async with self.lock:
                    if self.closed:
                        raise RuntimeError("Reference graph service is stopping")
                    entry = self._entry(workspace, instance)
                    self.entries[scope.key] = entry
            await request_check()
            if entry.retired or self.entries.get(scope.key) is not entry:
                raise RevisionConflict(
                    "Reference graph request ownership changed; retry the request"
                )
            now = self._now()
            entry.last_used = now
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

        async def collect(action: str, **values):
            await check()
            parameters = {
                "action": action,
                **{key: value for key, value in values.items() if value is not None},
            }
            parameters.update(
                expected_instance_id=scope.instance_id, expected_project_id=scope.project_id
            )
            with bind_credential_lease(active_lease()):
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
                caught_up = await attempt
                failures, entry.error = 0, None
            except asyncio.CancelledError:
                if asyncio.current_task().cancelling():
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

    async def aclose(self) -> None:
        async with self.lock:
            self.closed = True
            for entry in list(self.entries.values()):
                self._retire(entry)
        await self._resolved.wait()
        while self.tasks:
            await self._join(list(self.tasks))
            # gather may complete synchronously for done tasks (Python 3.14+).
            # Let their registry callbacks run before taking the next snapshot.
            await asyncio.sleep(0)

    @staticmethod
    def _grant(scope: GraphScope, configuration: dict) -> ExternalSource:
        ReferenceGraphService._operator_grant(scope, configuration)
        return ExternalSource(configuration)

    @staticmethod
    def _operator_grant(scope: GraphScope, configuration: dict) -> None:
        if not scope.user_id.startswith("remote:"):
            return
        # Remote filesystem paths belong to the server. Only its operator can
        # authorize a principal's roots; a client's source tool cannot do so.
        grants = json.loads(os.environ.get("UNITY_MCP_REFERENCE_GRAPH_EXTERNAL_ROOTS", "{}"))
        allowed = grants.get(scope.user_id[7:], []) if isinstance(grants, dict) else []
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
        stale = set()
        metadata = workspace.external_validation(pairs)
        adapters = {}
        for provider, identity in pairs:
            cached = metadata.get((provider, identity))
            if cached is None or not cached["enabled"]:
                raise RevisionConflict("External evidence changed; synchronize again")
            if provider not in adapters:
                configuration = json.loads(cached["config"])
                adapters[provider] = (
                    self._grant(workspace.store.scope, configuration),
                    configuration,
                )
            adapter = adapters[provider][0]
            stamp = _json(adapter.stamp(cached["path"]))
            if stamp != cached["stamp"]:
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
        if workspace.store.status()["revision"] != result["revision"]:
            raise RevisionConflict("Graph changed before delivery; restart the page")
        return bool(stale)

    def _validate_checked_sources(
        self, workspace: GraphWorkspace, expected: dict, revision: int
    ) -> None:
        # A checked absence is evidence too: authorize every participating root,
        # including providers that supplied no rows to this particular page.
        sources = [
            source
            for source in workspace.store.sources()
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
            self._grant(workspace.store.scope, source["config"])
        for source in sources:
            self._operator_grant(workspace.store.scope, source["config"])
        if workspace.store.status()["revision"] != revision:
            raise RevisionConflict("Graph changed before checked delivery; restart the page")

    async def execute(self, ctx, action: str, options: dict) -> dict:
        fields = {
            "status": set(),
            "sync": {"force"},
            "query": {"node", "direction", "limit", "cursor", "consistency"},
            "diagnostics": {"node", "offset", "limit", "revision", "consistency"},
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
        entry, editor = await self.resolve(ctx, action)
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
        workspace, store = entry.workspace, entry.workspace.store
        external_changed = False
        if action in {"declare", "remove_declaration", "set_source", "set_rule", "set_policy"}:
            if type(options.get("expected_version")) is not int or options["expected_version"] < 0:
                raise ValueError("Read current state and supply its nonnegative expected_version")
        if "id" in fields and (not isinstance(options.get("id"), str) or not options["id"]):
            raise ValueError("An explicit stable id is required")
        if action in {"query", "diagnostics"} and (
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
            if not await entry.engine.reconcile(force_external=True):
                raise RevisionConflict(
                    "Reference inputs are not synchronized; inspect status and retry"
                )
            editor = await entry.collect("status")
            checked_providers = await drained_thread(workspace.provider_status)
        if action == "query":
            data = await drained_thread(
                store.query,
                options["node"],
                **{
                    key: value
                    for key, value in options.items()
                    if key not in {"node", "consistency"}
                },
            )
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
        elif action == "declarations":
            data = {"declarations": await drained_thread(store.declarations, include_deleted=True)}
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
            data = {"sources": await drained_thread(store.sources)}
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
            data = await drained_thread(store.rules_state)
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
            data = await drained_thread(workspace.policy)
        elif action == "set_policy":
            data = {
                "version": await drained_thread(
                    workspace.set_policy,
                    options.get("config", {}),
                    expected_version=options["expected_version"],
                )
            }
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
        status = await drained_thread(store.status)
        progress = await drained_thread(workspace.progress)
        providers = await drained_thread(workspace.provider_status)
        native_current = (
            progress.get("initialized", False)
            and progress.get("epoch") == editor.get("epoch")
            and progress.get("after") == editor.get("revision")
            and progress.get("phase") == "idle"
            and status["queue_idle"]
            and not providers["addressables"]["errors"]
            and not any(
                editor.get(key)
                for key in (
                    "compiling",
                    "importing",
                    "compilation_failed",
                    "addressables_event_error",
                )
            )
        )
        counts = await drained_thread(workspace.counts)
        if action in {"query", "diagnostics"}:
            external_changed |= await drained_thread(
                self._validate_external, workspace, data, options.get("node")
            )
        if checked_providers is not None:
            await drained_thread(
                self._validate_checked_sources, workspace, checked_providers, data["revision"]
            )
            providers = await drained_thread(workspace.provider_status)
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
        current = native_current and all(
            provider["current"] for provider in external_providers.values()
        )
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
            else:
                self.start(
                    entry,
                    force=external_changed or options.get("force", False),
                    wake=external_changed
                    or action
                    not in {
                        "status",
                        "query",
                        "diagnostics",
                        "declarations",
                        "sources",
                        "rules",
                        "policy",
                    },
                )
        if action == "watch" and not options["enabled"]:
            await self._stop(entry)
            await entry.check()
        if external_changed:
            current = False
            status["queue_idle"] = False
        return {
            "success": True,
            "data": {
                **data,
                "scope": store.scope.key,
                "graph_status": {
                    **status,
                    **counts,
                    "current": bool(current),
                    "native_current": bool(native_current),
                    "providers": providers,
                    "consistency": "checked_snapshot" if consistency == "checked" else "cached",
                    "external_inputs_changed": external_changed,
                    "coverage": "partial_static",
                    "progress": progress,
                    "worker_error": entry.error,
                },
            },
        }
