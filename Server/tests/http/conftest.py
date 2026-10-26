"""Share descriptors and retain each isolated HTTP worker's current test phase."""

import json
import os
from pathlib import Path

import pytest

from tests.http.prepared_tool_registration import prepared_tool_factory  # noqa: F401

_progress_path: Path | None = None
_last_nodeid = ""


def _record_progress(phase: str, nodeid: str | None = None) -> None:
    global _last_nodeid
    if _progress_path is None:
        return
    if nodeid is not None:
        _last_nodeid = nodeid
    # One file per owned worker keeps the last phase even when other workers produce
    # more output than the wrapper's bounded tail. Replace atomically on timeout reads.
    pending = _progress_path.with_suffix(".tmp")
    pending.write_text(json.dumps({"nodeid": _last_nodeid, "phase": phase}), encoding="utf-8")
    pending.replace(_progress_path)


def pytest_configure(config):
    global _progress_path
    worker = getattr(config, "workerinput", None)
    directory = os.environ.get("UNITY_MCP_HTTP_TEST_PROGRESS_DIR")
    if worker is not None and directory:
        _progress_path = Path(directory) / f"{worker['workerid']}.json"
        _progress_path.parent.mkdir(parents=True, exist_ok=True)
        _record_progress("collection")


@pytest.hookimpl(wrapper=True, tryfirst=True)
def pytest_runtest_setup(item):
    _record_progress("setup", item.nodeid)
    return (yield)


@pytest.hookimpl(wrapper=True, tryfirst=True)
def pytest_runtest_call(item):
    _record_progress("call", item.nodeid)
    return (yield)


@pytest.hookimpl(wrapper=True, tryfirst=True)
def pytest_runtest_teardown(item):
    _record_progress("teardown", item.nodeid)
    return (yield)


def pytest_runtest_logfinish(nodeid):
    _record_progress("finished", nodeid)


@pytest.hookimpl(wrapper=True, tryfirst=True)
def pytest_sessionfinish():
    _record_progress("session teardown")
    result = yield
    _record_progress("session finished")
    return result
