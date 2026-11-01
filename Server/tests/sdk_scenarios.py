"""Opt-in module-owned real-SDK scenarios, isolated from collection-time SDK stubs."""

import atexit
import json
import os
from pathlib import Path
from queue import Empty, Queue
import subprocess
from threading import Thread
import textwrap
import time

import pytest
from tests.stdio_process import native_python

_active = None
_MAX_FRAME = 4 * 1024 * 1024


class _Host:
    def __init__(self):
        self.process = None
        self.replies = Queue()
        self.errors = []
        self.readers = []
        self.sequence = 0
        self.startup = {}

    def _read(self, stream, replies):
        for line in iter(lambda: stream.readline(_MAX_FRAME + 1), ""):
            if len(line) > _MAX_FRAME:
                line = "SDK host output exceeded its frame limit"
            if replies:
                self.replies.put(line)
            else:
                self.errors.append(line[-65536:])
                self.errors[:] = self.errors[-8:]
        if replies:
            self.replies.put("")

    def _reply(self, timeout):
        try:
            line = self.replies.get(timeout=timeout)
        except Empty as error:
            raise subprocess.TimeoutExpired("SDK scenario", timeout) from error
        if not line:
            raise AssertionError("SDK host exited: " + "".join(self.errors))
        return json.loads(line)

    def run(self, source, env, timeout):
        deadline = time.monotonic() + timeout
        try:
            if self.process is None:
                self.errors.clear()
                root = Path(__file__).resolve().parents[1]
                command = native_python(
                    [str(Path(__file__).with_name("sdk_scenario_worker.py"))], (root, root / "src")
                )
                command[1:1] = ["-B", "-u", "-W", "error"]
                self.process = subprocess.Popen(
                    command,
                    cwd=root,
                    env=env,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    encoding="utf-8",
                )
                for stream, replies in ((self.process.stdout, True), (self.process.stderr, False)):
                    reader = Thread(target=self._read, args=(stream, replies), daemon=True)
                    reader.start()
                    self.readers.append(reader)
                self.startup = self._reply(max(0, deadline - time.monotonic()))
                assert self.startup.pop("ready") is True
                assert self.startup["pid"] == self.process.pid, (
                    "SDK host must own the actual interpreter"
                )
            self.sequence += 1
            request = json.dumps(
                {"id": self.sequence, "source": textwrap.dedent(source), "env": env}
            )
            assert len(request) <= _MAX_FRAME, "SDK scenario exceeds its request limit"
            self.process.stdin.write(request + "\n")
            self.process.stdin.flush()
            reply = self._reply(max(0, deadline - time.monotonic()))
            assert reply["id"] == self.sequence, "SDK host returned a stale scenario receipt"
            timing = env.get("UNITY_MCP_SCENARIO_TIMINGS")
            if timing:
                with Path(timing).open("a", encoding="utf-8") as stream:
                    stream.write(
                        json.dumps(
                            {
                                **self.startup,
                                **reply["timing"],
                                "case": env.get("PYTEST_CURRENT_TEST"),
                            }
                        )
                        + "\n"
                    )
            self.startup = {}
            result = subprocess.CompletedProcess(
                "SDK scenario", reply["returncode"], reply["stdout"], reply["stderr"]
            )
            if result.returncode:
                self.close()
            return result
        except BaseException:
            self.close()
            raise

    def close(self):
        process, self.process = self.process, None
        if process is None:
            return
        try:
            process.stdin.close()
            process.wait(timeout=2)
        except (OSError, subprocess.TimeoutExpired):
            process.kill()
            process.wait(timeout=2)
        finally:
            for reader in self.readers:
                reader.join(timeout=2)
            process.stdout.close()
            process.stderr.close()
            self.readers.clear()
            self.replies = Queue()


@pytest.fixture(scope="module", autouse=True)
def sdk_scenario_host():
    """Own one lazy SDK process for this opted-in module, never an outcome cache."""
    global _active
    assert _active is None
    _active = _Host()
    try:
        yield _active
    finally:
        try:
            _active.close()
        finally:
            _active = None


def run_sdk_scenario(source: str, *, env=None, timeout: float = 30):
    """Execute every request in fresh application modules and return its actual receipt."""
    assert _active is not None, "Import the sdk_scenario_host fixture in the owning test module"
    scenario_env = {**os.environ, "UNITY_MCP_DISABLE_TELEMETRY": "1"} if env is None else env
    return _active.run(source, scenario_env, timeout)


def run_sdk_regression(source: str) -> None:
    result = run_sdk_scenario(source)
    assert result.returncode == 0, result.stdout + result.stderr


atexit.register(lambda: _active.close() if _active is not None else None)
