"""Explicit stdio pool teardown releases discovery generation metadata."""

from concurrent.futures import ThreadPoolExecutor
from threading import Event
from unittest.mock import Mock

from models.models import UnityInstanceInfo
import transport.legacy.unity_connection as connections


def instance():
    return UnityInstanceInfo(
        id="Owned@deadbeef",
        name="Owned",
        path="/Owned/Assets",
        hash="deadbeef",
        port=6400,
        status="running",
    )


def test_explicit_pool_teardown_releases_metadata_and_forces_next_discovery(monkeypatch):
    pool = connections.UnityConnectionPool()
    observed = instance()
    snapshot = {observed.id: observed}
    pool._known_instances = snapshot
    pool._target_refreshes = {observed.id: connections.time.time()}
    pool._last_full_scan = connections.time.time()
    connection = Mock()
    pool._connections = {observed.id: connection}
    discover = Mock(return_value=[])
    monkeypatch.setattr(connections.PortDiscovery, "discover_all_unity_instances", discover)

    pool.disconnect_all()

    connection.disconnect.assert_called_once()
    assert pool._connections == {}
    assert pool._known_instances == {}
    assert pool._target_refreshes == {}
    assert pool._last_full_scan is None
    assert snapshot == {observed.id: observed}, "Existing resource readers retain their snapshot"
    assert pool.discover_all_instances() == []
    discover.assert_called_once()


def test_pool_teardown_waits_for_existing_discovery_before_clearing_metadata(monkeypatch):
    pool = connections.UnityConnectionPool()
    scanning, release_scan, closing = Event(), Event(), Event()

    def discover():
        scanning.set()
        assert release_scan.wait(2)
        return [instance()]

    def close():
        closing.set()
        pool.disconnect_all()

    monkeypatch.setattr(connections.PortDiscovery, "discover_all_unity_instances", discover)
    with ThreadPoolExecutor(max_workers=2) as executor:
        scan = executor.submit(pool.discover_all_instances)
        assert scanning.wait(2)
        teardown = executor.submit(close)
        try:
            assert closing.wait(2)
            release_scan.set()
            scan.result(timeout=2)
            teardown.result(timeout=2)
        finally:
            release_scan.set()
    assert pool._known_instances == {}
    assert pool._last_full_scan is None
