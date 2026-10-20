"""Benchmark reports must not hide drift, missing stages, or lifecycle leaks."""

from __future__ import annotations

import json
import socket
import textwrap
import time
from contextlib import asynccontextmanager
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

import pytest
import anyio

TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS.parent))
from tools.bench_transport_report import (
    Observation,
    attach_stages,
    distribution,
    equivalent_outputs,
)
from tools.bench_transport_process import cleanup_process, native_python
from tools.bench_transport_compare import Capture, check_contract


@dataclass
class SlowChild:
    """Mutable process fake models a child that ignores terminate until kill."""

    returncode: int | None = None
    events: list[str] = field(default_factory=list)

    def terminate(self) -> None:
        self.events.append("terminate")

    def kill(self) -> None:
        self.events.append("kill")
        self.returncode = -9

    async def wait(self) -> int:
        self.events.append("wait")
        if self.returncode is None:
            await anyio.sleep_forever()
        return self.returncode

    async def aclose(self) -> None:
        self.events.append("close")


@pytest.mark.asyncio
async def test_cleanup_escalates_when_caller_is_already_cancelled() -> None:
    # Given: a slow owned child and a cancellation already active on its caller.
    child = SlowChild()
    # When: cleanup runs inside that cancelled scope.
    with anyio.CancelScope() as scope:
        scope.cancel()
        await cleanup_process(child, grace_seconds=0.001)
    # Then: graceful timeout still reaches kill, reap and close under shielding.
    assert child.events == ["terminate", "wait", "kill", "wait", "close"]


@pytest.mark.asyncio
async def test_cleanup_closes_when_owned_real_child_already_exited() -> None:
    # Given: a real subprocess already reaped through normal completion.
    process = await anyio.open_process([sys.executable, "-c", "pass"])
    await process.wait()
    # When: the same cancellation-safe cleanup takes its exited-child path.
    await cleanup_process(process)
    # Then: owned stdout/stderr handles are closed as well as the child reaped.
    assert process.returncode == 0
    with pytest.raises(anyio.ClosedResourceError):
        await process.stdout.receive()


@pytest.mark.asyncio
async def test_cleanup_reaps_when_owned_native_child_is_running_and_caller_cancelled() -> None:
    # Given: the native interpreter itself, without the Windows venv redirector.
    process = await anyio.open_process(
        [
            sys._base_executable,
            "-I",
            "-c",
            "import os,time;print(os.getpid(),flush=True);time.sleep(60)",
        ]
    )
    try:
        with anyio.fail_after(5):
            reported_pid = int((await process.stdout.receive()).strip())
        assert reported_pid == process.pid
        # When: cleanup receives cancellation with that actual server child still alive.
        with anyio.CancelScope() as scope:
            scope.cancel()
            await cleanup_process(process)
    finally:
        await cleanup_process(process)
    # Then: the held PID is reaped and its owned pipe handle is closed.
    assert process.returncode is not None
    with pytest.raises(anyio.ClosedResourceError):
        await process.stdout.receive()


def test_percentiles_when_tail_contains_an_outlier() -> None:
    # Given: ninety-nine fast samples and a single slow sample.
    samples = [1.0] * 99 + [500.0]
    # When: compute nearest-rank percentiles.
    result = distribution(samples)
    # Then: p99 excludes only the slowest 1%, while max retains its evidence.
    assert result == {"count": 100, "min": 1.0, "max": 500.0, "p50": 1.0, "p95": 1.0, "p99": 1.0}


def test_equivalence_rejects_drift_when_later_samples_match() -> None:
    # Given: an earlier mismatch that would be hidden by a last-value map.
    original = Observation(
        correlation="small:0",
        workload="small",
        phase="warm",
        client_total_ms=10,
        output_sha256="a",
        output_bytes=1,
    )
    drift = original.model_copy(update={"correlation": "small:1", "output_sha256": "b"})
    # When: compare the full series, whose final values are equal.
    equivalent = equivalent_outputs([[original, drift], [drift, drift]])
    # Then: the earlier mismatch fails output equivalence.
    assert equivalent is False


