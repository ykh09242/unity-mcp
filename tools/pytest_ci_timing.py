"""Opt-in, same-host pytest worker timing; test ordering and outcomes are unchanged."""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Generator
from datetime import datetime, timezone
from io import open as progress_open
from os import replace as progress_replace
from pathlib import Path
from time import perf_counter
from uuid import uuid4

import pytest


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption("--ci-timing-output", help="Write observational worker timing JSON")
    parser.addoption("--ci-progress-dir", help="Persist bounded per-process pytest progress JSON")


def pytest_configure(config: pytest.Config) -> None:
    if config.getoption("ci_timing_output"):
        config.pluginmanager.register(WorkerTimings(config), "ci-worker-timings")
    if config.getoption("ci_progress_dir"):
        config.pluginmanager.register(WorkerProgress(config), "ci-worker-progress")


class WorkerProgress:
    """Replace one snapshot per process; retain the last observation after abrupt death."""

    def __init__(self, config: pytest.Config) -> None:
        worker = getattr(config, "workerinput", None)
        self.worker_id = worker["workerid"][:128] if worker else "main"
        self.pid = os.getpid()
        # A run token also separates nested pytest sessions and reused worker IDs/PIDs.
        self.output = Path(config.getoption("ci_progress_dir")) / f"{self.pid}-{uuid4().hex}.json"
        self.disabled = False
        self.exitstatus: int | None = None
        self.session_finished = False
        # Tests deliberately patch shared JSON and filesystem APIs. Keep this observer's
        # encoder methods and built-in I/O independent of those application probes.
        encoder = json.JSONEncoder(ensure_ascii=True)
        # Shadow the class method before later class-level monkeypatches can replace it.
        encoder.iterencode = encoder.iterencode
        self._encode = encoder.encode
        self._open = progress_open
        self._replace = progress_replace
        try:
            self.output = self.output.resolve()
            self._output_name = str(self.output)
            self._staging_name = str(self.output.with_suffix(".tmp"))
            self.output.parent.mkdir(parents=True, exist_ok=True)
        except (OSError, ValueError):
            self.disabled = True
        self.write("configuration")

    def write(self, phase: str, nodeid: str | None = None, *, complete: bool = False) -> None:
        """Keep at most one bounded JSON and one staging file; diagnostics never warn."""
        if self.disabled:
            return
        payload = {
            "schema": 1,
            "workerId": self.worker_id,
            "pid": self.pid,
            "nodeid": nodeid[:4096] if nodeid is not None else None,
            "nodeidTruncated": nodeid is not None and len(nodeid) > 4096,
            "phase": phase,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "complete": complete,
            "exitstatus": self.exitstatus,
        }
        try:
            with self._open(self._staging_name, "w", encoding="utf-8") as staging:
                staging.write(self._encode(payload) + "\n")
            self._replace(self._staging_name, self._output_name)
        except (OSError, ValueError):
            # Preserve the last good snapshot and avoid repeated failing I/O or -W error.
            self.disabled = True

    @pytest.hookimpl(wrapper=True, tryfirst=True)
    def pytest_sessionstart(self) -> Generator[None, None, None]:
        self.write("session-start")
        result = yield
        self.write("waiting-for-collection")
        return result

    @pytest.hookimpl(wrapper=True, tryfirst=True)
    def pytest_collection(self) -> Generator[None, None, None]:
        self.write("collection")
        result = yield
        self.write("waiting-for-tests")
        return result

    @pytest.hookimpl(wrapper=True, tryfirst=True)
    def pytest_runtestloop(self) -> Generator[None, bool | None, bool | None]:
        self.write("runtest-loop")
        result = yield
        self.write("waiting-for-session-finish")
        return result

    @pytest.hookimpl(wrapper=True, tryfirst=True)
    def pytest_runtest_protocol(
        self, item: pytest.Item
    ) -> Generator[None, bool | None, bool | None]:
        self.write("protocol", item.nodeid)
        result = yield
        self.write("between-tests")
        return result

    @pytest.hookimpl(wrapper=True, tryfirst=True)
    def pytest_runtest_setup(self, item: pytest.Item) -> Generator[None, None, None]:
        self.write("setup", item.nodeid)
        try:
            return (yield)
        finally:
            self.write("report:setup", item.nodeid)

    @pytest.hookimpl(wrapper=True, tryfirst=True)
    def pytest_runtest_call(self, item: pytest.Item) -> Generator[None, None, None]:
        self.write("call", item.nodeid)
        try:
            return (yield)
        finally:
            self.write("report:call", item.nodeid)

    @pytest.hookimpl(wrapper=True, tryfirst=True)
    def pytest_runtest_teardown(self, item: pytest.Item) -> Generator[None, None, None]:
        self.write("teardown", item.nodeid)
        try:
            return (yield)
        finally:
            self.write("report:teardown", item.nodeid)

    @pytest.hookimpl(wrapper=True, tryfirst=True)
    def pytest_sessionfinish(
        self, session: pytest.Session, exitstatus: int
    ) -> Generator[None, None, None]:
        self.exitstatus = int(exitstatus)
        self.write("session-finalization")
        result = yield
        self.exitstatus = int(session.exitstatus)
        self.session_finished = True
        self.write("session-finished")
        return result

    @pytest.hookimpl(wrapper=True, tryfirst=True)
    def pytest_unconfigure(self) -> Generator[None, None, None]:
        self.write("unconfigure")
        result = yield
        # Completion describes the observed lifecycle, independently of test success.
        self.write("unconfigured", complete=self.session_finished)
        return result


