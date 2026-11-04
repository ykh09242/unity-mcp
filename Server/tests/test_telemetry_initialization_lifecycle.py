"""Public telemetry initialization with owned storage and an inert HTTP boundary."""

from tests.timing import watchdog_timeout

import importlib
import json
from pathlib import Path
import sys
import threading
from types import ModuleType, SimpleNamespace
import uuid
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest


@pytest.fixture
def controlled_telemetry(tmp_path, monkeypatch):
    for key in ("HOME", "USERPROFILE", "APPDATA", "XDG_DATA_HOME"):
        monkeypatch.setenv(key, str(tmp_path))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    for key in (
        "DISABLE_TELEMETRY",
        "UNITY_MCP_DISABLE_TELEMETRY",
        "MCP_DISABLE_TELEMETRY",
        "UNITY_MCP_TELEMETRY_ENDPOINT",
    ):
        monkeypatch.delenv(key, raising=False)
    telemetry = importlib.import_module("core.telemetry")
    telemetry.reset_telemetry()
    config = importlib.import_module("core.config").config
    monkeypatch.setattr(config, "telemetry_enabled", True)
    monkeypatch.setattr(config, "telemetry_endpoint", "https://owned.example/telemetry")
    received = threading.Event()
    requests = []

    def respond(request):
        requests.append((str(request.url), json.loads(request.content)))
        received.set()
        return httpx.Response(200, json={})

    client_type = httpx.Client
    monkeypatch.setattr(
        telemetry.httpx,
        "Client",
        lambda **kwargs: client_type(transport=httpx.MockTransport(respond), **kwargs),
    )
    try:
        yield telemetry, config, received, requests
    finally:
        telemetry.reset_telemetry()


def test_canonical_config_opt_out_precedes_legacy_alias(
    controlled_telemetry, monkeypatch, tmp_path
):
    telemetry, config, _, requests = controlled_telemetry
    config.telemetry_enabled = False
    legacy = ModuleType("src.core.config")
    legacy.config = SimpleNamespace(
        telemetry_enabled=True, telemetry_endpoint="https://legacy.example/events"
    )
    monkeypatch.setitem(sys.modules, "src.core.config", legacy)
    telemetry.record_tool_usage("owned_tool", True, 1.0)
    assert telemetry.is_telemetry_enabled() is False
    assert telemetry.get_telemetry()._worker is None
    assert requests == []
    assert not (tmp_path / "UnityMCP").exists()


def test_canonical_enabled_config_controls_actual_send(controlled_telemetry):
    telemetry, _, received, requests = controlled_telemetry
    telemetry.record_tool_usage("owned_tool", True, 1.0)
    assert received.wait(timeout=watchdog_timeout())
    assert requests[0][0] == "https://owned.example/telemetry"
    assert requests[0][1]["data"]["tool_name"] == "owned_tool"
    assert telemetry.get_telemetry()._worker.is_alive()


def test_env_endpoint_overrides_canonical_config(controlled_telemetry, monkeypatch):
    telemetry, _, received, requests = controlled_telemetry
    monkeypatch.setenv("UNITY_MCP_TELEMETRY_ENDPOINT", "https://override.example/events")
    telemetry.record_resource_usage("owned_resource", True, 2.0)
    assert received.wait(timeout=watchdog_timeout())
    assert requests[0][0] == "https://override.example/events"


def test_legacy_config_fallback_when_canonical_module_unavailable(
    controlled_telemetry, monkeypatch
):
    telemetry, _, _, _ = controlled_telemetry
    legacy = ModuleType("src.core.config")
    legacy.config = SimpleNamespace(
        telemetry_enabled=False, telemetry_endpoint="https://legacy.example/events"
    )
    monkeypatch.setitem(sys.modules, "src.core.config", legacy)
    import_module = telemetry.import_module

    def without_canonical(name):
        if name == "core.config":
            raise ModuleNotFoundError(name)
        return import_module(name)

    monkeypatch.setattr(telemetry, "import_module", without_canonical)
    config = telemetry.TelemetryConfig()
    assert not config.enabled
    assert config.endpoint == "https://legacy.example/events"


