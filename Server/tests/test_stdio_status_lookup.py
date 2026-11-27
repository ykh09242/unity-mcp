"""Targeted status reads preserve suffix identity and newest-file semantics."""

import asyncio
import json
import os
from pathlib import Path

import pytest

import transport.legacy.unity_connection as uc


@pytest.fixture
def status_reader(monkeypatch, tmp_path):
    monkeypatch.setattr(uc.Path, "home", lambda: tmp_path)
    directory = tmp_path / ".unity-mcp"
    directory.mkdir()
    return uc.read_status_file, directory


def write_status(directory, hash_value, data, timestamp):
    path = directory / f"unity-mcp-status-{hash_value}.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    os.utime(path, (timestamp, timestamp))
    return path


def test_unique_target_does_not_stat_unrelated_editor_statuses(status_reader, monkeypatch):
    read_status, directory = status_reader
    write_status(directory, "deadbeef", {"reloading": True}, 1)
    for number in range(10):
        write_status(directory, f"{number:08x}", {"reloading": False}, number + 10)
    stat_calls = []
    original_stat = Path.stat

    def tracked_stat(path, *args, **kwargs):
        if path.parent == directory:
            stat_calls.append(path)
        return original_stat(path, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", tracked_stat)
    assert read_status("deadbeef") == {"reloading": True}
    assert stat_calls == [], f"Target read statted {len(stat_calls)} editor statuses"


@pytest.mark.parametrize("selector", ["deadbeef", "beef"])
def test_newer_matching_legacy_suffix_keeps_precedence(status_reader, selector):
    read_status, directory = status_reader
    write_status(directory, "deadbeef", {"reloading": False}, 1)
    write_status(directory, "cafefeeddeadbeef", {"reloading": True}, 2)
    write_status(directory, "ffffffff", {"reloading": False}, 3)
    assert read_status(selector) == {"reloading": True}


def test_untargeted_read_still_selects_newest_editor(status_reader):
    read_status, directory = status_reader
    write_status(directory, "deadbeef", {"reloading": True}, 1)
    write_status(directory, "other", {"reloading": False}, 2)
    assert read_status() == {"reloading": False}


def test_utf8_heartbeat_preserves_project_identity_with_cp949_default(status_reader, monkeypatch):
    read_status, directory = status_reader
    project = "마법성"
    status = {"project_path": f"/projects/{project}/Assets", "unity_port": 6401}
    (directory / "unity-mcp-status-deadbeef.json").write_bytes(
        json.dumps(status, ensure_ascii=False).encode("utf-8")
    )
    open_path = Path.open

    def cp949_open(path, mode="r", buffering=-1, encoding=None, errors=None, newline=None):
        return open_path(path, mode, buffering, encoding or "cp949", errors, newline)

    monkeypatch.setattr(Path, "open", cp949_open)
    monkeypatch.setattr(uc.PortDiscovery, "_try_probe_unity_mcp", lambda port: True)
    [instance] = uc.PortDiscovery.discover_all_unity_instances()
    assert instance.id == f"{project}@deadbeef"
    assert uc.PortDiscovery._read_latest_status() == status
    assert read_status("deadbeef") == read_status() == status


@pytest.mark.skipif(os.name != "nt", reason="Windows atomic replacement sharing")
@pytest.mark.asyncio
async def test_published_heartbeat_remains_readable_while_writer_has_replacement_access(
    status_reader, monkeypatch
):
    from importlib import import_module
    from transport.unity_instance_middleware import UnityInstanceMiddleware

    read_status, directory = status_reader
    generation = "a" * 32
    greetings = []

    async def greet(_reader, peer):
        greetings.append(generation)
        peer.write(
            f"WELCOME UNITY-MCP 2 FRAMING=1 AUTH=HMAC-SHA256 SERVER={generation} "
            f"CHALLENGE={'c' * 64}\n".encode("ascii")
        )
        await peer.drain()
        peer.close()
        await peer.wait_closed()

    pool = uc.UnityConnectionPool()
    monkeypatch.setattr(uc.config, "transport_mode", "stdio")
    monkeypatch.setattr(uc.config, "http_remote_hosted", False)
    monkeypatch.setattr(uc, "get_unity_connection_pool", lambda: pool)
    winapi = import_module("_winapi")
    async with await asyncio.start_server(greet, "127.0.0.1", 0) as server:
        status = {
            "project_path": "/Owned/Game/Assets",
            "unity_port": server.sockets[0].getsockname()[1],
            "listener_generation": generation,
        }
        path = write_status(directory, "deadbeef", status, 1)
        assert [row.id for row in await uc.run_discovery(pool.discover_all_instances)] == [
            "Game@deadbeef"
        ]
        # DELETE is the access needed to replace this directory entry. The
        # publisher still shares read, write and delete; the original file is intact.
        writer = winapi.CreateFile(str(path), 0x10000, 7, 0, winapi.OPEN_EXISTING, 0, 0)
        try:
            assert (
                await UnityInstanceMiddleware()._resolve_instance_value("Game@deadbeef", None)
                == "Game@deadbeef"
            )
            assert read_status("deadbeef") == uc.PortDiscovery._read_latest_status() == status
        finally:
            winapi.CloseHandle(writer)
    assert greetings == [generation, generation]


def test_atomic_replacement_during_discovery_keeps_one_complete_snapshot(
    status_reader, monkeypatch
):
    _, directory = status_reader
    old = {"project_path": "/Owned/Game/Assets", "unity_port": 6400}
    new = {**old, "unity_port": 6401}
    path = write_status(directory, "deadbeef", old, 1)
    replacement = directory / "pending"
    replacement.write_text(json.dumps(new), encoding="utf-8")
    os.utime(replacement, (2, 2))
    load = json.load

    def replace_while_open(reader):
        if os.name == "nt":
            # File.Replace uses ReplaceFileW; os.replace uses MoveFileExW,
            # which cannot replace an open destination on this platform.
            import ctypes
            from ctypes import wintypes

            replace = ctypes.WinDLL("kernel32", use_last_error=True).ReplaceFileW
            replace.argtypes = [
                wintypes.LPCWSTR,
                wintypes.LPCWSTR,
                wintypes.LPCWSTR,
                wintypes.DWORD,
                ctypes.c_void_p,
                ctypes.c_void_p,
            ]
            replace.restype = wintypes.BOOL
            assert replace(str(path), str(replacement), None, 0, None, None), (
                ctypes.get_last_error()
            )
        else:
            replacement.replace(path)
        return load(reader)

    with monkeypatch.context() as during:
        during.setattr(json, "load", replace_while_open)
        entries = uc.PortDiscovery._read_status_entries()
    assert len(entries) == 1
    observed_path, first, modified = entries[0]
    assert observed_path == path and first == old and modified.timestamp() == 1
    entries = uc.PortDiscovery._read_status_entries()
    assert len(entries) == 1
    _, second, modified = entries[0]
    assert second == new and modified.timestamp() == 2


@pytest.mark.parametrize("selector", ["missing", "../outside", "*", "?", "[deadbeef]"])
def test_missing_or_unsafe_target_never_reads_another_editor(status_reader, selector):
    read_status, directory = status_reader
    write_status(directory, "deadbeef", {"reloading": True}, 1)
    assert read_status(selector) is None


def test_malformed_selected_status_does_not_fall_back_to_another_editor(status_reader):
    read_status, directory = status_reader
    (directory / "unity-mcp-status-deadbeef.json").write_text("{", encoding="utf-8")
    write_status(directory, "other", {"reloading": True}, 3)
    assert read_status("deadbeef") is None