def test_stages_include_readiness_when_peer_uses_extra_probe(tmp_path: Path) -> None:
    # Given: one public call, its prerequisite ping, and a legacy admission queue.
    events = [
        {
            "correlation": "small:0",
            "queue_ms": 2,
            "synthetic_unity_work_ms": 3,
            "peer_serialization_ms": 1,
            "response_bytes": 80,
        },
        {
            "correlation": "small:0:readiness",
            "queue_ms": 1,
            "synthetic_unity_work_ms": 4,
            "peer_serialization_ms": 2,
            "response_bytes": 40,
        },
    ]
    (tmp_path / "peer.jsonl").write_text(
        "\n".join(json.dumps(row) for row in events), encoding="utf-8"
    )
    (tmp_path / "queue.jsonl").write_text(
        json.dumps({"correlation": "small:0", "queue_ms": 1}), encoding="utf-8"
    )
    observation = Observation(
        correlation="small:0",
        workload="small",
        phase="warm",
        client_total_ms=20,
        output_sha256="a",
        output_bytes=1,
    )
    # When: join all stages of the call.
    result = attach_stages([observation], tmp_path)[0]
    # Then: stage accounting includes the extra work and sums to client latency.
    assert (
        result.queue_ms,
        result.synthetic_unity_work_ms,
        result.peer_serialization_ms,
        result.wire_response_framework_ms,
        result.peer_response_bytes,
    ) == (4, 7, 3, 6, 120)


def test_missing_stage_rejected_when_peer_has_no_correlated_observation(tmp_path: Path) -> None:
    # Given: a client observation whose peer evidence is absent.
    (tmp_path / "peer.jsonl").write_text("", encoding="utf-8")
    observation = Observation(
        correlation="small:0",
        workload="small",
        phase="warm",
        client_total_ms=20,
        output_sha256="a",
        output_bytes=1,
    )
    # When/Then: a missing stage cannot become fabricated zero-cost work.
    with pytest.raises(ValueError, match="Missing peer observation"):
        attach_stages([observation], tmp_path)


def test_actual_transports_when_owned_peers_complete_cancel_and_reconnect(tmp_path: Path) -> None:
    # Given: the installed SDK/server runtime and isolated owned fixture endpoints.
    output = tmp_path / "measurement.json"
    command = native_python(
        [
            str(TOOLS / "bench_transport.py"),
            "--output",
            str(output),
            "--samples",
            "2",
            "--warmup",
            "0",
            "--large-bytes",
            "4194304",
            "--concurrency",
            "2",
            "--cohort-gate",
            "--work-ms",
            "0",
            "--resource-contract",
        ],
        (TOOLS.parent,),
    )
    # When: execute the actual CLI over both product routing paths.
    completed = subprocess.run(
        command, cwd=TOOLS.parent, capture_output=True, text=True, timeout=40, check=False
    )
    # Then: matched output, observed peer work, and drained state survive replacement.
    assert completed.returncode == 0, completed.stderr
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["output_equivalent"] is True
    assert report["source"]["unchanged_during_measurement"] is True
    check_contract(Capture.model_validate(report))
    for result in report["results"]:
        assert set(result["warmed"]) == {"small", "state", "large", "job"}
        assert len(result["observations"]) == 14
        assert result["lifecycle"]["cancel_requested"] is True
        state = result["lifecycle"]["after_reconnect"]
        assert state["registrations"] == 2
        if result["mode"] == "http":
            assert (state["pending"], state["retained"]) == (0, 0)
            partial = result["lifecycle"]["partial_cancel"]
            assert partial["receiver_final_chunk_processed"] is True
            assert partial["held"]["assembler_buffer_bytes"] > 4194304
            assert partial["held"]["hub_reserved_bytes"] <= partial["held"]["capacity_bytes"]
        else:
            assert (state["pending"], state["retained"]) == (None, None)
            assert state["accounting"]["stdio_delivery_observer_available"] is True
        assert state["commands"]["read_console"] == 9
        assert state["commands"]["get_editor_state"] == state["commands"]["get_test_job"] == 3
        assert result["warmed"]["large"]["output_bytes"] > 4194304


