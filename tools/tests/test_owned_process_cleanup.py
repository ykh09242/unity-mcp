"""Owned async children drain pipes and settle cleanup under failure/cancellation."""

import asyncio
import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS))


@pytest.mark.asyncio
async def test_owned_child_drains_large_stdout_and_stderr():
    from owned_process import drain_owned_process

    # Given: each owned child pipe exceeds the asyncio/OS pipe buffers.
    child = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        "import sys; sys.stdout.buffer.write(b'o' * 3000000); sys.stderr.buffer.write(b'e' * 3000000)",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    # When: both pipes drain throughout the child lifetime.
    async with drain_owned_process(child, 2) as output:
        stdout, stderr = await asyncio.wait_for(asyncio.shield(output), 5)
    # Then: all bytes and a confirmed exit survive, with no owned observer left.
    assert stdout == b"o" * 3000000 and stderr == b"e" * 3000000
    assert child.returncode == 0
    assert not [task for task in asyncio.all_tasks() if task.get_name().startswith("owned-child-")]


@pytest.mark.asyncio
async def test_receive_timeout_kills_and_reaps_owned_large_output_child():
    from owned_process import drain_owned_process

    # Given: an owned child fills stderr before waiting for more work.
    child = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        "import sys,time; sys.stderr.buffer.write(b'e'*3000000); sys.stderr.flush(); time.sleep(60)",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    # When: an independent receiver timeout interrupts the owning operation.
    with pytest.raises(TimeoutError):
        async with drain_owned_process(child, 2):
            await asyncio.wait_for(asyncio.Future(), 0.1)
    # Then: cleanup has reaped the child and settled all owned pipe observers.
    assert child.returncode is not None
    assert not [task for task in asyncio.all_tasks() if task.get_name().startswith("owned-child-")]


@pytest.mark.asyncio
async def test_cancelled_communicate_closes_owned_pipe_transport():
    from owned_process import drain_owned_process, ProcessCleanupTimeout

    # Given: the only communicate task was cancelled before cleanup.
    child = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        "import time; time.sleep(60)",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    # When: the context observes the cancelled drain.
    with pytest.raises(ProcessCleanupTimeout):
        async with drain_owned_process(child, 2) as output:
            output.cancel()
            await asyncio.gather(output, return_exceptions=True)
    # Then: pipes close and exit is confirmed rather than a bare unbounded wait.
    assert child.returncode is not None
    assert not [task for task in asyncio.all_tasks() if task.get_name().startswith("owned-child-")]


@pytest.mark.asyncio
async def test_stalled_child_reap_and_cancel_suppression_have_independent_budget():
    from owned_process import drain_owned_process

    # Given: a fake wait never completes; drain cancellation needs transport close.
    closed = asyncio.Event()

    class Child:
        returncode = None
        _transport = SimpleNamespace(close=closed.set)

        def kill(self):
            pass

        async def communicate(self):
            try:
                await asyncio.Future()
            except asyncio.CancelledError:
                await closed.wait()
            return b"", b""

        async def wait(self):
            await asyncio.Future()

    # When: the operation fails and cleanup cannot confirm process exit.
    with pytest.raises(ValueError, match="original") as error:
        async with drain_owned_process(Child(), 0.05):
            await asyncio.sleep(0)
            raise ValueError("original")
    # Then: the original error carries cleanup evidence and no owned tasks remain.
    assert closed.is_set()
    assert any("cleanup failed" in note for note in error.value.__notes__)
    assert not [task for task in asyncio.all_tasks() if task.get_name().startswith("owned-child-")]


@pytest.mark.asyncio
async def test_codec_receiver_error_resolves_waiter_and_kills_owned_sender(tmp_path, monkeypatch):
    # Given: a real loopback sender emits malformed start metadata.
    spec = importlib.util.spec_from_file_location(
        "codec_measure_cleanup", TOOLS / "tests/fixtures/large_result_codec/measure.py"
    )
    measure = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(measure)
    sender = tmp_path / "owned_sender.py"
    sender.write_text("""import asyncio, sys
from websockets.asyncio.client import connect
async def main():
    async with connect(sys.argv[4]) as socket:
        await socket.send("{")
        await asyncio.Future()
asyncio.run(main())
""")
    processes = []
    create = asyncio.create_subprocess_exec

    async def owned_spawn(*args, **kwargs):
        child = await create(*args, **kwargs)
        processes.append(child)
        return child

    monkeypatch.setattr(measure.asyncio, "create_subprocess_exec", owned_spawn)
    # When: the receiver raises, its owning operation fails before its long allowance.
    with pytest.raises(json.JSONDecodeError):
        await asyncio.wait_for(
            measure.wire_case(
                tmp_path,
                [sys.executable, sender],
                "bad",
                b"{}",
                "bad",
                1,
                receive_timeout=30,
                cleanup_timeout=1,
            ),
            5,
        )
    # Then: failure is immediate and the sole owned child is reaped.
    assert len(processes) == 1 and processes[0].returncode is not None


@pytest.mark.asyncio
async def test_repeated_operation_cancellation_does_not_cancel_owned_cleanup():
    from owned_process import drain_owned_process

    # Given: a child whose native-style waits settle only after transport close.
    started, killed, closed = asyncio.Event(), asyncio.Event(), asyncio.Event()

    class Child:
        returncode = None
        _transport = SimpleNamespace(close=closed.set)

        def kill(self):
            killed.set()

        async def communicate(self):
            started.set()
            await closed.wait()
            return b"", b""

        async def wait(self):
            await closed.wait()
            return -1

    async def operation():
        async with drain_owned_process(Child(), 0.05):
            await asyncio.Future()

    worker = asyncio.create_task(operation())
    await started.wait()
    # When: cancellation arrives in the operation and again during child cleanup.
    worker.cancel()
    await killed.wait()
    worker.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(worker, 1)
    # Then: the bounded cleanup owner closes the pipes and all observers settle.
    assert closed.is_set()
    assert not [task for task in asyncio.all_tasks() if task.get_name().startswith("owned-child-")]
