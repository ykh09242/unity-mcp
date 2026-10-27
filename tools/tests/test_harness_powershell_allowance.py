"""Cold PowerShell startup uses the configurable test watchdog without wall sleeps."""

import asyncio
from pathlib import Path
import subprocess
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tools.tests import test_harness_cleanup_deadlines as case


@pytest.mark.asyncio
@pytest.mark.parametrize("scale", ["0.25", "0.1"])
async def test_powershell_case_observes_cold_startup_and_reaps_on_watchdog(
    tmp_path, monkeypatch, scale
):
    # Given: an owned child needs 11 virtual seconds to finish cold startup.
    started, finish = asyncio.Event(), asyncio.Event()
    budget = 60 * float(scale)
    output = "owned stdout evidence\nowned stderr evidence\nbounded cleanup failure observed\n"

    class Child:
        returncode = None
        kills = 0
        reaps = 0

        async def communicate(self):
            started.set()
            await finish.wait()
            if self.returncode is None:
                self.returncode = 0
            return output.encode(), b""

        def kill(self):
            self.kills += 1
            self.returncode = -9
            finish.set()

        async def wait(self):
            self.reaps += 1
            await finish.wait()
            return self.returncode

    child = Child()

    async def spawn(*_args, **_kwargs):
        return child

    def old_synchronous_start(command, **kwargs):
        if kwargs["timeout"] < 11:
            raise subprocess.TimeoutExpired(command, kwargs["timeout"], output="cold startup")
        return subprocess.CompletedProcess(command, 0, output, "")

    import shutil

    monkeypatch.setattr(shutil, "which", lambda _name: "owned-fake-powershell")
    monkeypatch.setenv("UNITY_MCP_TEST_TIMEOUT_SCALE", scale)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    monkeypatch.setattr(subprocess, "run", old_synchronous_start)
    loop = asyncio.get_running_loop()
    clock = [loop.time()]
    task = None
    started_waiter = None
    try:
        with monkeypatch.context() as virtual_clock:
            virtual_clock.setattr(loop, "time", lambda: clock[0])

            async def run_case():
                task_result = case.test_powershell_postkill_wait_is_bounded_and_preserves_logs(
                    tmp_path
                )
                await task_result

            task = asyncio.create_task(run_case())
            # The real timing helper has installed its timer before communicate starts.
            started_waiter = asyncio.create_task(started.wait())
            await asyncio.wait({task, started_waiter}, return_when=asyncio.FIRST_COMPLETED)
            if task.done():
                await task  # Preserve an immediate failure from the immutable baseline.
            clock[0] += 11
            if budget > 11:
                finish.set()
                # When: cold startup completes after the old fixed 10-second limit.
                await task
                # Then: the larger configured allowance succeeds without a sleep.
                assert child.kills == 0 and child.returncode == 0 and child.reaps == 1
            else:
                # When: the configured correctness watchdog expires first.
                with pytest.raises(AssertionError, match="UNITY_MCP_TEST_TIMEOUT_SCALE"):
                    await task
                # Then: its phase remains observable and the exact owned child is reaped.
                assert child.kills == 1 and child.returncode == -9 and child.reaps == 1
    finally:
        finish.set()
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        if started_waiter is not None:
            started_waiter.cancel()
            await asyncio.gather(started_waiter, return_exceptions=True)
    assert not [task for task in asyncio.all_tasks() if task.get_name().startswith("owned-child-")]
