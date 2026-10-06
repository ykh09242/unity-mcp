"""Exact-target discovery avoids unrelated probes without relaxing identity guards."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
import os
import socket
import struct
from types import SimpleNamespace

import pytest

from core.config import config
from transport.legacy.port_discovery import PortDiscovery
import transport.legacy.unity_connection as uc


@pytest.fixture
def target_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("UNITY_MCP_STATUS_DIR", str(tmp_path))
    monkeypatch.delenv("UNITY_MCP_DEFAULT_INSTANCE", raising=False)
    monkeypatch.setattr(uc.UnityConnection, "connect", lambda *args, **kwargs: True)
    monkeypatch.setattr(config, "http_remote_hosted", False)
    clock = [100.0]
    monkeypatch.setattr(uc.time, "time", lambda: clock[0])
    available = {6400, 6402}
    probes = []
    monkeypatch.setattr(PortDiscovery, "_try_probe_unity_mcp", lambda port: probes.append(port) or port in available)
    now = datetime.now(timezone.utc).timestamp()

    def write(name, hash_value, port, offset=0, **extra):
        path = tmp_path / f"unity-mcp-status-{hash_value}.json"
        path.write_text(json.dumps({"project_path": f"/Owned/{name}/Assets",
                                    "unity_port": port, **extra}), encoding="utf-8")
        os.utime(path, (now + offset, now + offset))
        return path

    write("Main", "deadbeef", 6400)
    write("Other", "cafebabe", 6401)
    return uc.UnityConnectionPool(), write, probes, clock, available


def test_exact_target_skips_unrelated_probe_and_retains_root_metadata(target_environment):
    pool, _, probes, _, _ = target_environment
    conn = pool.get_connection("Main@deadbeef")
    assert conn.instance_id == "Main@deadbeef" and conn.port == 6400
    assert probes == [6400]
    assert pool._known_instances[conn.instance_id].path == "/Owned/Main/Assets"
    assert pool._last_full_scan is None or pool._last_full_scan < 95


def test_target_only_cache_does_not_hide_unbound_ambiguity_or_forced_listing(target_environment):
    pool, _, probes, _, available = target_environment
    available.add(6401)
    pool.get_connection("Main@deadbeef")
    with pytest.raises(ConnectionError, match="Multiple Unity instances"):
        pool.get_connection()
    assert set(pool._known_instances) == {"Main@deadbeef", "Other@cafebabe"}
    before = len(probes)
    assert len(pool.discover_all_instances(force_refresh=True)) == 2
    assert len(probes) == before + 2


def test_target_cache_expires_for_changed_port_and_never_caches_failed_probes(target_environment):
    pool, write, probes, clock, _ = target_environment
    conn = pool.get_connection("Main@deadbeef")
    pool.get_connection("Main@deadbeef")
    assert probes == [6400]
    write("Main", "deadbeef", 6402, offset=1)
    clock[0] += 5.0
    assert pool.get_connection("Main@deadbeef") is conn
    assert conn.port == 6402 and probes == [6400, 6402]


def test_newer_other_identity_on_reused_port_forces_full_discovery(target_environment):
    pool, write, probes, _, _ = target_environment
    write("Other", "cafebabe", 6400, offset=1)
    with pytest.raises(ConnectionError, match="Main@deadbeef.*not found"):
        pool.get_connection("Main@deadbeef")
    assert "Main@deadbeef" not in pool._known_instances
    assert probes == [6400, 6400]


def test_newer_selected_identity_wins_same_port_competition(target_environment):
    pool, write, probes, _, _ = target_environment
    write("Other", "cafebabe", 6400, offset=-1)
    assert pool.get_connection("Main@deadbeef").port == 6400
    assert probes == [6400]


@pytest.mark.parametrize("selector", ["Main", "dead", "6400", "/Owned/Main/Assets", "Main@dead", None])
def test_noncanonical_selection_keeps_full_discovery(target_environment, selector):
    pool, _, probes, _, _ = target_environment
    assert pool.get_connection(selector).instance_id == "Main@deadbeef"
    assert sorted(probes) == [6400, 6401]


def test_default_canonical_target_still_uses_full_discovery(target_environment):
    pool, _, probes, _, _ = target_environment
    pool._default_instance_id = "Main@deadbeef"
    assert pool.get_connection().instance_id == "Main@deadbeef"
    assert sorted(probes) == [6400, 6401]


def test_missing_exact_target_falls_back_to_global_semantics(target_environment):
    pool, _, probes, _, _ = target_environment
    with pytest.raises(ConnectionError, match="Missing@ffffffff.*not found"):
        pool.get_connection("Missing@ffffffff")
    assert sorted(probes) == [6400, 6401]


def test_fresh_reload_is_retained_and_next_refresh_probes_again(target_environment):
    pool, write, probes, clock, available = target_environment
    available.remove(6400)
    write("Main", "deadbeef", 6400, reloading=True)
    assert pool.get_connection("Main@deadbeef").instance_id == "Main@deadbeef"
    assert pool._known_instances["Main@deadbeef"].status == "reloading"
    clock[0] += 5
    available.add(6400)
    write("Main", "deadbeef", 6400, offset=1, reloading=False)
    pool.get_connection("Main@deadbeef")
    assert probes == [6400, 6400]
    assert pool._known_instances["Main@deadbeef"].status == "running"


def test_parallel_exact_target_calls_share_refresh(target_environment):
    pool, _, probes, _, _ = target_environment
    with ThreadPoolExecutor(max_workers=3) as executor:
        connections = list(executor.map(pool.get_connection, ["Main@deadbeef"] * 3))
    assert all(conn is connections[0] for conn in connections)
    assert probes == [6400]


def test_full_cache_precedes_target_cache_and_validated_sixteen_hex_id_is_supported(target_environment):
    pool, write, probes, _, _ = target_environment
    write("Long", "0123456789abcdef", 6402, offset=1)
    assert pool.get_connection("Long@0123456789abcdef").port == 6402
    assert probes == [6402]
    pool.discover_all_instances(force_refresh=True)
    before = len(probes)
    pool.get_connection("Main@deadbeef")
    assert len(probes) == before


def test_expired_target_metadata_cannot_retarget_reused_port(target_environment):
    pool, write, probes, clock, _ = target_environment
    pool.resolve_instance("Main@deadbeef")
    write("Other", "cafebabe", 6400, offset=1)
    clock[0] += 5
    with pytest.raises(ConnectionError, match="Main@deadbeef.*not found"):
        pool.resolve_instance("Main@deadbeef")
    assert "Main@deadbeef" not in pool._known_instances
    assert probes == [6400, 6400, 6400]


def test_forced_target_validation_invalidates_full_inventory_cache(target_environment):
    pool, write, probes, _, available = target_environment
    pool.discover_all_instances()
    write("Other", "cafebabe", 6402, offset=1)
    available.add(6402)
    before = len(probes)
    assert pool.resolve_instance("Main@deadbeef", force_refresh=True).id == "Main@deadbeef"
    assert len(probes) == before + 1
    assert pool.discover_all_instances()[0].id == "Other@cafebabe"
    assert len(probes) == before + 3


def test_target_refresh_preserves_global_selection_if_only_longer_hash_exists(target_environment):
    pool, write, probes, _, _ = target_environment
    path = write("Main", "deadbeef", 6400)
    path.unlink()
    write("Main", "deadbeef00112233", 6400)
    # Existing pool selectors accept a composite prefix when no exact ID exists.
    assert pool.get_connection("Main@deadbeef").instance_id == "Main@deadbeef00112233"
    assert sorted(probes) == [6400, 6401]


def test_metadata_resolution_is_disabled_in_remote_hosted_mode(target_environment, monkeypatch):
    pool, _, probes, _, _ = target_environment
    monkeypatch.setattr(config, "http_remote_hosted", True)
    with pytest.raises(RuntimeError, match="disabled in remote-hosted mode"):
        pool.resolve_instance("Main@deadbeef")
    assert probes == []


def test_target_metadata_refresh_preserves_existing_reader_snapshot(target_environment):
    pool, write, _, clock, _ = target_environment
    pool.resolve_instance("Main@deadbeef")
    snapshot = pool._known_instances
    reader = iter(snapshot.values())
    write("Main", "deadbeef", 6402, offset=1)
    clock[0] += 5
    pool.resolve_instance("Main@deadbeef")
    assert next(reader).port == 6400
    assert pool._known_instances is not snapshot
    assert pool._known_instances["Main@deadbeef"].port == 6402


def test_target_only_cache_is_never_a_full_snapshot_at_epoch_zero(target_environment):
    pool, _, probes, clock, available = target_environment
    clock[0] = 0.0
    available.add(6401)
    pool.resolve_instance("Main@deadbeef")
    with pytest.raises(ConnectionError, match="Multiple Unity instances"):
        pool.resolve_instance()
    assert sorted(probes) == [6400, 6400, 6401]


def test_duplicate_target_statuses_keep_newest_viable_port(target_environment, tmp_path):
    pool, write, probes, _, available = target_environment
    # Full discovery historically normalizes every '.json' occurrence; retain
    # compatibility for duplicate status filenames representing the same ID.
    latest = write("Main", "deadbeef", 6402, offset=1)
    latest.rename(tmp_path / "unity-mcp-status-deadbeef.json.json")
    write("Main", "deadbeef", 6400)
    assert pool.resolve_instance("Main@deadbeef").port == 6402
    assert sorted(probes) == [6400, 6402]
    available.remove(6402)
    probes.clear()
    assert pool.resolve_instance("Main@deadbeef", force_refresh=True).port == 6400
    assert sorted(probes) == [6400, 6402]


def test_exact_target_real_pong_does_not_override_newer_port_identity(monkeypatch, tmp_path):
    monkeypatch.setenv("UNITY_MCP_STATUS_DIR", str(tmp_path))
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(2)
        listener.settimeout(2)
        port = listener.getsockname()[1]
        main_path = tmp_path / "unity-mcp-status-deadbeef.json"
        main_path.write_text(json.dumps({"project_path": "/Owned/Main/Assets", "unity_port": port}), encoding="utf-8")
        os.utime(main_path, (10, 10))
        received = []
        def serve():
            for _ in range(2):
                peer, _ = listener.accept()
                with peer:
                    peer.settimeout(2)
                    peer.sendall(b"MCP/0.1 FRAMING=1\n")
                    request = bytearray()
                    while len(request) < 12:
                        chunk = peer.recv(12-len(request))
                        assert chunk
                        request.extend(chunk)
                    assert request == struct.pack(">Q", 4) + b"ping"
                    received.append(bytes(request))
                    response = b'{"status":"success","result":{"message":"pong"}}'
                    peer.sendall(struct.pack(">Q", len(response)) + response)
        with ThreadPoolExecutor(max_workers=1) as executor:
            server = executor.submit(serve)
            assert PortDiscovery.discover_unity_instance("Main@deadbeef").port == port
            other_path = tmp_path / "unity-mcp-status-cafebabe.json"
            other_path.write_text(json.dumps({"project_path": "/Owned/Other/Assets", "unity_port": port}), encoding="utf-8")
            os.utime(other_path, (20, 20))
            assert PortDiscovery.discover_unity_instance("Main@deadbeef") is None
            server.result(timeout=2)
    assert len(received) == 2


@pytest.mark.asyncio
async def test_explicit_middleware_freshly_validates_target_without_connecting(target_environment, monkeypatch):
    from transport.unity_instance_middleware import UnityInstanceMiddleware, PluginHub
    pool, write, probes, _, _ = target_environment
    monkeypatch.setattr(config, "transport_mode", "stdio")
    monkeypatch.setattr(PluginHub, "is_configured", lambda: False)
    monkeypatch.setattr(uc, "get_unity_connection_pool", lambda: pool)
    monkeypatch.setattr(uc.UnityConnection, "connect", lambda *a, **kw: pytest.fail("metadata lookup must not connect"))
    middleware = UnityInstanceMiddleware()
    assert await middleware._resolve_instance_value("Main@deadbeef", None) == "Main@deadbeef"
    write("Main", "deadbeef", 6402, offset=1)
    assert await middleware._resolve_instance_value("Main@deadbeef", None) == "Main@deadbeef"
    assert pool._known_instances["Main@deadbeef"].port == 6402
    assert probes == [6400, 6402]
    assert pool._connections == {}


@pytest.mark.asyncio
async def test_middleware_missing_exact_target_keeps_exact_only_semantics(target_environment, monkeypatch):
    from transport.unity_instance_middleware import UnityInstanceMiddleware, PluginHub
    pool, write, probes, _, _ = target_environment
    write("Main", "deadbeef", 6400).unlink()
    write("Main", "deadbeef00112233", 6400)
    monkeypatch.setattr(config, "transport_mode", "stdio")
    monkeypatch.setattr(PluginHub, "is_configured", lambda: False)
    monkeypatch.setattr(uc, "get_unity_connection_pool", lambda: pool)
    with pytest.raises(ValueError, match="Instance 'Main@deadbeef' not found"):
        await UnityInstanceMiddleware()._resolve_instance_value("Main@deadbeef", None)
    assert sorted(probes) == [6400, 6401]


@pytest.mark.asyncio
@pytest.mark.parametrize("transport,remote,hub", [("http", False, False), ("stdio", True, False), ("stdio", False, True)])
async def test_middleware_target_fast_path_preserves_http_and_pluginhub_routing(target_environment, monkeypatch, transport, remote, hub):
    from transport.unity_instance_middleware import UnityInstanceMiddleware, PluginHub
    _, _, probes, _, _ = target_environment
    monkeypatch.setattr(config, "transport_mode", transport)
    monkeypatch.setattr(config, "http_remote_hosted", remote)
    monkeypatch.setattr(PluginHub, "is_configured", lambda: hub)
    middleware = UnityInstanceMiddleware()
    async def discover(ctx):
        return [SimpleNamespace(id="Main@deadbeef", hash="deadbeef")]
    monkeypatch.setattr(middleware, "_discover_instances", discover)
    monkeypatch.setattr(uc, "get_unity_connection_pool", lambda: pytest.fail("must retain existing discovery route"))
    assert await middleware._resolve_instance_value("Main@deadbeef", None) == "Main@deadbeef"
    assert probes == []
