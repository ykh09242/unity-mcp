"""Unit tests for utils.log_paths.resolve_log_dir."""

from __future__ import annotations

import os
import ntpath

import pytest

from utils.log_paths import resolve_log_dir


class TestEnvOverride:
    def test_override_wins_on_macos(self):
        path = resolve_log_dir(
            platform="darwin",
            env={"UNITY_MCP_LOG_DIR": "/tmp/custom-logs"},
        )
        assert path == "/tmp/custom-logs"

    def test_override_wins_on_windows(self):
        path = resolve_log_dir(
            platform="win32",
            env={"UNITY_MCP_LOG_DIR": "C:/tmp/custom-logs", "LOCALAPPDATA": r"C:\ignored"},
        )
        assert path == "C:/tmp/custom-logs"

    def test_override_wins_on_linux(self):
        path = resolve_log_dir(
            platform="linux",
            env={"UNITY_MCP_LOG_DIR": "/tmp/custom-logs", "XDG_STATE_HOME": "/ignored"},
        )
        assert path == "/tmp/custom-logs"

    def test_override_expands_user(self):
        path = resolve_log_dir(
            platform="linux",
            env={"UNITY_MCP_LOG_DIR": "~/my-logs"},
        )
        assert path == os.path.expanduser("~/my-logs")
        assert "~" not in path


def _norm(p: str) -> str:
    """Normalize for cross-host comparison: os.path.expanduser can leave
    mixed separators when a foreign platform is injected during testing."""
    return os.path.normpath(p)


class TestPlatformDefaults:
    def test_macos_uses_application_support(self):
        path = resolve_log_dir(platform="darwin", env={})
        assert _norm(path).endswith(_norm("Library/Application Support/UnityMCP/Logs"))
        assert "~" not in path

    def test_windows_uses_localappdata_env(self):
        path = resolve_log_dir(
            platform="win32",
            env={"LOCALAPPDATA": r"C:\Users\alice\AppData\Local"},
        )
        assert _norm(path) == _norm(
            os.path.join(r"C:\Users\alice\AppData\Local", "UnityMCP", "Logs")
        )

    def test_windows_falls_back_when_localappdata_missing(self):
        path = resolve_log_dir(platform="win32", env={})
        # Fallback expands ~/AppData/Local; assert shape, not the exact user home.
        assert _norm(path).endswith(_norm("AppData/Local/UnityMCP/Logs"))
        assert "~" not in path

    def test_linux_uses_xdg_state_home(self):
        path = resolve_log_dir(
            platform="linux",
            env={"XDG_STATE_HOME": "/home/alice/.local/state"},
        )
        assert _norm(path) == _norm(os.path.join("/home/alice/.local/state", "UnityMCP", "Logs"))

    def test_linux_falls_back_to_default_xdg_path(self):
        path = resolve_log_dir(platform="linux", env={})
        assert _norm(path).endswith(_norm(".local/state/UnityMCP/Logs"))
        assert "~" not in path

    @pytest.mark.parametrize("unix_like", ["freebsd", "openbsd", "sunos5"])
    def test_unknown_unix_variants_follow_linux_branch(self, unix_like):
        path = resolve_log_dir(
            platform=unix_like,
            env={"XDG_STATE_HOME": "/var/state"},
        )
        assert _norm(path) == _norm(os.path.join("/var/state", "UnityMCP", "Logs"))


class TestDefaults:
    def test_no_args_reads_live_environment(self, monkeypatch):
        """With no args, should read sys.platform and os.environ directly."""
        expected = os.path.abspath("/tmp/live-env-test")
        monkeypatch.setenv("UNITY_MCP_LOG_DIR", expected)
        assert resolve_log_dir() == expected


@pytest.mark.parametrize("override", ["logs", "../logs", "C:logs"])
def test_relative_override_rejected_independently_of_working_directory(
    tmp_path, monkeypatch, override
):
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    for folder in (first, second):
        monkeypatch.chdir(folder)
        with pytest.raises(ValueError, match="absolute"):
            resolve_log_dir(env={"UNITY_MCP_LOG_DIR": override})
    assert list(first.iterdir()) == list(second.iterdir()) == []


@pytest.mark.parametrize("override", [r"\logs", "/logs"])
def test_windows_drive_relative_root_is_rejected_on_legacy_python(monkeypatch, override):
    # Python 3.11/3.12 treated a single Windows slash as absolute. Emulate that
    # documented runtime contract so current Python also exercises the regression.
    original = ntpath.isabs
    monkeypatch.setattr(
        ntpath,
        "isabs",
        lambda value: os.fspath(value).startswith(("/", "\\")) or original(value),
    )
    with pytest.raises(ValueError, match="absolute"):
        resolve_log_dir(platform="win32", env={"UNITY_MCP_LOG_DIR": override})


@pytest.mark.parametrize("override", [r"C:\logs", "C:/logs", r"\\server\share\logs"])
def test_windows_fully_qualified_override_remains_supported(override):
    assert resolve_log_dir(platform="win32", env={"UNITY_MCP_LOG_DIR": override}) == override