@pytest.mark.parametrize(
    "disable_var", ["DISABLE_TELEMETRY", "UNITY_MCP_DISABLE_TELEMETRY", "MCP_DISABLE_TELEMETRY"]
)
def test_env_opt_out_precedes_enabled_config(
    controlled_telemetry, monkeypatch, tmp_path, disable_var
):
    telemetry, _, _, requests = controlled_telemetry
    monkeypatch.setenv(disable_var, "true")
    telemetry.record_tool_usage("owned_tool", True, 1.0)
    collector = telemetry.get_telemetry()
    assert not collector.config.enabled
    assert collector._worker is None
    assert requests == []
    assert not (tmp_path / "UnityMCP").exists()


@pytest.mark.parametrize("storage", ["empty", "blocked", "invalid_utf8"])
def test_disabled_initialization_does_not_touch_storage(
    controlled_telemetry, monkeypatch, tmp_path, storage
):
    telemetry, _, _, requests = controlled_telemetry
    data_dir = tmp_path / "UnityMCP"
    if storage == "blocked":
        data_dir.write_bytes(b"owned blocker")
    elif storage == "invalid_utf8":
        data_dir.mkdir()
        (data_dir / "customer_uuid.txt").write_bytes(b"\xff")
        (data_dir / "milestones.json").write_bytes(b"\xff")
    monkeypatch.setenv("UNITY_MCP_DISABLE_TELEMETRY", "true")
    # Even a read attempt is a regression for disabled collection.
    monkeypatch.setattr(
        Path,
        "read_text",
        lambda *args, **kwargs: pytest.fail("Disabled telemetry read persistent storage"),
    )
    monkeypatch.setattr(
        Path,
        "write_text",
        lambda *args, **kwargs: pytest.fail("Disabled telemetry wrote persistent storage"),
    )
    telemetry.record_tool_usage("owned_tool", True, 1.0)
    collector = telemetry.get_telemetry()
    assert collector.config.data_dir == data_dir
    assert collector._worker is None
    assert collector._queue.empty()
    assert telemetry.record_milestone(telemetry.MilestoneType.FIRST_STARTUP) is False
    collector.shutdown()
    collector.shutdown()
    assert requests == []
    if storage == "empty":
        assert not data_dir.exists()
    elif storage == "blocked":
        assert data_dir.read_bytes() == b"owned blocker"
    else:
        assert (data_dir / "customer_uuid.txt").read_bytes() == b"\xff"
        assert (data_dir / "milestones.json").read_bytes() == b"\xff"


def test_invalid_utf8_uuid_uses_in_memory_fallback(controlled_telemetry, tmp_path):
    telemetry, _, received, requests = controlled_telemetry
    data_dir = tmp_path / "UnityMCP"
    data_dir.mkdir()
    uuid_file = data_dir / "customer_uuid.txt"
    uuid_file.write_bytes(b"\xff")
    milestones = {"first_startup": {"timestamp": 1.0, "data": {}}}
    (data_dir / "milestones.json").write_text(json.dumps(milestones), encoding="utf-8")
    telemetry.record_tool_usage("owned_tool", True, 1.0)
    assert received.wait(timeout=watchdog_timeout())
    collector = telemetry.get_telemetry()
    assert str(uuid.UUID(collector._customer_uuid)) == collector._customer_uuid
    assert requests[0][1]["customer_uuid"] == collector._customer_uuid
    assert collector._milestones == milestones
    assert uuid_file.read_bytes() == b"\xff"


