"""Scoped Unity catalog invalidations over the SDK's modern listen transport."""

import asyncio
from collections import Counter
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from contextvars import Context, ContextVar
from dataclasses import dataclass, field
from threading import Lock

import anyio
from fastmcp import FastMCP
from mcp.server.context import ServerRequestContext
from mcp.server.subscriptions import ListenHandler
from mcp.shared.exceptions import MCPError
from mcp.shared.subscriptions import ServerEvent, ToolsListChanged
from mcp_types import (
    INTERNAL_ERROR,
    INVALID_PARAMS,
    DiscoverResult,
    RequestParams,
    SubscriptionFilter,
    SubscriptionsListenRequestParams,
    SubscriptionsListenResult,
)
from starlette.types import ASGIApp, Receive, Scope, Send


@dataclass(frozen=True, slots=True)
class WatchScope:
    """Trusted principal and canonical project; None project is local-global only."""

    user_id: str | None
    project_hash: str | None


class SubscriptionAdmission:
    """Charge actual handlers, including retired runtimes, until their finally exits."""

    def __init__(self, total: int = 128, principal: int = 16, project: int = 8) -> None:
        self.total = total
        self.principal = principal
        self.project = project
        self._lock = Lock()
        self._active: dict[object, WatchScope] = {}
        self._principals: Counter[str | None] = Counter()
        self._projects: Counter[WatchScope] = Counter()

    def acquire(self, scope: WatchScope) -> object:
        with self._lock:
            if (
                len(self._active) >= self.total
                or self._principals[scope.user_id] >= self.principal
                or self._projects[scope] >= self.project
            ):
                raise MCPError(INTERNAL_ERROR, "Catalog subscription capacity reached")
            token = object()
            self._active[token] = scope
            self._principals[scope.user_id] += 1
            self._projects[scope] += 1
            return token

    def release(self, token: object) -> None:
        with self._lock:
            scope = self._active.pop(token)
            self._principals[scope.user_id] -= 1
            self._projects[scope] -= 1
            if not self._principals[scope.user_id]:
                del self._principals[scope.user_id]
            if not self._projects[scope]:
                del self._projects[scope]

    @property
    def active_count(self) -> int:
        with self._lock:
            return len(self._active)


# Never reset this pool on app reconfiguration: a noncooperative old handler must
# keep its charge rather than grant another lifespan additional capacity.
_process_admission = SubscriptionAdmission()
_watch_scope: ContextVar[WatchScope | None] = ContextVar("catalog_watch_scope", default=None)
_HTTP_OWNER = "unity_mcp_catalog_subscription_owner"


class _HttpOwner:
    """Keep admission through the transport's final send and child-task teardown."""

    def __init__(self) -> None:
        self.cancel = anyio.CancelScope()
        self.on_finished: Callable[[], None] | None = None


