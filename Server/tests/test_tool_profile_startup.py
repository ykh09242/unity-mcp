"""Explicit startup profiles work before a fixed client captures its tool list."""

import os
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from services.tool_discovery import ToolDiscovery


@pytest.fixture
def entry(monkeypatch, tmp_path):
    monkeypatch.setenv("UNITY_MCP_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("UNITY_MCP_TELEMETRY_ENABLED", "false")
    monkeypatch.setenv("UNITY_MCP_HTTP_HOST", "127.0.0.1")
    monkeypatch.setenv("UNITY_MCP_HTTP_PORT", "8080")
    monkeypatch.setenv("UNITY_MCP_HTTP_REMOTE_HOSTED", "false")
    monkeypatch.delenv("UNITY_MCP_TOOL_PROFILE", raising=False)
    monkeypatch.delenv("UNITY_MCP_TOOL_GROUPS", raising=False)
    import main

    return main


@pytest.mark.parametrize(
    ("cli", "environment", "expected"),
    [
        (None, None, "core"),
        (None, "full", "full"),
        ("full", "core", "full"),
        ("core", "full", "core"),
        ("minimal", "full", "minimal"),
    ],
)
def test_startup_profile_is_resolved_before_server_construction(
    entry, monkeypatch, cli, environment, expected
):
    if environment is not None:
        monkeypatch.setenv("UNITY_MCP_TOOL_PROFILE", environment)
    arguments = ["unity-mcp", "--project-scoped-tools"]
    if cli is not None:
        arguments.extend(["--tool-profile", cli])
    monkeypatch.setattr(entry.sys, "argv", arguments)
    server = Mock()
    profiles = []

    def create(*_args, **_kwargs):
        profiles.append(ToolDiscovery(SimpleNamespace(), {}, {}, hosted=False).profile)
        return server

    monkeypatch.setattr(entry, "create_mcp_server", create)
    try:
        entry.main()
        assert profiles == [expected]
        server.run.assert_called_once_with(transport="stdio")
    finally:
        # The real entry point intentionally applies CLI settings to its process environment.
        os.environ.pop("UNITY_MCP_TOOL_PROFILE", None)


def test_invalid_profile_refuses_before_server_or_pidfile(entry, monkeypatch, tmp_path):
    pidfile = tmp_path / "never-created.pid"
    monkeypatch.setattr(
        entry.sys,
        "argv",
        ["unity-mcp", "--tool-profile", "typo", "--pidfile", str(pidfile)],
    )
    create = Mock()
    monkeypatch.setattr(entry, "create_mcp_server", create)
    with pytest.raises(SystemExit) as error:
        entry.main()
    assert error.value.code == 2
    create.assert_not_called()
    assert not pidfile.exists()


@pytest.mark.parametrize(
    ("arguments", "environment", "expected"),
    [
        ([], "core,vfx", {"core", "vfx"}),
        (["--tool-groups", "vfx, docs,vfx"], "core", {"vfx", "docs"}),
        (["--tool-profile", "minimal"], "core,vfx", set()),
    ],
)
def test_startup_groups_respect_explicit_cli_selection(
    entry, monkeypatch, arguments, environment, expected
):
    monkeypatch.setenv("UNITY_MCP_TOOL_PROFILE", "full")
    monkeypatch.setenv("UNITY_MCP_TOOL_GROUPS", environment)
    monkeypatch.setattr(entry.sys, "argv", ["unity-mcp", "--transport", "stdio", *arguments])
    observed = []

    def create(*_args, **_kwargs):
        discovery = ToolDiscovery(SimpleNamespace(), {}, {}, hosted=False)
        observed.append((discovery.profile, discovery.default_groups))
        return Mock()

    monkeypatch.setattr(entry, "create_mcp_server", create)
    entry.main()
    assert observed == [("groups" if expected else "minimal", expected)]


@pytest.mark.parametrize(
    ("arguments", "environment"),
    [
        (["--tool-groups", "core,unknown"], None),
        (["--tool-groups", "core,"], None),
        ([], ""),
        ([], "unknown"),
        (["--tool-groups", "core", "--tool-profile", "full"], None),
    ],
)
def test_invalid_startup_groups_refuse_before_server_or_pidfile(
    entry, monkeypatch, tmp_path, arguments, environment
):
    if environment is not None:
        monkeypatch.setenv("UNITY_MCP_TOOL_GROUPS", environment)
    pidfile = tmp_path / "never-created.pid"
    monkeypatch.setattr(entry.sys, "argv", ["unity-mcp", "--pidfile", str(pidfile), *arguments])
    create = Mock()
    monkeypatch.setattr(entry, "create_mcp_server", create)
    with pytest.raises(SystemExit) as error:
        entry.main()
    assert error.value.code == 2
    create.assert_not_called()
    assert not pidfile.exists()