def test_concurrent_first_use_owns_one_actual_worker(controlled_telemetry, monkeypatch):
    telemetry, _, received, requests = controlled_telemetry
    constructing = threading.Event()
    contender_arrived = threading.Event()
    release = threading.Event()
    collectors = []
    state_lock = telemetry._telemetry_lock

    class ObservedLock:
        def __enter__(self):
            acquired = state_lock.acquire(blocking=False)
            if threading.current_thread().name.startswith("telemetry-contender"):
                contender_arrived.set()
            if not acquired:
                state_lock.acquire()

        def __exit__(self, *_args):
            state_lock.release()

    def load(collector):
        collectors.append(collector)
        constructing.set()
        assert release.wait(watchdog_timeout())

    monkeypatch.setattr(telemetry.TelemetryCollector, "_load_persistent_data", load)
    monkeypatch.setattr(telemetry, "_telemetry_lock", ObservedLock())

    try:
        with (
            ThreadPoolExecutor(max_workers=1) as executor,
            ThreadPoolExecutor(
                max_workers=1, thread_name_prefix="telemetry-contender"
            ) as contender,
        ):
            first = executor.submit(telemetry.get_telemetry)
            assert constructing.wait(watchdog_timeout())
            second = contender.submit(telemetry.get_telemetry)
            assert contender_arrived.wait(watchdog_timeout())
            release.set()
            results = [future.result(timeout=watchdog_timeout()) for future in (first, second)]
        assert len(collectors) == 1
        assert results[0] is results[1]
        telemetry.record_tool_usage("owned_tool", True, 1.0)
        assert received.wait(watchdog_timeout())
        assert len(requests) == 1
        telemetry.reset_telemetry()
        assert all(not collector._worker.is_alive() for collector in collectors)
    finally:
        release.set()
        # Also retire orphan workers when running this regression against the old code.
        for collector in collectors:
            collector.shutdown()


def test_initialization_exception_leaves_singleton_retryable(controlled_telemetry, monkeypatch):
    telemetry, _, _, _ = controlled_telemetry
    load = telemetry.TelemetryCollector._load_persistent_data

    def fail(_collector):
        raise RuntimeError("owned initialization failure")

    monkeypatch.setattr(telemetry.TelemetryCollector, "_load_persistent_data", fail)
    with pytest.raises(RuntimeError, match="owned initialization failure"):
        telemetry.get_telemetry()
    monkeypatch.setattr(telemetry.TelemetryCollector, "_load_persistent_data", load)
    collector = telemetry.get_telemetry()
    assert collector._worker.is_alive()
    assert telemetry.get_telemetry() is collector


def test_shutdown_without_first_use_never_constructs(controlled_telemetry, monkeypatch):
    telemetry, _, _, requests = controlled_telemetry

    def unexpected_config():
        pytest.fail("Shutdown or late callbacks initialized telemetry")

    monkeypatch.setattr(telemetry, "TelemetryConfig", unexpected_config)
    owner = telemetry.start_telemetry()
    telemetry.shutdown_telemetry(owner)
    telemetry.shutdown_telemetry()
    telemetry.record_tool_usage("late_tool", True, 1.0)
    assert telemetry.record_milestone(telemetry.MilestoneType.FIRST_STARTUP) is False
    assert telemetry.is_telemetry_enabled() is False
    assert requests == []


def test_shutdown_fences_before_callback_and_does_not_hold_singleton_lock(
    controlled_telemetry, monkeypatch
):
    telemetry, _, received, requests = controlled_telemetry
    telemetry.record_tool_usage("owned_tool", True, 1.0)
    assert received.wait(watchdog_timeout())
    collector = telemetry.get_telemetry()
    shutdown = collector.shutdown
    observed = []

    def close():
        # A different thread must be able to enter the getter during close.
        with ThreadPoolExecutor(max_workers=1) as executor:
            observed.append(executor.submit(telemetry.get_telemetry).result(watchdog_timeout()))
        shutdown()

    monkeypatch.setattr(collector, "shutdown", close)
    telemetry.shutdown_telemetry()
    assert observed == [None]
    assert not collector._worker.is_alive()
    collector.record(telemetry.RecordType.STARTUP, {})
    assert collector.record_milestone(telemetry.MilestoneType.FIRST_STARTUP) is False
    assert collector._queue.empty()
    assert len(requests) == 1


