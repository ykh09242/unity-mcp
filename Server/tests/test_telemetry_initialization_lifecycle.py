"""Public telemetry initialization with owned storage and an inert HTTP boundary."""

import importlib
import json
from pathlib import Path
import sys
import threading
from types import ModuleType, SimpleNamespace
import uuid

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
    assert received.wait(timeout=1.0)
    assert requests[0][0] == "https://owned.example/telemetry"
    assert requests[0][1]["data"]["tool_name"] == "owned_tool"
    assert telemetry.get_telemetry()._worker.is_alive()


def test_env_endpoint_overrides_canonical_config(controlled_telemetry, monkeypatch):
    telemetry, _, received, requests = controlled_telemetry
    monkeypatch.setenv("UNITY_MCP_TELEMETRY_ENDPOINT", "https://override.example/events")
    telemetry.record_resource_usage("owned_resource", True, 2.0)
    assert received.wait(timeout=1.0)
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
    assert received.wait(timeout=1.0)
    collector = telemetry.get_telemetry()
    assert str(uuid.UUID(collector._customer_uuid)) == collector._customer_uuid
    assert requests[0][1]["customer_uuid"] == collector._customer_uuid
    assert collector._milestones == milestones
    assert uuid_file.read_bytes() == b"\xff"