class CatalogSubscriptionLifetimeMiddleware:
    """No parsing or routing: hand HTTP stream lifetime to the listen handler."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        owner = _HttpOwner()
        owned_scope = {**scope, "state": {**scope.get("state", {}), _HTTP_OWNER: owner}}
        try:
            with owner.cancel:
                await self.app(owned_scope, receive, send)
        finally:
            if owner.on_finished is not None:
                # Pylint sees the None initializer, not listen's callable assignment.
                owner.on_finished()  # pylint: disable=not-callable
                owner.on_finished = None


class ScopedSubscriptionBus:
    """SDK bus seam; scopes stay outside the event vocabulary and wire payload."""

    def __init__(self, on_removed: Callable[[WatchScope], None]) -> None:
        self._listeners: dict[object, tuple[WatchScope, Callable[[ServerEvent], None]]] = {}
        self._on_removed = on_removed

    @property
    def scopes(self) -> set[WatchScope]:
        return {scope for scope, _ in self._listeners.values()}

    def subscribe(self, listener: Callable[[ServerEvent], None]) -> Callable[[], None]:
        scope = _watch_scope.get()
        if scope is None:
            raise RuntimeError("Catalog subscription scope is not bound")
        token = object()
        self._listeners[token] = (scope, listener)

        def unsubscribe() -> None:
            removed = self._listeners.pop(token, None)
            if removed is not None and scope not in self.scopes:
                self._on_removed(scope)

        return unsubscribe

    async def publish(self, event: ServerEvent) -> None:
        scope = _watch_scope.get()
        if scope is None:
            raise RuntimeError("Catalog publication scope is not bound")
        for target, listener in tuple(self._listeners.values()):
            if target == scope:
                listener(event)
        await anyio.lowlevel.checkpoint()


@dataclass(slots=True)
class _Lease:
    """Mutable handler lifetime; cancellation never releases admission by itself."""

    cancel: anyio.CancelScope = field(default_factory=anyio.CancelScope)


class CatalogSubscriptions:
    """Own tools-only SDK subscriptions and one bounded catalog publisher worker."""

    def __init__(
        self,
        server: FastMCP,
        *,
        remote_hosted: bool,
        admission: SubscriptionAdmission | None = None,
        buffered_events: int = 32,
    ) -> None:
        self.remote_hosted = remote_hosted
        self.admission = admission if admission is not None else _process_admission
        self.bus = ScopedSubscriptionBus(self._remove_scope)
        self.handler = ListenHandler(
            self.bus, max_subscriptions=128, max_buffered_events=buffered_events
        )
        self._leases: dict[object, _Lease] = {}
        self._dirty: set[WatchScope] = set()
        self._stamps: dict[WatchScope, tuple[str, int]] = {}
        self._wake = anyio.Event()
        self._changed = anyio.Event()
        self._closed = True
        self._worker: asyncio.Task[None] | None = None
        low = server._mcp_server
        self._discover_entry = low.get_request_handler("server/discover")
        if self._discover_entry is None:
            raise RuntimeError("Installed SDK has no discover handler")
        low.add_request_handler(
            "subscriptions/listen", SubscriptionsListenRequestParams, self.listen
        )
        low.add_request_handler("server/discover", RequestParams, self.discover)

    @asynccontextmanager
    async def lifespan(self) -> AsyncIterator[None]:
        if self._leases:
            raise RuntimeError("Previous catalog subscriptions are still active")
        self._closed = False
        # FastMCP unwinds user lifespans under its own cancellation shield; an
        # AnyIO task group's cancel scope cannot span that SDK yield boundary.
        # Match the existing legacy notifier's explicitly owned task pattern.
        self._worker = asyncio.create_task(self._drain(), context=Context())
        try:
            yield
        finally:
            with anyio.CancelScope(shield=True):
                await self.close()
                self._worker.cancel()
                await asyncio.gather(self._worker, return_exceptions=True)
                self._worker = None

    async def discover(self, ctx: ServerRequestContext, params: RequestParams) -> DiscoverResult:
        result = await self._discover_entry.handler(ctx, params)
        if not isinstance(result, DiscoverResult):
            raise RuntimeError("Installed SDK discover result is unsupported")
        caps = result.capabilities
        return result.model_copy(
            update={
                "capabilities": caps.model_copy(
                    update={
                        "prompts": caps.prompts.model_copy(update={"list_changed": False})
                        if caps.prompts is not None
                        else None,
                        "resources": caps.resources.model_copy(
                            update={"list_changed": False, "subscribe": False}
                        )
                        if caps.resources is not None
                        else None,
                    }
                )
            }
        )

    async def _resolve_scope(self, ctx: ServerRequestContext) -> WatchScope:
        from fastmcp.server.dependencies import get_context
        from transport.plugin_hub import PluginHub
        from transport.unity_instance_middleware import get_unity_instance_middleware
        from transport.unity_transport import _resolve_user_id_from_request

        user_id = await _resolve_user_id_from_request()
        if self.remote_hosted and not user_id:
            raise MCPError(INVALID_PARAMS, "Authenticated catalog principal is required")
        selector = (ctx.meta or {}).get("unity_instance")
        if selector is None and not self.remote_hosted:
            return WatchScope(None, None)
        if not isinstance(selector, str) or not selector.strip():
            raise MCPError(INVALID_PARAMS, "Catalog subscriptions require unity_instance metadata")
        fast_context = get_context()
        await fast_context.set_state("user_id", user_id, serializable=False)
        try:
            resolved = await get_unity_instance_middleware()._resolve_instance_value(
                selector.strip(), fast_context
            )
        except ValueError as exc:
            raise MCPError(INVALID_PARAMS, str(exc)) from exc
        project_hash = resolved.rsplit("@", 1)[-1]
        registry = PluginHub._registry
        if registry is None or await registry.get_catalog_snapshot(project_hash, user_id) is None:
            raise MCPError(INVALID_PARAMS, "Catalog subscription instance is unavailable")
        return WatchScope(user_id, project_hash)

    async def listen(
        self, ctx: ServerRequestContext, params: SubscriptionsListenRequestParams
    ) -> SubscriptionsListenResult:
        if self._closed:
            raise MCPError(INTERNAL_ERROR, "Catalog subscriptions are closed")
        scope = await self._resolve_scope(ctx)
        if self._closed:
            raise MCPError(INTERNAL_ERROR, "Catalog subscriptions are closed")
        admission = self.admission.acquire(scope)
        request = ctx.request
        owner = request.scope.get("state", {}).get(_HTTP_OWNER) if request is not None else None
        lease = _Lease(cancel=owner.cancel) if owner is not None else _Lease()
        self._leases[admission] = lease
        if owner is not None:
            owner.on_finished = lambda: self._finish(admission)
        bound = _watch_scope.set(scope)
        try:
            narrowed = params.model_copy(
                update={
                    "notifications": SubscriptionFilter(
                        tools_list_changed=params.notifications.tools_list_changed
                    )
                }
            )
            if owner is not None:
                return await self.handler(ctx, narrowed)
            with lease.cancel:
                return await self.handler(ctx, narrowed)
            raise MCPError(INTERNAL_ERROR, "Catalog subscription closed")
        finally:
            _watch_scope.reset(bound)
            if owner is None:
                self._finish(admission)

    def _finish(self, admission: object) -> None:
        self._leases.pop(admission)
        self.admission.release(admission)
        changed, self._changed = self._changed, anyio.Event()
        changed.set()

    def publish(
        self,
        *,
        user_id: str | None = None,
        project_hash: str | None = None,
        session_id: str | None = None,
        catalog_revision: int | None = None,
    ) -> None:
        """Queue only committed invalidations; one marker per active watch scope."""
        if self._closed or (self.remote_hosted and (not user_id or not project_hash)):
            return
        for scope in self.bus.scopes:
            if self.remote_hosted:
                matches = scope == WatchScope(user_id, project_hash)
            else:
                matches = scope.user_id is None and (
                    project_hash is None or scope.project_hash in (None, project_hash)
                )
            if not matches:
                continue
            if session_id is not None and catalog_revision is not None:
                stamp = (session_id, catalog_revision)
                if self._stamps.get(scope) == stamp:
                    continue
                self._stamps[scope] = stamp
            self._dirty.add(scope)
        if self._dirty:
            self._wake.set()

    def _remove_scope(self, scope: WatchScope) -> None:
        self._dirty.discard(scope)
        self._stamps.pop(scope, None)

    async def _drain(self) -> None:
        while not self._closed:
            await self._wake.wait()
            self._wake = anyio.Event()
            dirty, self._dirty = self._dirty, set()
            for scope in dirty:
                if self._closed:
                    break
                bound = _watch_scope.set(scope)
                try:
                    await self.bus.publish(ToolsListChanged())
                finally:
                    _watch_scope.reset(bound)

    async def close(self, grace_seconds: float = 5) -> None:
        """Bound graceful drain; stuck handlers retain admission until actual exit."""
        if self._closed:
            return
        self._closed = True
        self._dirty.clear()
        self._stamps.clear()
        self._wake.set()
        self.handler.close()
        with anyio.move_on_after(grace_seconds):
            while self._leases:
                await self._changed.wait()
        for lease in tuple(self._leases.values()):
            lease.cancel.cancel()
        # Give cooperative handlers a cleanup opportunity, without claiming that
        # a noncooperative Python task can be forcibly terminated.
        await anyio.lowlevel.checkpoint()


def install_catalog_subscriptions(server: FastMCP, *, remote_hosted: bool) -> CatalogSubscriptions:
    runtime = CatalogSubscriptions(server, remote_hosted=remote_hosted)
    server._catalog_subscriptions = runtime
    return runtime