def test_retiring_worker_prevents_reset_from_starting_replacement(
    controlled_telemetry, monkeypatch
):
    telemetry, _, _, _ = controlled_telemetry
    sending = threading.Event()
    release = threading.Event()

    def send(_collector, _record):
        sending.set()
        assert release.wait(watchdog_timeout())

    monkeypatch.setattr(telemetry.TelemetryCollector, "_send_telemetry", send)
    telemetry.record_tool_usage("owned_tool", True, 1.0)
    collector = telemetry.get_telemetry()
    assert sending.wait(watchdog_timeout())
    # Keep this test deterministic without waiting for the real bounded join timeout.
    monkeypatch.setattr(collector._worker, "join", lambda **_kwargs: None)
    try:
        telemetry.reset_telemetry()
        assert collector._worker.is_alive()
        assert telemetry.get_telemetry() is None
        telemetry.shutdown_telemetry()
        assert telemetry.get_telemetry() is None
    finally:
        release.set()
        threading.Thread.join(collector._worker, timeout=watchdog_timeout())
    telemetry.reset_telemetry()
    replacement = telemetry.get_telemetry()
    assert replacement is not collector
    assert replacement._worker.is_alive()


def test_previous_lifespan_cannot_emit_or_close_current_owner(controlled_telemetry):
    telemetry, _, received, requests = controlled_telemetry
    old_owner = telemetry.start_telemetry()
    current_owner = telemetry.start_telemetry()
    telemetry.record_telemetry(telemetry.RecordType.STARTUP, {}, owner=old_owner)
    assert (
        telemetry.record_milestone(telemetry.MilestoneType.FIRST_STARTUP, owner=old_owner) is False
    )
    telemetry.shutdown_telemetry(old_owner)
    telemetry.record_telemetry(telemetry.RecordType.STARTUP, {}, owner=current_owner)
    assert received.wait(watchdog_timeout())
    assert len(requests) == 1
    collector = telemetry.get_telemetry()
    telemetry.shutdown_telemetry(current_owner)
    assert not collector._worker.is_alive()
    assert telemetry.get_telemetry() is None


def test_reset_completion_cannot_reopen_concurrent_shutdown(controlled_telemetry, monkeypatch):
    telemetry, _, _, _ = controlled_telemetry
    collector = telemetry.get_telemetry()
    closing = threading.Event()
    release = threading.Event()
    shutdown = collector.shutdown

    def close():
        closing.set()
        assert release.wait(watchdog_timeout())
        shutdown()

    monkeypatch.setattr(collector, "shutdown", close)
    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            reset = executor.submit(telemetry.reset_telemetry)
            assert closing.wait(watchdog_timeout())
            terminal_close = telemetry.begin_telemetry_shutdown()
            release.set()
            reset.result(timeout=watchdog_timeout())
            terminal_close()
        assert telemetry.get_telemetry() is None
        assert not collector._worker.is_alive()
    finally:
        release.set()


def test_shutdown_retires_in_progress_publication(controlled_telemetry, monkeypatch):
    telemetry, _, _, _ = controlled_telemetry
    constructing = threading.Event()
    shutdown_started = threading.Event()
    release = threading.Event()
    collectors = []

    def load(collector):
        collectors.append(collector)
        constructing.set()
        assert release.wait(watchdog_timeout())

    def close():
        shutdown_started.set()
        telemetry.shutdown_telemetry()

    monkeypatch.setattr(telemetry.TelemetryCollector, "_load_persistent_data", load)
    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            first_use = executor.submit(telemetry.get_telemetry)
            assert constructing.wait(watchdog_timeout())
            shutdown = executor.submit(close)
            assert shutdown_started.wait(watchdog_timeout())
            release.set()
            result = first_use.result(timeout=watchdog_timeout())
            shutdown.result(timeout=watchdog_timeout())
        assert result is None
        assert len(collectors) == 1
        assert not collectors[0]._worker.is_alive()
        assert telemetry.get_telemetry() is None
    finally:
        release.set()
