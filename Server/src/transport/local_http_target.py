"""Resolve a local HTTP target against a single snapshot of the visible sessions."""

import os

from core.config import config
from transport.models import SessionDetails, SessionList


def configured_default() -> str | None:
    if config.http_remote_hosted:
        return None
    return os.environ.get("UNITY_MCP_DEFAULT_INSTANCE", "").strip() or None


def normalize_instance_token(instance_token: str | None) -> tuple[str | None, str | None]:
    if not instance_token:
        return None, None
    if "@" in instance_token:
        name_part, _, hash_part = instance_token.rpartition("@")
        return (name_part or None), (hash_part or None)
    return None, instance_token


def select_local_session(
    sessions: SessionList, instance_token: str | None, *, default: str | None = None
) -> tuple[str | None, SessionDetails | None]:
    """Prefer an explicit selector, then the local default, then a sole session."""
    if instance_token is None:
        instance_token = default
    if instance_token is None:
        return (
            next(iter(sessions.sessions.items())) if len(sessions.sessions) == 1 else (None, None)
        )
    if not isinstance(instance_token, str) or not instance_token:
        return None, None
    instance_name, instance_hash = normalize_instance_token(instance_token)
    matches = [pair for pair in sessions.sessions.items() if pair[1].hash == instance_hash]
    if "@" in instance_token:
        if not instance_name or any(pair[1].project != instance_name for pair in matches):
            return None, None
    elif not matches:
        matches = [pair for pair in sessions.sessions.items() if pair[1].project == instance_token]
    return matches[0] if len(matches) == 1 else (None, None)