@asynccontextmanager
async def readiness_fixture(tmp_path: Path, monkeypatch, fault: str = ""):
    """Launch the actual server, controlling only readiness publication boundaries."""
    from tools import bench_transport as bench

    directory = tmp_path / "http"
    held = fault in {"", "deadline", "cancel"}
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    listener.settimeout(10)
    control = None
    process = None
    observed = {"held": None, "released": False, "cleanup_called": False}
    entered = anyio.Event()
    original_process = anyio.open_process
    original_sleep = anyio.sleep
    original_cleanup = bench.cleanup_process
    bootstrap = textwrap.dedent(
        f"""
        import json, os, socket, sys
        from pathlib import Path
        sys.dont_write_bytecode = True
        directory = Path({str(directory)!r})
        fault = {fault!r}
        held = {held!r}
        original_open = Path.open
        original_write = Path.write_text
        original_replace = Path.replace

        def readiness_path(path):
            return path.parent == directory and path.name in ('ready.json', 'ready.json.pending')

        def controlled_open(path, *args, **kwargs):
            stream = original_open(path, *args, **kwargs)
            mode = args[0] if args else kwargs.get('mode', 'r')
            if held and readiness_path(path) and mode == 'w':
                with socket.create_connection(('127.0.0.1', {listener.getsockname()[1]}), timeout=20) as control:
                    control.sendall((json.dumps({{'path': str(path), 'pid': os.getpid(),
                        'final_exists': (directory / 'ready.json').exists()}}) + '\\n').encode())
                    if control.recv(1) != b'x':
                        raise RuntimeError('Readiness test control closed before release')
            return stream

        def controlled_write(path, data, *args, **kwargs):
            if readiness_path(path):
                if fault == 'exit':
                    os._exit(17)
                if fault == 'write':
                    raise OSError('Owned readiness write failure')
                if fault == 'malformed':
                    data = '{{not-json'
                if fault == 'pid':
                    value = json.loads(data)
                    data = json.dumps({{**value, 'pid': value['pid'] + 1}})
            return original_write(path, data, *args, **kwargs)

        def controlled_replace(path, target):
            if readiness_path(path) and fault == 'replace':
                raise OSError('Owned readiness replacement failure')
            return original_replace(path, target)

        Path.open = controlled_open
        Path.write_text = controlled_write
        Path.replace = controlled_replace
        """
    )

    async def launch(command, **kwargs):
        nonlocal process, control
        command = list(command)
        code_index = command.index("-c") + 1
        command[code_index] = "exec(" + repr(bootstrap) + ");" + command[code_index]
        kwargs["stdout"] = subprocess.PIPE
        process = await original_process(command, **kwargs)
        observed["process"] = process
        if held:
            control, _address = await anyio.to_thread.run_sync(listener.accept)
            control.settimeout(20)

            def receive():
                data = bytearray()
                while not data.endswith(b"\n"):
                    chunk = control.recv(4096)
                    if not chunk:
                        raise RuntimeError("Readiness writer control disconnected")
                    data.extend(chunk)
                return json.loads(data)

            observed["held"] = await anyio.to_thread.run_sync(receive)
            entered.set()
        return process

    async def release_on_reader_poll(seconds):
        if held and fault == "" and not observed["released"]:
            control.sendall(b"x")
            observed["released"] = True
        await original_sleep(seconds)

    async def observe_cleanup(child):
        observed["cleanup_called"] = True
        await original_cleanup(child)

    options = bench.Options(
        output=tmp_path / "measurement.json",
        samples=1,
        warmup=0,
        large_bytes=262144,
        work_ms=0,
        concurrency=1,
        order="http-first",
    )
    try:
        with monkeypatch.context() as patches:
            patches.setattr(anyio, "open_process", launch)
            patches.setattr(anyio, "sleep", release_on_reader_poll)
            patches.setattr(bench, "cleanup_process", observe_cleanup)
            yield bench, options, directory, observed, entered
        assert observed["cleanup_called"] is True
        assert process.returncode is not None
        with pytest.raises(anyio.ClosedResourceError):
            await process.stdout.receive()
        observed["pipe_closed"] = True
    finally:
        if control is not None:
            control.close()
        listener.close()
        if process is not None:
            await original_cleanup(process)


