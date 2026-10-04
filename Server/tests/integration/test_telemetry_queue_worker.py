import logging
from pathlib import Path
import threading
import time

import core.config
import core.telemetry as telemetry


def test_telemetry_queue_backpressure_and_single_worker(tmp_path, monkeypatch, caplog):
    for key in ("HOME", "USERPROFILE", "APPDATA", "XDG_DATA_HOME"):
        monkeypatch.setenv(key, str(tmp_path))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    for key in ("DISABLE_TELEMETRY", "UNITY_MCP_DISABLE_TELEMETRY", "MCP_DISABLE_TELEMETRY"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(core.config.config, "telemetry_enabled", True)
    monkeypatch.setattr(core.config.config, "telemetry_endpoint", "https://owned.example/events")
    entered = threading.Event()
    release = threading.Event()

    def controlled_send(self, record):
        entered.set()
        release.wait()

    # Configure the enabled collector and intercept sends before construction.
    monkeypatch.setattr(telemetry.TelemetryCollector, "_send_telemetry", controlled_send)
    tel_logger = logging.getLogger("unity-mcp-telemetry")
    tel_logger.addHandler(caplog.handler)
    collector = None
    try:
        caplog.set_level("DEBUG", logger="unity-mcp-telemetry")
        collector = telemetry.TelemetryCollector()
        collector.record(telemetry.RecordType.TOOL_EXECUTION, {"i": -1})
        assert entered.wait(timeout=1.0), "Worker did not enter the controlled sender"
        # Fill the real bounded queue while its sole consumer is event-gated.
        start = time.perf_counter()
        for i in range(collector._queue.maxsize + 50):
            collector.record(telemetry.RecordType.TOOL_EXECUTION, {"i": i})
        elapsed_ms = (time.perf_counter() - start) * 1000.0
        assert elapsed_ms < 500.0, f"Took {elapsed_ms:.1f}ms for nonblocking records"
        assert collector._queue.full()
        assert any("Telemetry queue full; dropping" in message for message in caplog.messages)
        assert collector._worker.is_alive()
        assert sum(thread is collector._worker for thread in threading.enumerate()) == 1
    finally:
        release.set()
        if collector is not None:
            collector.shutdown()
            assert not collector._worker.is_alive()
        if caplog.handler in tel_logger.handlers:
            tel_logger.removeHandler(caplog.handler)
