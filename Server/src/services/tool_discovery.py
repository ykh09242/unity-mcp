"""Configured discovery profiles and bounded, project-bound modern selection."""

import asyncio
import hashlib
import json
import os
import time

from fastmcp.server.dependencies import get_context
from fastmcp.server.transforms import Transform, Visibility
from mcp.types import ToolListChangedNotification

from core.sdk_compatibility import is_reviewed_sdk
from services.registry import DEFAULT_ENABLED_GROUPS, TOOL_GROUPS
from transport.protocol import is_sessionless


REFRESH_GUIDANCE = (
    "Selection updated; Unity availability and permissions still apply. "
    "Refresh tools/list after tools/list_changed. If the client uses a fixed inventory, "
    "start the server with UNITY_MCP_TOOL_PROFILE=full and reconnect. "
    "Reconnect alone does not preserve a legacy session selection."
)
UNSCOPED_GUIDANCE = (
    "This sessionless connection has no supported stable client scope and resolved project. "
    "Use a stateful handshake, a project-bound stdio connection, or authenticated SDK scope. "
    "Alternatively start the server with UNITY_MCP_TOOL_PROFILE=full and reconnect. "
    "Editor toggles still control availability."
)


class ToolDiscovery(Transform):
    """Keep schemas intact; store one finite override map per trusted scope/project.

    The server-owned SessionStateStore supplies admission accounting, idle expiry
    and shutdown cleanup. This preference lifetime never interrupts native work.
    Legacy clients continue using the SDK's connection-specific visibility rules.
    """

    def __init__(self, server, definitions, groups, *, hosted):
        self.profile = os.environ.get("UNITY_MCP_TOOL_PROFILE", "full" if hosted else "core")
        if self.profile not in {"minimal", "core", "full"}:
            raise ValueError("UNITY_MCP_TOOL_PROFILE must be minimal, core or full")
        self.default_groups = (
            set(TOOL_GROUPS)
            if self.profile == "full"
            else set(DEFAULT_ENABLED_GROUPS)
            if self.profile == "core"
            else set()
        )
        self.definitions = definitions
        self.groups = groups
        self.store = getattr(server, "_unity_session_state_store", None)
        self._supported = is_reviewed_sdk()
        self._lock = asyncio.Lock()

    async def scope_key(self, ctx):
        if not self._supported or self.store is None or not is_sessionless(ctx):
            return None
        # Reviewed SDK API: HTTP without a verified SDK principal returns None.
        # Never substitute a caller-supplied client ID or an API key string.
        scope = ctx._session_scope_key()
        project = await ctx.get_state("unity_instance")
        owner = await ctx.get_state("user_id")
        if scope is None or not isinstance(project, str) or "@" not in project:
            return None
        project_hash = project.rpartition("@")[2]
        if not project_hash:
            return None
        binding = json.dumps([scope, owner, project_hash], separators=(",", ":"))
        return "unity-tool-selection:" + hashlib.sha256(binding.encode()).hexdigest()

    async def _overrides(self):
        try:
            ctx = get_context()
        except RuntimeError:
            return {}
        key = await self.scope_key(ctx)
        if key is None:
            return {}
        async with self._lock:
            record = await self.store.get(key)
            if record is None:
                return {}
            # Renew only active preferences, at most hourly. Idle selections
            # expire after the SDK's 24-hour retention period.
            if time.time() - record["touched"] >= 3600:
                record["touched"] = time.time()
                await self.store.put(key, record, ttl=86400)
            return record["overrides"]

    async def update(self, ctx, names=(), *, enabled=None, reset=False):
        key = await self.scope_key(ctx)
        if key is None:
            raise ValueError(UNSCOPED_GUIDANCE)
        async with self._lock:
            record = await self.store.get(key)
            previous = record["overrides"] if record else {}
            updated = {} if reset else dict(previous)
            if not reset:
                for name in names:
                    if name not in self.groups or self.groups[name] is None:
                        continue
                    if enabled == (self.groups[name] in self.default_groups):
                        updated.pop(name, None)
                    else:
                        updated[name] = enabled
            changed = updated != previous
            if updated:
                await self.store.put(key, {"overrides": updated, "touched": time.time()}, ttl=86400)
            elif record is not None:
                await self.store.delete(key)
        if changed:
            await ctx.send_notification(ToolListChangedNotification())
        return changed

    async def list_tools(self, tools):
        overrides = await self._overrides()
        for enabled in (False, True):
            names = {name for name, value in overrides.items() if value is enabled}
            if names:
                tools = await Visibility(enabled, names=names, components={"tool"}).list_tools(
                    tools
                )
        return tools

    async def get_tool(self, name, call_next, *, version=None):
        overrides = await self._overrides()
        if name in overrides:
            return await Visibility(overrides[name], names={name}, components={"tool"}).get_tool(
                name, call_next, version=version
            )
        return await call_next(name, version=version)
