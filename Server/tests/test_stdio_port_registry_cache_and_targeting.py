"""Inert discovery coverage for negative registry caching and target preservation."""
from datetime import datetime, timedelta
import importlib
from pathlib import Path

import pytest


@pytest.fixture
def registry_environment(monkeypatch, tmp_path):
    for name in ("APPDATA", "XDG_DATA_HOME", "UNITY_MCP_LOG_DIR", "UNITY_MCP_STATUS_DIR"):
        monkeypatch.setenv(name, str(tmp_path / name))
    monkeypatch.setenv("UNITY_MCP_DISABLE_TELEMETRY", "true")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    import socket
    def deny_network(*args, **kwargs):
        raise AssertionError("Registry tests must not perform live network discovery")
    monkeypatch.setattr(socket, "create_connection", deny_network)
    module = importlib.import_module("transport.legacy.stdio_port_registry")
    clock = [0.0]
    discovered = []
    scans = []
    fallback = []
    monkeypatch.setattr(module.time, "time", lambda: clock[0])
    monkeypatch.setattr(module.config, "port_registry_ttl", 5.0)
    def discover():
        scans.append(clock[0])
        return list(discovered)
    monkeypatch.setattr(module.PortDiscovery, "discover_all_unity_instances", discover)
    monkeypatch.setattr(module.PortDiscovery, "discover_unity_port", lambda: fallback.append(6501) or 6501)
    return module, clock, discovered, scans, fallback


def _instance(name, port, heartbeat=None):
    from models.models import UnityInstanceInfo
    return UnityInstanceInfo(id=f"{name}@{name.lower()}hash", name=name, path=f"owned/{name}",
                             hash=f"{name.lower()}hash", port=port, status="running", last_heartbeat=heartbeat)


def test_empty_results_are_cached_from_clock_zero_until_exact_ttl(registry_environment):
    module, clock, _, scans, _ = registry_environment
    registry = module.StdioPortRegistry()
    for _ in range(4):
        assert registry.get_instances() == []
    assert scans == [0.0]
    clock[0] = 4.999
    assert registry.get_instances() == []
    assert scans == [0.0]
    clock[0] = 5.0
    assert registry.get_instances() == []
    assert scans == [0.0, 5.0]


def test_force_refresh_and_clear_bypass_empty_cache(registry_environment):
    module, _, _, scans, _ = registry_environment
    registry = module.StdioPortRegistry()
    registry.get_instances()
    registry.get_instances(force_refresh=True)
    registry.get_instances()
    assert len(scans) == 2
    registry.clear()
    registry.get_instances()
    registry.get_instances()
    assert len(scans) == 3


def test_forced_refresh_can_discover_new_instance_and_cache_its_removal(registry_environment):
    module, _, discovered, scans, _ = registry_environment
    registry = module.StdioPortRegistry()
    assert registry.get_instances() == []
    selected = _instance("Selected", 6401)
    discovered.append(selected)
    assert registry.get_instances(force_refresh=True) == [selected]
    discovered.clear()
    assert registry.get_instances(force_refresh=True) == []
    assert registry.get_instances() == []
    assert len(scans) == 3


def test_failed_discovery_does_not_initialize_a_negative_cache(registry_environment, monkeypatch):
    module, _, _, _, _ = registry_environment
    registry = module.StdioPortRegistry()
    attempts = []
    def discover():
        attempts.append(1)
        if len(attempts) == 1:
            raise OSError("owned discovery failure")
        return []
    monkeypatch.setattr(module.PortDiscovery, "discover_all_unity_instances", discover)
    with pytest.raises(OSError, match="owned discovery failure"):
        registry.get_instances()
    assert registry.get_instances() == []
    assert registry.get_instances() == []
    assert len(attempts) == 2


@pytest.mark.parametrize("has_other", [False, True])
def test_missing_explicit_target_never_uses_untargeted_fallback(registry_environment, has_other):
    module, _, discovered, _, fallback = registry_environment
    if has_other:
        discovered.append(_instance("Other", 6501))
    registry = module.StdioPortRegistry()
    with pytest.raises(ConnectionError, match="Selected@selectedhash"):
        registry.get_port("Selected@selectedhash")
    assert fallback == []


@pytest.mark.parametrize("selector", [None, ""])
def test_no_target_preserves_legacy_fallback(registry_environment, selector):
    module, _, _, _, fallback = registry_environment
    assert module.StdioPortRegistry().get_port(selector) == 6501
    assert fallback == [6501]


def test_selected_target_and_untargeted_newest_instance_keep_existing_rules(registry_environment):
    module, _, discovered, _, fallback = registry_environment
    now = datetime(2020, 1, 1)
    selected = _instance("Selected", 6401, now)
    other = _instance("Other", 6501, now + timedelta(seconds=1))
    discovered.extend([selected, other])
    registry = module.StdioPortRegistry()
    assert registry.get_port(selected.id) == 6401
    assert registry.get_port() == 6501
    assert fallback == []
    returned = registry.get_instances()
    returned.clear()
    assert len(registry.get_instances()) == 2


def test_connection_constructor_preserves_missing_target_failure(registry_environment, monkeypatch):
    module, _, discovered, _, fallback = registry_environment
    discovered.append(_instance("Other", 6501))
    connection = importlib.import_module("transport.legacy.unity_connection")
    monkeypatch.setattr(connection, "stdio_port_registry", module.StdioPortRegistry())
    with pytest.raises(ConnectionError, match="Selected@selectedhash"):
        connection.UnityConnection(instance_id="Selected@selectedhash")
    assert fallback == []


def test_send_rediscovery_does_not_retarget_missing_selection(registry_environment, monkeypatch):
    module, _, discovered, _, fallback = registry_environment
    selected = _instance("Selected", 6401)
    other = _instance("Other", 6501)
    discovered.append(selected)
    connection = importlib.import_module("transport.legacy.unity_connection")
    monkeypatch.setattr(connection, "stdio_port_registry", module.StdioPortRegistry())
    conn = connection.UnityConnection(instance_id=selected.id)
    attempted = []
    def fail_connect(connect_timeout=None, deadline=None):
        attempted.append(conn.port)
        discovered[:] = [other]
        return False
    monkeypatch.setattr(conn, "connect", fail_connect)
    monkeypatch.setattr(connection.time, "sleep", lambda seconds: None)
    with pytest.raises(ConnectionError, match="Could not connect to Unity"):
        conn.send_command("get_editor_state", {}, max_attempts=1)
    assert attempted == [6401, 6401]
    assert conn.port == 6401
    assert fallback == []