@pytest.mark.asyncio
async def test_readiness_is_complete_before_production_reader_observes_it(tmp_path, monkeypatch):
    # The actual writer is held after opening its file, not after publication.
    async with readiness_fixture(tmp_path, monkeypatch) as (bench, options, directory, observed, _):
        result = await bench.measure_mode("http", options, directory)
        assert observed["held"]["final_exists"] is False
        assert Path(observed["held"]["path"]).name == "ready.json.pending"
        assert observed["released"] is True
        ready = json.loads((directory / "ready.json").read_text(encoding="utf-8"))
        assert ready["pid"] == observed["process"].pid == result["fixture_metadata"]["pid"]
        assert ready["endpoint"].startswith("http://127.0.0.1:")
        assert not (directory / "ready.json.pending").exists()
        assert result["mode"] == "http"
        assert result["text_structured_parity"] is True
        assert set(result["warmed"]) == {"small", "state", "large", "job"}
        assert len(result["observations"]) == 10
        state = result["lifecycle"]["after_reconnect"]
        assert (state["pending"], state["retained"]) == (0, 0)
        assert state["registrations"] == 2
        assert result["lifecycle"]["partial_cancel"]["receiver_final_chunk_processed"] is True
        print(
            json.dumps({"readiness_held": observed["held"], "actual_transport": result}), flush=True
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["malformed", "pid", "exit", "write", "replace"])
async def test_readiness_failure_stays_visible_and_owned_child_is_closed(
    tmp_path, monkeypatch, fault
):
    async with readiness_fixture(tmp_path, monkeypatch, fault) as (
        bench,
        options,
        directory,
        observed,
        _,
    ):
        # Publication failures must terminate the owned fixture, not consume its deadline.
        error = json.JSONDecodeError if fault == "malformed" else RuntimeError
        match = "Expecting" if fault == "malformed" else "forked" if fault == "pid" else "exited"
        with pytest.raises(error, match=match) as failure:
            await bench.measure_mode("http", options, directory)
        if fault in {"write", "replace"}:
            assert observed["process"].returncode not in (None, 0)
        if fault in {"exit", "write", "replace"}:
            assert not (directory / "ready.json").exists()
        if fault == "exit":
            assert observed["process"].returncode == 17
        if fault in {"write", "replace"}:
            log = (directory / "server.stderr.log").read_text(encoding="utf-8")
            assert (
                f"Owned readiness {'write' if fault == 'write' else 'replacement'} failure" in log
            )
        if fault == "replace":
            assert (
                json.loads((directory / "ready.json.pending").read_text())["pid"]
                == observed["process"].pid
            )
        print(
            json.dumps(
                {
                    "fault": fault,
                    "error": str(failure.value),
                    "child_exit": observed["process"].returncode,
                }
            ),
            flush=True,
        )


@pytest.mark.asyncio
async def test_readiness_deadline_reaps_held_real_writer_without_publishing(tmp_path, monkeypatch):
    # Exercise the production 15-second deadline, without shortening or replacing it.
    async with readiness_fixture(tmp_path, monkeypatch, "deadline") as (
        bench,
        options,
        directory,
        observed,
        _,
    ):
        started = time.monotonic()
        with pytest.raises(TimeoutError):
            await bench.measure_mode("http", options, directory)
        elapsed = time.monotonic() - started
        assert elapsed >= 15
        assert observed["held"]["final_exists"] is False
        assert not (directory / "ready.json").exists()
        assert observed["released"] is False
        print(
            json.dumps(
                {"deadline_elapsed_seconds": elapsed, "child_exit": observed["process"].returncode}
            ),
            flush=True,
        )


@pytest.mark.asyncio
async def test_readiness_cancellation_reaps_held_real_writer(tmp_path, monkeypatch):
    async with readiness_fixture(tmp_path, monkeypatch, "cancel") as (
        bench,
        options,
        directory,
        observed,
        entered,
    ):
        scope = anyio.CancelScope()

        async def measure():
            with scope:
                await bench.measure_mode("http", options, directory)

        async with anyio.create_task_group() as group:
            group.start_soon(measure)
            with anyio.fail_after(10):
                await entered.wait()
            scope.cancel()
        assert scope.cancel_called is True
        assert observed["held"]["final_exists"] is False
        assert not (directory / "ready.json").exists()
        print(
            json.dumps({"readiness_cancelled": True, "child_exit": observed["process"].returncode}),
            flush=True,
        )
