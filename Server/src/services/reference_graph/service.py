"""Server-owned graph sessions. Tool input never selects a user or database."""

from __future__ import annotations

import asyncio
from contextlib import nullcontext
import contextvars
from dataclasses import dataclass, field
import getpass
import json
import os
from pathlib import Path
import re
import sys

from core.config import config
from services.credential_lease import (
    CredentialLease,
    bind_credential_lease,
    require_credential_lease,
)
from services.operation_permissions import authorize_operation
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
    lease: CredentialLease | None
    collect: object
    check: object
    engine: Reconciler
    task: asyncio.Task | None = None
    wake: asyncio.Event = field(default_factory=asyncio.Event)
    force: bool = False
    error: str | None = None


class ReferenceGraphService:
    POLL_SECONDS = 5

    def __init__(self, root: Path | None = None, *, send=native_send):
        self.root = root
        self.send = send
        self.entries: dict[str, Entry] = {}
        self.tasks: set[asyncio.Task] = set()
        self.closed = False
        self.lock = asyncio.Lock()

    async def resolve(self, ctx, action: str) -> tuple[Entry, dict]:
        if self.closed:
            raise RuntimeError("Reference graph service is stopping")
        instance = await get_unity_instance_from_context(ctx)
        if not isinstance(instance, str) or not instance:
            raise ValueError("Select a Unity instance before using its reference graph")
        lease = await require_credential_lease() if config.http_remote_hosted else None
        if lease is not None:
            authorize_operation(lease.permissions, "manage_reference_graph", {"action": action})
        user = "remote:" + lease.user_id if lease else local_user()

        async def check():
            if lease is not None:
                await lease.check()

        async def collect(action: str, **values):
            await check()
            parameters = {
                "action": action,
                **{key: value for key, value in values.items() if value is not None},
            }
            parameters.update(
                expected_instance_id=scope.instance_id, expected_project_id=scope.project_id
            )
            with bind_credential_lease(lease):
                data = await self.send(instance, parameters)
            await check()
            if (
                data.get("instance_id") != scope.instance_id
                or data.get("project_id") != scope.project_id
            ):
                raise ScopeChanged("The selected Unity instance or project changed")
            return data

        await check()
        status = await self.send(instance, {"action": "status"})
        await check()
        scope = GraphScope(user, status.get("instance_id"), status.get("project_id"))
        async with self.lock:
            if self.closed:
                raise RuntimeError("Reference graph service is stopping")
            for old_key, old_entry in list(self.entries.items()):
                if (
                    old_key != scope.key
                    and old_entry.instance == instance
                    and old_entry.workspace.store.scope.user_id == scope.user_id
                ):
                    await self._stop(old_entry)
                    self.entries.pop(old_key, None)
            entry = self.entries.get(scope.key)
            if entry is not None and (entry.instance != instance or entry.lease != lease):
                await self._stop(entry)
                self.entries.pop(scope.key, None)
                entry = None
            if entry is None:
                workspace = await drained_thread(
                    lambda: GraphWorkspace(GraphStore.open(self.root or state_root(), scope))
                )
                await check()
                entry = Entry(
                    workspace,
                    instance,
                    lease,
                    collect,
                    check,
                    Reconciler(
                        workspace,
                        collect,
                        check,
                        lease.check_now if lease else lambda: None,
                        lambda configuration: self._operator_grant(scope, configuration),
                    ),
                )
                self.entries[scope.key] = entry
        return entry, status

    def start(self, entry: Entry, *, force: bool = False, wake: bool = True) -> None:
        if self.closed:
            raise RuntimeError("Reference graph service is stopping")
        if self.entries.get(entry.workspace.store.scope.key) is not entry:
            raise RevisionConflict("Reference graph request ownership changed; retry the request")
        entry.force |= force
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
            if self.entries.get(entry.workspace.store.scope.key) is entry:
                self.entries.pop(entry.workspace.store.scope.key, None)

    async def _watch(self, entry: Entry) -> None:
        async with entry.lease.owner_lifetime() if entry.lease else nullcontext():
            while not self.closed:
                await entry.check()
                entry.wake.clear()
                force, entry.force = entry.force, False
                try:
                    await entry.engine.reconcile(force_external=force)
                    entry.error = None
                except ScopeChanged:
                    return
                except RevisionConflict as error:
                    entry.error = str(error)
                except (OSError, ValueError, RuntimeError) as error:
                    entry.error = type(error).__name__ + ": " + str(error)
                await entry.check()
                try:
                    await asyncio.wait_for(entry.wake.wait(), self.POLL_SECONDS)
                except TimeoutError:
                    pass

    @staticmethod
    async def _stop(entry: Entry) -> None:
        if entry.task is not None:
            task = entry.task
            entry.task = None
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def aclose(self) -> None:
        async with self.lock:
            self.closed = True
            tasks = list(self.tasks)
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            self.entries.clear()

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
        workspace, store = entry.workspace, entry.workspace.store
        external_changed = False
        if action in {"declare", "remove_declaration", "set_source", "set_rule", "set_policy"}:
            if type(options.get("expected_version")) is not int or options["expected_version"] < 0:
                raise ValueError("Read current state and supply its nonnegative expected_version")
        if "id" in fields[action] and (not isinstance(options.get("id"), str) or not options["id"]):
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
                await self._stop(entry)
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
