"""Bound standalone executable layout and native host attribution."""

import os
from pathlib import Path
import platform
import sys
from typing import Final, Literal, TypedDict

from pydantic import JsonValue

BuildTarget = Literal["windows64", "linux64", "macos"]
Backend = Literal["mono", "il2cpp"]
EXECUTABLES: Final[dict[BuildTarget, str]] = {
    "windows64": "MCPScenarioPlayer.exe",
    "linux64": "MCPScenarioPlayer.x86_64",
    "macos": "MCPScenarioPlayer.app/Contents/MacOS/MCPScenarioPlayer",
}
HOSTS: Final[dict[BuildTarget, str]] = {
    "windows64": "win32",
    "linux64": "linux",
    "macos": "darwin",
}


class PlayerPlatformError(RuntimeError):
    """The selected native payload cannot run on this host or has an unsafe layout."""


class HostAttribution(TypedDict):
    build_target: BuildTarget
    scripting_backend: Backend
    host_platform: str
    host_machine: str
    graphical_requested: bool
    cancellation_transport: str


def require_native_host(target: BuildTarget) -> None:
    """Refuse foreign native binaries before creating request/output directories."""
    if sys.platform != HOSTS[target]:
        raise PlayerPlatformError(f"Player target {target} requires a native {HOSTS[target]} host")


def executable_path(root: Path, target: BuildTarget) -> Path:
    """Allow only the fixed platform path, rejecting symlinked parents and missing files."""
    path = root
    for segment in EXECUTABLES[target].split("/"):
        path = path / segment
        if path.is_symlink():
            raise PlayerPlatformError("Player executable or parent must not be a symlink")
    if not path.is_file() or not path.resolve(strict=True).is_relative_to(root):
        raise PlayerPlatformError(
            "Player executable must be a regular file inside the build directory"
        )
    if os.name != "nt" and target != "windows64" and not os.access(path, os.X_OK):
        raise PlayerPlatformError("Native Player executable lacks execute permission")
    return path


def host_attribution(target: BuildTarget, backend: Backend, graphical: bool) -> HostAttribution:
    """Record actual launcher host separately from the selected build's declared backend."""
    return {
        "build_target": target,
        "scripting_backend": backend,
        "host_platform": sys.platform,
        "host_machine": platform.machine(),
        "graphical_requested": graphical,
        "cancellation_transport": "request-file-then-owned-process-termination",
    }


def validate_runtime_environment(
    reproduction: dict[str, JsonValue], target: BuildTarget, backend: Backend
) -> None:
    """Require the native report's actual platform/backend to match the admitted build."""
    environment = reproduction.get("environment")
    expected = {"windows64": "WindowsPlayer", "linux64": "LinuxPlayer", "macos": "OSXPlayer"}[
        target
    ]
    if (
        not isinstance(environment, dict)
        or environment.get("platform") != expected
        or environment.get("scripting_backend") != backend
        or not environment.get("operating_system")
        or not environment.get("architecture")
    ):
        raise PlayerPlatformError(
            "Native Player environment does not match its build target/backend"
        )
