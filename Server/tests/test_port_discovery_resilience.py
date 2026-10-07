"""Discovery remains complete while status timestamps and registry files vary."""

from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor
import json
import os
import socket
import struct
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
    isolated_registry: Path,
    heartbeat: str | None,
) -> None:
    older = isolated_registry / "unity-mcp-status-older.json"
    older.write_text(
        json.dumps(
            {"project_path": "/Owned/Older/Assets", "unity_port": 6401, "last_heartbeat": heartbeat}
        ),
        encoding="utf-8",
    )
    old_timestamp = datetime(2026, 1, 1, tzinfo=timezone.utc).timestamp()
    os.utime(older, (old_timestamp, old_timestamp))
    newer = isolated_registry / "unity-mcp-status-newer.json"
    newer.write_text(
        json.dumps(
            {
                "project_path": "/Owned/Newer/Assets",
                "unity_port": 6402,
                "last_heartbeat": "2026-01-02T00:00:00Z",
            }
        ),
        encoding="utf-8",
    )

    instances = PortDiscovery.discover_all_unity_instances()

    assert [instance.hash for instance in instances] == ["newer", "older"]


@pytest.mark.parametrize("newest_first", [False, True])
def test_duplicate_port_keeps_newest_status_with_mixed_timestamp_formats(
    isolated_registry: Path,
    monkeypatch: pytest.MonkeyPatch,
    newest_first: bool,
) -> None:
    older = isolated_registry / "unity-mcp-status-older.json"
    older.write_text(
        json.dumps({"project_path": "/Owned/Older/Assets", "unity_port": 6401}), encoding="utf-8"
    )
    old_timestamp = datetime(2026, 1, 1, tzinfo=timezone.utc).timestamp()
    os.utime(older, (old_timestamp, old_timestamp))
    newer = isolated_registry / "unity-mcp-status-newer.json"
    newer.write_text(
        json.dumps(
            {
                "project_path": "/Owned/Newer/Assets",
                "unity_port": 6401,
                "last_heartbeat": "2026-01-02T00:00:00Z",
            }
        ),
        encoding="utf-8",
    )
    paths = [str(newer), str(older)] if newest_first else [str(older), str(newer)]
    monkeypatch.setattr("transport.legacy.port_discovery.glob.glob", lambda pattern: paths)

    instances = PortDiscovery.discover_all_unity_instances()

    assert [instance.hash for instance in instances] == ["newer"]


def test_disappearing_port_registry_does_not_hide_surviving_candidate(
    isolated_registry: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    missing = isolated_registry / "unity-mcp-port-deleted.json"
    surviving = isolated_registry / "unity-mcp-port-surviving.json"
    surviving.write_text(json.dumps({"unity_port": 6401}), encoding="utf-8")
    monkeypatch.setattr(
        "transport.legacy.port_discovery.glob.glob", lambda pattern: [str(missing), str(surviving)]
    )

    assert PortDiscovery.list_candidate_files() == [surviving]
    assert PortDiscovery.get_port_config() == {"unity_port": 6401}


@pytest.mark.parametrize("responding", [True, False])
def test_duplicate_port_is_probed_once_per_scan_without_losing_newest_metadata(
    isolated_registry,
    monkeypatch,
    responding,
):
    now = datetime.now(timezone.utc).timestamp()
    for index in range(2):
        path = isolated_registry / f"unity-mcp-status-{index}.json"
        path.write_text(
            json.dumps(
                {
                    "project_path": f"/Owned/Project{index}/Assets",
                    "unity_port": 6401,
                    "reloading": not responding,
                }
            ),
            encoding="utf-8",
        )
        os.utime(path, (now - 2 + index, now - 2 + index))
    calls = []
    monkeypatch.setattr(
        PortDiscovery, "_try_probe_unity_mcp", lambda port: calls.append(port) or responding
    )

    first = PortDiscovery.discover_all_unity_instances()
    assert calls == [6401]
    assert [instance.hash for instance in first] == ["1"]
    assert first[0].status == ("running" if responding else "reloading")
    second = PortDiscovery.discover_all_unity_instances()
    assert calls == [6401, 6401], "Probe result persisted beyond its discovery scan"
    assert [instance.id for instance in first] == [instance.id for instance in second]


def test_duplicate_port_memo_uses_real_framed_probe_and_refreshes_next_scan(monkeypatch, tmp_path):
    monkeypatch.setenv("UNITY_MCP_STATUS_DIR", str(tmp_path))
    probes = []
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(2)
        listener.settimeout(2)
        port = listener.getsockname()[1]
        for index in range(2):
            path = tmp_path / f"unity-mcp-status-{index}.json"
            path.write_text(
                json.dumps({"project_path": f"/Owned/Project{index}/Assets", "unity_port": port}),
                encoding="utf-8",
            )
            os.utime(path, (index + 1, index + 1))

        def serve():
            for _ in range(2):
                peer, _ = listener.accept()
                with peer:
                    peer.settimeout(2)
                    peer.sendall(b"MCP/0.1 FRAMING=1\n")
                    frame = bytearray()
                    while len(frame) < 12:
                        chunk = peer.recv(12 - len(frame))
                        assert chunk, "Discovery disconnected before its framed ping"
                        frame.extend(chunk)
                    assert bytes(frame) == struct.pack(">Q", 4) + b"ping"
                    probes.append(port)
                    payload = b'{"status":"success","result":{"message":"pong"}}'
                    peer.sendall(struct.pack(">Q", len(payload)) + payload)

        with ThreadPoolExecutor(max_workers=1) as executor:
            server = executor.submit(serve)
            first = PortDiscovery.discover_all_unity_instances()
            assert probes == [port]
            second = PortDiscovery.discover_all_unity_instances()
            server.result(timeout=2)
    assert probes == [port, port]
    assert [instance.hash for instance in first] == ["1"]
    assert [instance.id for instance in first] == [instance.id for instance in second]
