import importlib
from pathlib import Path

import pytest


@pytest.fixture
def collector_factory(tmp_path, monkeypatch):
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
    config = importlib.import_module("core.config").config
    monkeypatch.setattr(config, "telemetry_enabled", True)
    monkeypatch.setattr(config, "telemetry_endpoint", "https://example.com/telemetry")
    # Intercept the sender before any worker can start.
    monkeypatch.setattr(telemetry.TelemetryCollector, "_send_telemetry", lambda self, record: None)
    collectors = []

    def create():
        collector = telemetry.TelemetryCollector()
        collectors.append(collector)
        return collector

    try:
        yield create
    finally:
        for collector in collectors:
            collector.shutdown()


def test_endpoint_rejects_non_http(collector_factory, monkeypatch):
    monkeypatch.setenv("UNITY_MCP_TELEMETRY_ENDPOINT", "file:///etc/passwd")
    collector = collector_factory()
    assert collector.config.endpoint == collector.config.default_endpoint


def test_config_preferred_then_env_override(collector_factory, monkeypatch):
    collector = collector_factory()
    assert collector.config.endpoint == "https://example.com/telemetry"
    monkeypatch.setenv("UNITY_MCP_TELEMETRY_ENDPOINT", "https://override.example/ep")
    overridden = collector_factory()
    assert overridden.config.endpoint == "https://override.example/ep"


def test_uuid_preserved_on_malformed_milestones(collector_factory):
    first = collector_factory()
    first_uuid = first._customer_uuid
    first.config.milestones_file.write_text("{not-json}", encoding="utf-8")
    first.shutdown()
    second = collector_factory()
    assert second._customer_uuid == first_uuid
