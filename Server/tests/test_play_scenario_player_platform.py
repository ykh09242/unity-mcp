"""Native layout and host admission run before starting a Player process."""

import os
from pathlib import Path

import pytest

from cli.utils import play_scenario_player_platform as platform


@pytest.mark.parametrize("target", ["windows64", "linux64", "macos"])
def test_fixed_native_layout_is_resolved_with_real_files(tmp_path, target):
    executable = tmp_path / platform.EXECUTABLES[target]
    executable.parent.mkdir(parents=True, exist_ok=True)
    executable.write_bytes(b"fixture")
    executable.chmod(0o755)
    assert platform.executable_path(tmp_path, target) == executable


def test_foreign_host_is_refused_before_process_creation(monkeypatch):
    monkeypatch.setattr(platform.sys, "platform", "linux")
    with pytest.raises(platform.PlayerPlatformError, match="requires a native win32"):
        platform.require_native_host("windows64")


def test_missing_mac_binary_is_not_treated_as_executable_app_directory(tmp_path):
    (tmp_path / "MCPScenarioPlayer.app").mkdir()
    with pytest.raises(platform.PlayerPlatformError, match="regular file"):
        platform.executable_path(tmp_path, "macos")


@pytest.mark.skipif(os.name == "nt", reason="POSIX execute bit contract")
def test_posix_non_executable_payload_is_refused(tmp_path):
    (tmp_path / platform.EXECUTABLES["linux64"]).write_bytes(b"fixture")
    with pytest.raises(platform.PlayerPlatformError, match="execute permission"):
        platform.executable_path(tmp_path, "linux64")


def test_environment_attributes_actual_host_separately_from_backend(monkeypatch):
    monkeypatch.setattr(platform.sys, "platform", "win32")
    evidence = platform.host_attribution("windows64", "il2cpp", False)
    assert evidence["host_platform"] == "win32"
    assert evidence["scripting_backend"] == "il2cpp"
    assert evidence["graphical_requested"] is False
    assert evidence["cancellation_transport"] == "request-file-then-owned-process-termination"


@pytest.mark.parametrize("backend", ["mono", "il2cpp"])
def test_runtime_backend_requires_native_attribution_instead_of_manifest_echo(backend):
    reproduction = {
        "environment": {
            "platform": "WindowsPlayer",
            "scripting_backend": backend,
            "operating_system": "Windows",
            "architecture": "X64",
        }
    }
    platform.validate_runtime_environment(reproduction, "windows64", backend)
    reproduction["environment"]["scripting_backend"] = "il2cpp" if backend == "mono" else "mono"
    with pytest.raises(platform.PlayerPlatformError, match="does not match"):
        platform.validate_runtime_environment(reproduction, "windows64", backend)