class WorkerTimings:
    """Accumulate timing observations for one process until its session finishes."""

    def __init__(self, config: pytest.Config) -> None:
        self.config = config
        self.started = perf_counter()
        self.collection = 0.0
        self.files: dict[str, dict] = {}
        self.workers: list[dict] = []
        self.complete = True
        self.worker = getattr(config, "workerinput", None)

    @pytest.hookimpl(wrapper=True)
    def pytest_collection(self) -> Generator[None, None, None]:
        started = perf_counter()
        try:
            return (yield)
        finally:
            self.collection += perf_counter() - started

    @pytest.hookimpl(wrapper=True)
    def pytest_runtest_protocol(
        self, item: pytest.Item
    ) -> Generator[None, bool | None, bool | None]:
        started = perf_counter()
        name = item.nodeid.split("::", 1)[0]
        entry = self.files.setdefault(
            name,
            {
                "testCount": 0,
                "protocolSeconds": 0.0,
                "setupSeconds": 0.0,
                "callSeconds": 0.0,
                "teardownSeconds": 0.0,
                "firstTestSeconds": started,
                "lastTestSeconds": started,
            },
        )
        try:
            return (yield)
        finally:
            finished = perf_counter()
            entry["testCount"] += 1
            entry["protocolSeconds"] += finished - started
            entry["lastTestSeconds"] = finished

    def pytest_runtest_logreport(self, report: pytest.TestReport) -> None:
        # Controller reports duplicate worker events; only local protocols have entries.
        entry = self.files.get(report.nodeid.split("::", 1)[0])
        if entry is not None and report.when in {"setup", "call", "teardown"}:
            entry[f"{report.when}Seconds"] += report.duration

    @pytest.hookimpl(optionalhook=True)
    def pytest_testnodedown(self, node, error) -> None:
        # Abrupt worker death never sends workerfinished or creates workeroutput.
        entry = getattr(node, "workeroutput", {}).get("ci_worker_timing")
        if error is not None or entry is None:
            self.complete = False
        if entry is not None:
            self.workers.append(entry)

    def pytest_sessionfinish(self, exitstatus: int) -> None:
        finished = perf_counter()
        if self.worker is not None or not getattr(self.config.option, "numprocesses", 0):
            entry = {
                "id": self.worker["workerid"] if self.worker else "main",
                "elapsedSeconds": finished - self.started,
                "collectionSeconds": self.collection,
                "firstTestSeconds": min(
                    (value["firstTestSeconds"] for value in self.files.values()), default=None
                ),
                "lastTestSeconds": max(
                    (value["lastTestSeconds"] for value in self.files.values()), default=None
                ),
                "finishedSeconds": finished,
                "files": self.files,
            }
            for field in (
                "testCount",
                "protocolSeconds",
                "setupSeconds",
                "callSeconds",
                "teardownSeconds",
            ):
                entry[field] = sum(value[field] for value in self.files.values())
            if self.worker is not None:
                self.config.workeroutput["ci_worker_timing"] = entry
                return
            self.workers.append(entry)

        # perf_counter uses one clock for local processes; remote xdist hosts are unsupported.
        for entry in self.workers:
            for value in (entry, *entry["files"].values()):
                for field in ("firstTestSeconds", "lastTestSeconds", "finishedSeconds"):
                    if value.get(field) is not None:
                        value[field] = max(0.0, value[field] - self.started)
        count = getattr(self.config.option, "numprocesses", 0) or 1
        distribution = getattr(self.config.option, "dist", "no")
        ordering = (
            "case-count"
            if distribution == "loadfile" and getattr(self.config.option, "loadscopereorder", True)
            else "collection"
        )
        first_file = self.config.getoption("ci_first_file", default=None)
        if first_file:
            ordering = f"first-file:{first_file}"
        payload = {
            "schema": 1,
            "distribution": distribution,
            "ordering": ordering,
            "expectedWorkers": count,
            "complete": self.complete and len(self.workers) == count and exitstatus == 0,
            "elapsedSeconds": finished - self.started,
            "workers": sorted(self.workers, key=lambda value: value["id"]),
        }
        try:
            output = Path(self.config.getoption("ci_timing_output"))
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(json.dumps(payload, allow_nan=False) + "\n", encoding="utf-8")
        except OSError as exc:
            # Reporting cannot change a test outcome or introduce a -W error failure.
            sys.stderr.write(f"CI worker timing output unavailable: {exc}\n")
