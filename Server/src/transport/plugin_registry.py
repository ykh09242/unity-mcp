"""In-memory registry for connected Unity plugin sessions."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

import asyncio

from core.config import config
from models.models import ToolDefinitionModel


class ToolSettingsConflict(ValueError):
    """A client revision cannot replace the current live session's intent."""


@dataclass(slots=True)
class PluginSession:
    """Represents a single Unity plugin connection."""

    session_id: str
    project_name: str
    project_hash: str
    unity_version: str
    registered_at: datetime
    connected_at: datetime
    tools: dict[str, ToolDefinitionModel] = field(default_factory=dict)
    tools_revision: int = 0
    tools_known: bool = False
    settings_epoch: str | None = None
    desired_revision: int = 0
    project_id: str | None = None
    # Full path to project root (for focus nudging)
    project_path: str | None = None
    user_id: str | None = None  # Associated user id (None for local mode)


class PluginRegistry:
    """Stores active plugin sessions in-memory.

    The registry is optimised for quick lookup by either ``session_id`` or
    ``project_hash`` (which is used as the canonical "instance id" across the
    HTTP command routing stack).

    In remote-hosted mode, sessions are scoped by (user_id, project_hash) composite key
    to ensure session isolation between users.
    """

    MAX_SESSIONS = 256
    MAX_SESSIONS_PER_USER = 32

    def __init__(self) -> None:
        self._sessions: dict[str, PluginSession] = {}
        # In local mode: project_hash -> session_id
        # In remote mode: (user_id, project_hash) -> session_id
        self._hash_to_session: dict[str, str] = {}
        self._user_hash_to_session: dict[tuple[str, str], str] = {}
        self._lock = asyncio.Lock()
        self._change_event = asyncio.Event()

    @property
    def change_event(self) -> asyncio.Event:
        """Snapshot before lookup so a change before waiting cannot be lost."""
        return self._change_event

    def notify_changed(self) -> None:
        """Publish synchronously with mapping changes, or wake a replaced registry."""
        event, self._change_event = self._change_event, asyncio.Event()
        event.set()

    async def wait_for_change(self, observed: asyncio.Event, timeout: float) -> bool:
        """Each caller owns its wait task; cancellation never cancels the shared event."""
        async with self._lock:
            if observed is not self._change_event:
                return True
        try:
            await asyncio.wait_for(observed.wait(), timeout)
            return True
        except TimeoutError:
            return False

    async def register(
        self,
        session_id: str,
        project_name: str,
        project_hash: str,
        unity_version: str,
        project_path: str | None = None,
        user_id: str | None = None,
        *,
        _publish=None,
    ) -> tuple[PluginSession, str | None]:
        """Register (or replace) a plugin session.

        If an existing session already claims the same ``project_hash`` (and ``user_id``
        in remote-hosted mode) it will be replaced, ensuring that reconnect scenarios
        always map to the latest WebSocket connection.

        Returns:
            A tuple of (new_session, evicted_session_id). The evicted ID is None
            when no previous session was replaced.
        """
        if config.http_remote_hosted and not user_id:
            raise ValueError("user_id is required in remote-hosted mode")

        async with self._lock:
            previous = (
                self._user_hash_to_session.get((user_id, project_hash))
                if user_id
                else self._hash_to_session.get(project_hash)
            )
            if previous is None:
                if len(self._sessions) >= self.MAX_SESSIONS:
                    raise ValueError("Plugin session limit reached")
                if (
                    user_id
                    and sum(session.user_id == user_id for session in self._sessions.values())
                    >= self.MAX_SESSIONS_PER_USER
                ):
                    raise ValueError("User plugin session limit reached")
            now = datetime.now(timezone.utc)
            session = PluginSession(
                session_id=session_id,
                project_name=project_name,
                project_hash=project_hash,
                unity_version=unity_version,
                registered_at=now,
                connected_at=now,
                project_path=project_path,
                user_id=user_id,
            )
            if _publish is not None:
                prospective = {
                    sid: entry
                    for sid, entry in self._sessions.items()
                    if sid not in {previous, session_id}
                }
                prospective[session_id] = session
                _publish(session, prospective)

            # Remove old mapping for this hash if it existed under a different session
            evicted_session_id: str | None = None
            if user_id:
                # Remote-hosted mode: use composite key (user_id, project_hash)
                composite_key = (user_id, project_hash)
                previous_session_id = self._user_hash_to_session.get(composite_key)
                if previous_session_id and previous_session_id != session_id:
                    self._sessions.pop(previous_session_id, None)
                    evicted_session_id = previous_session_id
                self._user_hash_to_session[composite_key] = session_id
            else:
                # Local mode: use project_hash only
                previous_session_id = self._hash_to_session.get(project_hash)
                if previous_session_id and previous_session_id != session_id:
                    self._sessions.pop(previous_session_id, None)
                    evicted_session_id = previous_session_id
                self._hash_to_session[project_hash] = session_id

            self._sessions[session_id] = session
            self.notify_changed()
            return session, evicted_session_id

    async def clear(self) -> None:
        """Release all session state when the owning server shuts down."""
        async with self._lock:
            self._sessions.clear()
            self._hash_to_session.clear()
            self._user_hash_to_session.clear()
            self.notify_changed()

    async def touch(self, session_id: str) -> None:
        """Update the ``connected_at`` timestamp when a heartbeat is received."""

        async with self._lock:
            session = self._sessions.get(session_id)
            if session:
                session.connected_at = datetime.now(timezone.utc)

    async def unregister(self, session_id: str, *, _publish=None) -> bool:
        """Remove a plugin session from the registry."""

        async with self._lock:
            session = self._sessions.get(session_id)
            if session:
                if _publish is not None:
                    _publish(
                        session,
                        {sid: entry for sid, entry in self._sessions.items() if sid != session_id},
                    )
                self._sessions.pop(session_id)
                # Clean up hash mappings
                if session.project_hash in self._hash_to_session:
                    mapped = self._hash_to_session.get(session.project_hash)
                    if mapped == session_id:
                        del self._hash_to_session[session.project_hash]

                # Clean up user-scoped mappings
                if session.user_id:
                    composite_key = (session.user_id, session.project_hash)
                    if composite_key in self._user_hash_to_session:
                        mapped = self._user_hash_to_session.get(composite_key)
                        if mapped == session_id:
                            del self._user_hash_to_session[composite_key]
                self.notify_changed()
                return True
            return False

    async def register_tools_for_session(
        self, session_id: str, tools: list[ToolDefinitionModel], *, _publish=None, _settings=None
    ) -> bool:
        """Register tools for a specific session."""
        prepared = {}
        for tool in tools:
            if tool.name in prepared:
                raise ValueError("Duplicate tool name in catalog")
            prepared[tool.name] = tool.model_copy(deep=True)
        async with self._lock:
            session = self._sessions.get(session_id)
            current = (
                self._user_hash_to_session.get((session.user_id, session.project_hash))
                if session is not None and session.user_id
                else self._hash_to_session.get(session.project_hash)
                if session is not None
                else None
            )
            if session is None or current != session_id:
                if _settings is not None:
                    raise ToolSettingsConflict("stale_session")
                return False
            if _settings is not None:
                epoch, revision = _settings
                if session.settings_epoch is not None:
                    if session.settings_epoch != epoch:
                        raise ToolSettingsConflict("epoch_mismatch")
                    if revision < session.desired_revision:
                        raise ToolSettingsConflict("stale_revision")
                    if revision == session.desired_revision and session.tools != prepared:
                        raise ToolSettingsConflict("revision_conflict")
            changed = not session.tools_known or session.tools != prepared
            if _publish is not None:
                _publish(session, prepared)
            if changed:
                session.tools = prepared
                session.tools_known = True
                session.tools_revision += 1
                self.notify_changed()
            if _settings is not None:
                session.settings_epoch, session.desired_revision = _settings
            return changed

    async def get_catalog_snapshot(self, project_hash, user_id=None):
        """Capture membership/revision/ownership under one existing registry lock."""
        if config.http_remote_hosted and not user_id:
            return None
        async with self._lock:
            sid = (
                self._user_hash_to_session.get((user_id, project_hash))
                if user_id
                else self._hash_to_session.get(project_hash)
            )
            session = self._sessions.get(sid)
            if session is None or session.user_id != user_id:
                return None
            return (
                session.session_id,
                session.tools_revision,
                session.tools_known,
                dict(session.tools),
            )

    async def get_session(self, session_id: str) -> PluginSession | None:
        """Fetch a session by its ``session_id``."""

        async with self._lock:
            return self._sessions.get(session_id)

    async def has_tool_for_session(
        self,
        session_id: str,
        project_hash: str,
        user_id: str,
        names: tuple[str, ...],
    ) -> bool:
        """Check current tenant mapping and membership in one registry critical section."""
        if not user_id:
            return False
        async with self._lock:
            session = self._sessions.get(session_id)
            if (
                session is None
                or session.user_id != user_id
                or session.project_hash != project_hash
                or self._user_hash_to_session.get((user_id, project_hash)) != session_id
            ):
                return False
            return any(name in session.tools for name in names)

    async def get_session_id_by_hash(
        self, project_hash: str, user_id: str | None = None
    ) -> str | None:
        """Resolve a ``project_hash`` (Unity instance id) to a session id."""

        if user_id:
            async with self._lock:
                return self._user_hash_to_session.get((user_id, project_hash))
        else:
            async with self._lock:
                return self._hash_to_session.get(project_hash)

    async def list_sessions(self, user_id: str | None = None) -> dict[str, PluginSession]:
        """Return a shallow copy of sessions.

        Args:
            user_id: If provided, only return sessions for this user (remote-hosted mode).
                     If None, return all sessions (local mode only).

        Raises:
            ValueError: If ``user_id`` is None while running in remote-hosted mode.
                        This prevents accidentally leaking sessions across users.
        """
        if user_id is None and config.http_remote_hosted:
            raise ValueError("list_sessions requires user_id in remote-hosted mode")

        async with self._lock:
            if user_id is None:
                return dict(self._sessions)
            else:
                return {
                    sid: session
                    for sid, session in self._sessions.items()
                    if session.user_id == user_id
                }


__all__ = ["PluginRegistry", "PluginSession"]
