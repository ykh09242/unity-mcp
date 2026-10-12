"""Opt-in, same-host pytest worker timing; test ordering and outcomes are unchanged."""

from __future__ import annotations

import json
import sys
from collections.abc import Generator
from pathlib import Path
from time import perf_counter

import pytest


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption("--ci-timing-output", help="Write observational worker timing JSON")


def pytest_configure(config: pytest.Config) -> None:
    if config.getoption("ci_timing_output"):
        config.pluginmanager.register(WorkerTimings(config), "ci-worker-timings")


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
