"""Bounded sidecars record resolver decisions without retaining credential material."""

import hashlib
import json
import math
import re
from collections.abc import Mapping
from urllib.parse import urlsplit
from typing import assert_never

from models.effective_config import (
    ConfigOrigin,
    ConfigScope,
    ConfigValue,
    EffectiveConfigEntry,
    EffectiveConfigReport,
)

_SECRET_FIELD = re.compile(
    r"token|secret|password|credential|authorization|header|(?:^|_)key$", re.I
)
_SECRET_TEXT = re.compile(
    r"(?:authorization|bearer|api[_-]?key|token|password|secret)\s*[:= ]", re.I
)


def safe_config_value(name: str, value: ConfigValue) -> ConfigValue:
    """Remove credentials before any value enters a diagnostic model."""
    if value is None:
        return None
    if _SECRET_FIELD.search(name):
        return "<redacted>"
    match value:
        case str():
            if _SECRET_TEXT.search(value):
                return "<redacted>"
            if value and ("://" in value or value.startswith("//") or name.endswith("_url")):
                try:
                    parsed = urlsplit(value)
                    host = parsed.hostname
                    if not host or not re.fullmatch(r"[a-zA-Z0-9._:-]{1,255}", host):
                        return "<redacted-url>"
                    authority = f"[{host}]" if ":" in host else host
                    port = f":{parsed.port}" if parsed.port is not None else ""
                    prefix = f"{parsed.scheme}://" if parsed.scheme else "//"
                    return f"{prefix}{authority}{port}/"[:512]
                except ValueError:
                    return "<redacted-url>"
            if any(marker in value for marker in ("?", "#", "@")) and name.endswith("host"):
                return "<redacted>"
            return value[:512]
        case float():
            return value if math.isfinite(value) else "<non-finite>"
        case list():
            return [str(safe_config_value(name, item)) for item in value[:64]]
        case int() | bool():
            return value
        case unreachable:
            assert_never(unreachable)


def _fingerprint(value: ConfigValue) -> bytes:
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode("utf-8")).digest()


class ConfigurationTrace:
    """Mutable sidecar for one resolver/config instance; snapshots are immutable models."""

    def __init__(self, defaults: Mapping[str, ConfigValue]) -> None:
        self._entries = {
            name: EffectiveConfigEntry(
                value=safe_config_value(name, value), default=safe_config_value(name, value)
            )
            for name, value in defaults.items()
        }
        self._fingerprints = {name: _fingerprint(value) for name, value in defaults.items()}

    def declare(self, name: str, default: ConfigValue) -> None:
        """Declare a startup-only field only when its resolver actually runs."""
        self._entries[name] = EffectiveConfigEntry(
            value=safe_config_value(name, default), default=safe_config_value(name, default)
        )
        self._fingerprints[name] = _fingerprint(default)

    def record(self, name: str, value: ConfigValue, origin: ConfigOrigin) -> None:
        """Record a decision at its resolver, never the unselected raw inputs."""
        default = self._entries[name].default if name in self._entries else None
        self._entries[name] = EffectiveConfigEntry(
            **origin.model_dump(), value=safe_config_value(name, value), default=default
        )
        self._fingerprints[name] = _fingerprint(value)

    def origin(self, name: str, value: ConfigValue) -> ConfigOrigin:
        """Retain a value's previous captured origin when the resolver preserves it."""
        if self._fingerprints[name] != _fingerprint(value):
            return ConfigOrigin(
                source="runtime",
                source_detail="unobserved_assignment",
                diagnostics=("origin_unavailable",),
            )
        entry = self._entries[name]
        return ConfigOrigin(
            source=entry.source,
            source_detail=entry.source_detail,
            overrides=entry.overrides,
            diagnostics=entry.diagnostics,
        )

    def report(
        self, scope: ConfigScope, current: Mapping[str, ConfigValue]
    ) -> EffectiveConfigReport:
        """Unobserved mutations report runtime origin instead of inventing provenance."""
        entries = dict(self._entries)
        for name, value in current.items():
            if name not in entries or self._fingerprints[name] != _fingerprint(value):
                entries[name] = EffectiveConfigEntry(
                    value=safe_config_value(name, value),
                    default=entries[name].default if name in entries else None,
                    source="runtime",
                    source_detail="unobserved_assignment",
                    diagnostics=("origin_unavailable",),
                )
        return EffectiveConfigReport(
            scope=scope,
            running_server_config_observed=scope == "running_server_configuration",
            fields=entries,
        )
