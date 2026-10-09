"""An idle telemetry worker must release the record whose send has finished."""

import gc
import queue
from pathlib import Path
import threading
from types import SimpleNamespace
import weakref

import pytest

import core.config
import core.telemetry as telemetry


class Payload(list):
    """A weak-referenceable, JSON-compatible synthetic metadata value."""


class ObservedQueue(queue.Queue):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.record_refs = []
        self.idle_retries = 0
        self.twice_idle = threading.Event()

    def get(self, *args, **kwargs):
        try:
            record = super().get(*args, **kwargs)
        except queue.Empty:
            if self.record_refs:
                self.idle_retries += 1
                if self.idle_retries >= 2:
                    self.twice_idle.set()
            raise
        self.record_refs.append(weakref.ref(record))
        return record


@pytest.fixture
def collector_factory(tmp_path, monkeypatch):
    for name in ("APPDATA", "XDG_DATA_HOME", "UNITY_MCP_LOG_DIR"):
        monkeypatch.setenv(name, str(tmp_path / name))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    for name in (
        "DISABLE_TELEMETRY",
        "UNITY_MCP_DISABLE_TELEMETRY",
        "MCP_DISABLE_TELEMETRY",
        "UNITY_MCP_TELEMETRY_ENDPOINT",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(core.config.config, "telemetry_enabled", True)
    monkeypatch.setattr(core.config.config, "telemetry_endpoint", "https://owned.example/events")
    monkeypatch.setattr(telemetry.queue, "Queue", ObservedQueue)
    state = {"posts": 0, "closed": 0, "worker_errors": 0}
    collectors = []

    class Client:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            state["closed"] += 1

        def post(self, endpoint, json):
            assert endpoint == "https://owned.example/events"
            assert json["data"]["operation"] == "fixture"
            state["posts"] += 1
            if state.get("fail_send"):
                raise RuntimeError("offline sender failure")
            return SimpleNamespace(status_code=200)

    monkeypatch.setattr(telemetry, "httpx", SimpleNamespace(Client=Client))

    # Do not let pytest's LogRecord observer retain the synthetic worker-exception traceback.
    def debug(message, *args, **kwargs):
        if message == "Telemetry worker send failed":
            state["worker_errors"] += 1

    monkeypatch.setattr(telemetry.logger, "debug", debug)

    def create():
        collector = telemetry.TelemetryCollector()
        assert collector.config.enabled
        collectors.append(collector)
        return collector, state

    yield create
    for collector in collectors:
        collector.shutdown()
        assert not collector._worker.is_alive()


def enqueue_payload(collector):
    payload = Payload(["x" * 64] * 16384)
    reference = weakref.ref(payload)
    collector.record(telemetry.RecordType.LATENCY, {"operation": "fixture", "values": payload})
    return reference


def assert_finished_and_released(collector, state, references, sends):
    assert collector._queue.twice_idle.wait(5), "Worker did not retry its idle queue twice"
    gc.collect()
    assert state["posts"] == state["closed"] == sends
    assert collector._queue.empty()
    assert collector._queue.unfinished_tasks == 0
    assert collector._worker.is_alive()
    assert all(reference() is None for reference in references), (
        "Idle worker kept completed payload"
    )
    assert all(reference() is None for reference in collector._queue.record_refs)


@pytest.mark.parametrize("fail_send", [False, True], ids=["success", "sender-failure"])
def test_completed_send_releases_record_before_idle_queue_retries(collector_factory, fail_send):
    collector, state = collector_factory()
    state["fail_send"] = fail_send
    reference = enqueue_payload(collector)
    assert_finished_and_released(collector, state, [reference], sends=1)


def test_worker_exception_releases_record_after_client_closes(collector_factory, monkeypatch):
    collector, state = collector_factory()
    send = collector._send_telemetry

    def unexpected_failure(record):
        send(record)
        raise RuntimeError("unexpected worker-side fixture failure")

    monkeypatch.setattr(collector, "_send_telemetry", unexpected_failure)
    reference = enqueue_payload(collector)
    assert_finished_and_released(collector, state, [reference], sends=1)
    assert state["worker_errors"] == 1


def test_worker_processes_next_record_and_keeps_queue_accounting(collector_factory):
    collector, state = collector_factory()
    references = [enqueue_payload(collector), enqueue_payload(collector)]
    assert_finished_and_released(collector, state, references, sends=2)
