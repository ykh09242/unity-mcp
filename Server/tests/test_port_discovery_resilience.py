"""Discovery remains complete while status timestamps and registry files vary."""

from datetime import datetime, timezone
import json
import os
from pathlib import Path

import pytest

from transport.legacy.port_discovery import PortDiscovery


@pytest.fixture
def isolated_registry(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    monkeypatch.setenv("UNITY_MCP_STATUS_DIR", str(tmp_path))
    monkeypatch.setattr(PortDiscovery, "_try_probe_unity_mcp", lambda port: True)
    return tmp_path


@pytest.mark.parametrize("heartbeat", [None, "2026-01-01T00:00:00", "2026-01-01T00:00:00Z"])
def test_mixed_heartbeat_formats_do_not_break_instance_sorting(
    isolated_registry: Path, heartbeat: str | None,
) -> None:
    older = isolated_registry / "unity-mcp-status-older.json"
    older.write_text(json.dumps({"project_path": "/Owned/Older/Assets", "unity_port": 6401,
                                 "last_heartbeat": heartbeat}), encoding="utf-8")
    old_timestamp = datetime(2026, 1, 1, tzinfo=timezone.utc).timestamp()
    os.utime(older, (old_timestamp, old_timestamp))
    newer = isolated_registry / "unity-mcp-status-newer.json"
    newer.write_text(json.dumps({"project_path": "/Owned/Newer/Assets", "unity_port": 6402,
                                 "last_heartbeat": "2026-01-02T00:00:00Z"}), encoding="utf-8")

    instances = PortDiscovery.discover_all_unity_instances()

    assert [instance.hash for instance in instances] == ["newer", "older"]


@pytest.mark.parametrize("newest_first", [False, True])
def test_duplicate_port_keeps_newest_status_with_mixed_timestamp_formats(
    isolated_registry: Path, monkeypatch: pytest.MonkeyPatch, newest_first: bool,
) -> None:
    older = isolated_registry / "unity-mcp-status-older.json"
    older.write_text(json.dumps({"project_path": "/Owned/Older/Assets", "unity_port": 6401}),
                     encoding="utf-8")
    old_timestamp = datetime(2026, 1, 1, tzinfo=timezone.utc).timestamp()
    os.utime(older, (old_timestamp, old_timestamp))
    newer = isolated_registry / "unity-mcp-status-newer.json"
    newer.write_text(json.dumps({"project_path": "/Owned/Newer/Assets", "unity_port": 6401,
                                 "last_heartbeat": "2026-01-02T00:00:00Z"}), encoding="utf-8")
    paths = [str(newer), str(older)] if newest_first else [str(older), str(newer)]
    monkeypatch.setattr("transport.legacy.port_discovery.glob.glob", lambda pattern: paths)

    instances = PortDiscovery.discover_all_unity_instances()

    assert [instance.hash for instance in instances] == ["newer"]


def test_disappearing_port_registry_does_not_hide_surviving_candidate(
    isolated_registry: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    missing = isolated_registry / "unity-mcp-port-deleted.json"
    surviving = isolated_registry / "unity-mcp-port-surviving.json"
    surviving.write_text(json.dumps({"unity_port": 6401}), encoding="utf-8")
    monkeypatch.setattr("transport.legacy.port_discovery.glob.glob",
                        lambda pattern: [str(missing), str(surviving)])

    assert PortDiscovery.list_candidate_files() == [surviving]
    assert PortDiscovery.get_port_config() == {"unity_port": 6401}
