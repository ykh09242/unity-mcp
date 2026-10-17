"""Bounded project-local CLI defaults; no credentials or persistent session selection."""

import ipaddress
import json
import os
import re
import stat
from collections.abc import Mapping
from pathlib import Path
from typing import Literal

from click.core import ParameterSource
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from cli.utils.config import CLIConfig


class ProjectContextError(ValueError):
    """A stable local rejection that never contains configuration contents."""

    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


class _ContextFile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    schema_version: Literal["unity-mcp/cli-context@1"]
    host: str = Field(default="127.0.0.1", min_length=1, max_length=255)
    port: int = Field(default=8080, ge=1, le=65535)
    timeout: int = Field(default=30, ge=1)
    format: Literal["text", "json", "table"] = "text"
    instance: str = Field(default="", max_length=256)


def is_loopback(host: str) -> bool:
    """Only literal loopback addresses or localhost may use local context auth."""
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host.strip("[]")).is_loopback
    except ValueError:
        return False


def exact_hash(instance: str | None) -> str | None:
    """Reject names, ports and prefixes in context mode, preserving full native hashes."""
    if not instance or any(ord(c) < 32 or ord(c) == 127 for c in instance):
        return None
    value = instance.rsplit("@", 1)[-1]
    return value.lower() if re.fullmatch(r"[0-9a-fA-F]{16}", value) else None


def _unity_root(root: Path) -> bool:
    return (
        (root / "Assets").is_dir()
        and (root / "ProjectSettings").is_dir()
        and (root / "ProjectSettings/ProjectVersion.txt").is_file()
    )


def _root(path: Path) -> Path:
    try:
        root = path.resolve(strict=True)
        if len(str(root)) > 4096 or not _unity_root(root):
            raise ProjectContextError("invalid_project_root")
        return root
    except OSError as error:
        raise ProjectContextError("project_root_unavailable") from error


def _discover() -> Path | None:
    current = Path.cwd().resolve()
    for _ in range(64):
        if _unity_root(current):
            path = current / ".unity-mcp.json"
            return path if path.exists() or path.is_symlink() else None
        if current.parent == current:
            return None
        current = current.parent
    raise ProjectContextError("discovery_limit")


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ProjectContextError("duplicate_context_key")
        result[key] = value
    return result


def _read(path: Path) -> tuple[_ContextFile, Path]:
    try:
        before = path.lstat()
        if path.is_symlink() or getattr(before, "st_file_attributes", 0) & getattr(
            stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400
        ):
            raise ProjectContextError("unsafe_context_file")
        if not stat.S_ISREG(before.st_mode):
            raise ProjectContextError("unsafe_context_file")
        with path.open("rb") as stream:
            opened = os.fstat(stream.fileno())
            if not stat.S_ISREG(opened.st_mode) or (opened.st_dev, opened.st_ino) != (
                before.st_dev,
                before.st_ino,
            ):
                raise ProjectContextError("unsafe_context_file")
            data = stream.read(8193)
        if len(data) > 8192:
            raise ProjectContextError("context_limit")
        raw = json.loads(data.decode("utf-8-sig"), object_pairs_hook=_pairs)
        model = _ContextFile.model_validate(raw)
        if model.instance and exact_hash(model.instance) is None:
            raise ProjectContextError("invalid_context_target")
        if "instance" in model.model_fields_set and not model.instance:
            raise ProjectContextError("invalid_context_target")
        if not is_loopback(model.host):
            raise ProjectContextError("context_requires_local_server")
        return model, _root(path.parent)
    except (OSError, UnicodeError, ValidationError, json.JSONDecodeError, RecursionError) as error:
        raise ProjectContextError("invalid_context") from error


def resolve_project_context(
    config: CLIConfig,
    sources: Mapping[str, ParameterSource | None],
    selected: str | None,
    disabled: bool,
    expected: str | None,
) -> CLIConfig:
    """Merge only default-sourced fields; explicit flags and Click env keep precedence."""
    if selected and disabled:
        raise ProjectContextError("conflicting_context_options")
    try:
        path = Path(selected).absolute() if selected else None if disabled else _discover()
        if path is not None:
            model, root = _read(path)
            for name in ("host", "port", "timeout", "format", "instance"):
                if sources.get(name) == ParameterSource.DEFAULT:
                    setattr(
                        config,
                        "unity_instance" if name == "instance" else name,
                        getattr(model, name) or None
                        if name == "instance"
                        else getattr(model, name),
                    )
            config.expected_project_root = str(root)
        if expected is not None:
            config.expected_project_root = str(_root(Path(expected)))
        if config.expected_project_root and not is_loopback(config.host):
            raise ProjectContextError("context_requires_local_server")
        return config
    except OSError as error:
        raise ProjectContextError("context_unavailable") from error
