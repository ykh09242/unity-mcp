"""Worker timing validation and real serial/xdist outcome preservation."""

import copy
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
from types import SimpleNamespace
import xml.etree.ElementTree as ET

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ci_worker_timing import read_worker_timings, validate_worker_timings
from pytest_ci_timing import WorkerProgress


def sample():
    return {
        "schema": 1,
        "distribution": "loadfile",
        "expectedWorkers": 1,
        "complete": True,
        "elapsedSeconds": 12,
        "workers": [
            {
                "id": "gw0",
                "testCount": 3,
                "elapsedSeconds": 10,
                "collectionSeconds": 2,
                "protocolSeconds": 5,
                "setupSeconds": 1,
                "callSeconds": 3,
                "teardownSeconds": 0.5,
                "firstTestSeconds": 4,
                "lastTestSeconds": 10,
                "finishedSeconds": 11,
                "files": {"tests/test_owned.py": {"testCount": 3}},
            }
        ],
    }


def test_compact_worker_report_preserves_phases_without_raw_file_details():
    raw = sample()
    original = copy.deepcopy(raw)
    compact = validate_worker_timings(raw)
    assert raw == original
    assert "files" not in compact["workers"][0]
    assert compact["workers"][0]["collectionSeconds"] == 2
    assert compact["workers"][0]["protocolSeconds"] == 5


@pytest.mark.parametrize(
    "field,value",
    [
        ("callSeconds", float("nan")),
        ("callSeconds", float("inf")),
        ("setupSeconds", -1),
        ("teardownSeconds", True),
        ("testCount", True),
        ("collectionSeconds", 11),
        ("callSeconds", 6),
        ("firstTestSeconds", None),
        ("lastTestSeconds", 13),
        ("finishedSeconds", 13),
        ("id", "<script>"),
    ],
)
def test_invalid_worker_observations_are_rejected(field, value):
    raw = sample()
    raw["workers"][0][field] = value
    with pytest.raises(ValueError):
        validate_worker_timings(raw)


@pytest.mark.parametrize("complete", [False, True])
def test_missing_workers_are_explicitly_incomplete(complete):
    raw = sample()
    raw.update(expectedWorkers=2, complete=complete)
    if complete:
        with pytest.raises(ValueError):
            validate_worker_timings(raw)
    else:
        assert validate_worker_timings(raw)["complete"] is False


def test_duplicate_workers_are_rejected():
    raw = sample()
    raw["expectedWorkers"] = 2
    raw["workers"].append(copy.deepcopy(raw["workers"][0]))
    with pytest.raises(ValueError):
        validate_worker_timings(raw)


@pytest.mark.parametrize("ordering", [False, "first-file:<script>", "first-file:" + "x" * 201])
def test_invalid_file_ordering_is_rejected(ordering):
    raw = sample()
    raw["ordering"] = ordering
    with pytest.raises(ValueError, match="ordering"):
        validate_worker_timings(raw)


@pytest.mark.parametrize("workers", [0, 2])
def test_real_worker_plugin_preserves_failures_skips_and_file_isolation(tmp_path, workers):
    (tmp_path / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
    (tmp_path / "test_a.py").write_text(
        "import pytest\n"
        "@pytest.fixture\ndef state():\n    yield []\n"
        "def test_first(state):\n    assert state == []\n    state.append(1)\n"
        "def test_second(state):\n    assert state == []\n"
        "def test_failed():\n    assert False\n"
        "def test_skipped():\n    pytest.skip('owned skip')\n",
        encoding="utf-8",
    )
    (tmp_path / "test_b.py").write_text(
        "import pytest\n"
        "@pytest.mark.parametrize('value', [1, 2, 3])\n"
        "def test_value(value):\n    assert value > 0\n",
        encoding="utf-8",
    )
    output = tmp_path / "workers.json"
    environment = {
        **os.environ,
        "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
        "PYTEST_ADDOPTS": "",
        "PYTHONPATH": str(Path(__file__).resolve().parents[1]),
    }
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-p",
            "xdist",
            "-p",
            "pytest_ci_timing",
            "-n",
            str(workers),
            "--dist=loadfile",
            "--max-worker-restart=0",
            "-q",
            "-W",
            "error",
            "--ci-timing-output",
            str(output),
            "--ci-progress-dir",
            str(tmp_path / "progress"),
            "--junitxml",
            str(tmp_path / "junit.xml"),
            str(tmp_path),
        ],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 1, result.stdout + result.stderr
    cases = list(ET.parse(tmp_path / "junit.xml").iter("testcase"))
    assert len(cases) == 7
    assert sum(case.find("failure") is not None for case in cases) == 1
    assert sum(case.find("skipped") is not None for case in cases) == 1
    report = read_worker_timings(output)
    assert report["ordering"] == ("case-count" if workers else "collection")
    assert report["complete"] is False
    assert len(report["workers"]) == (workers or 1)
    assert sum(worker["testCount"] for worker in report["workers"]) == 7
    assert all(worker["collectionSeconds"] > 0 for worker in report["workers"])
    raw = json.loads(output.read_text(encoding="utf-8"))
    files = [file for worker in raw["workers"] for file in worker["files"]]
    assert sorted(files) == ["test_a.py", "test_b.py"]
    assert sorted(
        value["testCount"] for worker in raw["workers"] for value in worker["files"].values()
    ) == [3, 4]
    progress = [json.loads(path.read_text()) for path in (tmp_path / "progress").glob("*.json")]
    assert len(progress) == (workers + 1 if workers else 1)
    assert {entry["workerId"] for entry in progress} == (
        {"main", "gw0", "gw1"} if workers else {"main"}
    )
    assert len({entry["pid"] for entry in progress}) == len(progress)
    assert all(entry["complete"] and entry["phase"] == "unconfigured" for entry in progress)
    assert all(entry["exitstatus"] == 1 for entry in progress if entry["workerId"] == "main")

    if workers == 0:
        # An unwritable observational target must not turn successful tests into failures.
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                "-p",
                "pytest_ci_timing",
                "-q",
                "-W",
                "error",
                "--ci-timing-output",
                str(output / "unwritable.json"),
                "--ci-progress-dir",
                str(output / "unwritable-progress"),
                str(tmp_path / "test_b.py"),
            ],
            cwd=tmp_path,
            env=environment,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("mode", ["success", "collection-error", "worker-crash"])
def test_real_worker_plugin_reports_completed_and_partial_sessions(tmp_path, mode):
    (tmp_path / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
    (tmp_path / "test_first.py").write_text(
        "def test_first():\n    assert True\n", encoding="utf-8"
    )
    sources = {
        "success": "def test_second():\n    assert True\n",
        "collection-error": "raise RuntimeError('owned collection failure')\n",
        "worker-crash": "def test_second():\n    import os\n    os._exit(17)\n",
    }
    (tmp_path / "test_second.py").write_text(sources[mode], encoding="utf-8")
    output = tmp_path / "workers.json"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-p",
            "xdist",
            "-p",
            "pytest_ci_timing",
            "-n",
            "2",
            "--dist=loadfile",
            "--max-worker-restart=0",
            "-q",
            "-W",
            "error",
            "--ci-timing-output",
            str(output),
            "--ci-progress-dir",
            str(tmp_path / "progress"),
            "--junitxml",
            str(tmp_path / "junit.xml"),
            str(tmp_path),
        ],
        cwd=tmp_path,
        env={
            **os.environ,
            "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
            "PYTEST_ADDOPTS": "",
            "PYTHONPATH": str(Path(__file__).resolve().parents[1]),
        },
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    report = read_worker_timings(output)
    assert result.returncode == (0 if mode == "success" else 1), result.stdout + result.stderr
    assert report["complete"] == (mode == "success")
    if mode == "success":
        assert sum(worker["testCount"] for worker in report["workers"]) == 2
        progress = [json.loads(path.read_text()) for path in (tmp_path / "progress").glob("*.json")]
        assert len(progress) == 3
        assert all(entry["complete"] and entry["exitstatus"] == 0 for entry in progress)
    elif mode == "collection-error":
        cases = list(ET.parse(tmp_path / "junit.xml").iter("testcase"))
        assert any(case.find("error") is not None for case in cases)
        assert sum(worker["testCount"] for worker in report["workers"]) == sum(
            case.find("error") is None for case in cases
        )
    else:
        assert len(report["workers"]) < report["expectedWorkers"]
        progress = [json.loads(path.read_text()) for path in (tmp_path / "progress").glob("*.json")]
        crashed = [entry for entry in progress if not entry["complete"]]
        assert len(crashed) == 1
        assert crashed[0]["nodeid"] == "test_second.py::test_second"
        assert crashed[0]["phase"] == "call"


def stop_owned_pytest(process):
    """Reap the controller and its descendants if a controlled probe fails or times out."""
    if process.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            capture_output=True,
            check=False,
            timeout=10,
        )
    else:
        getattr(os, "killpg")(process.pid, getattr(signal, "SIGKILL"))
    process.communicate(timeout=10)


def test_progress_is_bounded_unique_and_keeps_last_atomic_snapshot_on_io_failure(
    tmp_path, monkeypatch
):
    config = SimpleNamespace(getoption=lambda _name: str(tmp_path))
    progress = WorkerProgress(config)
    other = WorkerProgress(config)
    assert progress.output != other.output
    progress.write("call", "\ud55c" * 10000)
    original = progress.output.read_bytes()
    record = json.loads(original)
    assert record["nodeid"] == "\ud55c" * 4096
    assert record["nodeidTruncated"] is True
    assert len(original) < 26000

    original_replace = Path.replace

    def deny_replace(source, target):
        if source == progress.output.with_suffix(".tmp"):
            raise PermissionError("owned read-only target")
        return original_replace(source, target)

    monkeypatch.setattr(Path, "replace", deny_replace)
    progress.write("teardown", "test_owned.py::test_owned")
    assert progress.disabled
    assert progress.output.read_bytes() == original
    # One failed staging file is retained; subsequent hooks perform no more I/O.
    staging = progress.output.with_suffix(".tmp").read_bytes()
    progress.write("session-finalization")
    assert progress.output.with_suffix(".tmp").read_bytes() == staging
    assert len(list(tmp_path.iterdir())) == 3


@pytest.mark.parametrize(
    "phase", ["setup", "call", "teardown", "collection", "session-finalization", "unconfigure"]
)
def test_stopped_pytest_retains_actual_phase_after_subtest_reports(tmp_path, phase):
    (tmp_path / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
    (tmp_path / "test_stopped.py").write_text(
        "import os, time\nfrom pathlib import Path\nimport pytest\n"
        "def block(phase):\n"
        f"    if phase == {phase!r}:\n"
        "        Path('ready').write_text(str(os.getpid()))\n"
        "        time.sleep(30)\n"
        "@pytest.fixture\ndef state():\n"
        "    block('setup')\n    yield\n    block('teardown')\n"
        "def test_stopped(state, subtests):\n"
        "    with subtests.test(part='first'):\n        assert True\n"
        "    block('call')\n",
        encoding="utf-8",
    )
    test_phase = phase in {"setup", "call", "teardown"}
    if not test_phase:
        (tmp_path / "conftest.py").write_text(
            "import os, time\nfrom pathlib import Path\n"
            "def block():\n"
            "    Path('ready').write_text(str(os.getpid()))\n"
            "    time.sleep(30)\n"
            + {
                "collection": "def pytest_collection(session):\n    block()\n",
                "session-finalization": "def pytest_sessionfinish(session):\n    block()\n",
                "unconfigure": "def pytest_unconfigure(config):\n    block()\n",
            }[phase],
            encoding="utf-8",
        )
    progress_dir = tmp_path / "progress"
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "pytest",
            "-p",
            "xdist",
            "-p",
            "pytest_ci_timing",
            "-n",
            "2" if test_phase else "0",
            "--max-worker-restart=0",
            "-q",
            "-W",
            "error",
            "--ci-progress-dir",
            str(progress_dir),
            str(tmp_path),
        ],
        cwd=tmp_path,
        env={
            **os.environ,
            "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
            "PYTEST_ADDOPTS": "",
            "PYTHONPATH": str(Path(__file__).resolve().parents[1]),
        },
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=os.name != "nt",
    )
    try:
        deadline = time.monotonic() + 15
        ready = tmp_path / "ready"
        while not ready.exists() and process.poll() is None and time.monotonic() < deadline:
            time.sleep(0.02)
        assert ready.exists(), "Controlled worker never entered its target phase"
        pid = int(ready.read_text())
        records = [json.loads(path.read_text()) for path in progress_dir.glob("*.json")]
        entry = next(record for record in records if record["pid"] == pid)
        assert entry["workerId"] in ({"gw0", "gw1"} if test_phase else {"main"})
        assert entry["nodeid"] == ("test_stopped.py::test_stopped" if test_phase else None)
        assert entry["phase"] == phase
        assert entry["timestamp"] and entry["complete"] is False
        assert len(records) == (3 if test_phase else 1)
        os.kill(pid, signal.SIGTERM)
        stdout, stderr = process.communicate(timeout=15)
        assert process.returncode != 0, stdout + stderr
        retained = [json.loads(path.read_text()) for path in progress_dir.glob("*.json")]
        assert next(record for record in retained if record["pid"] == pid) == entry
    finally:
        stop_owned_pytest(process)


def test_ci_progress_is_enabled_and_uploaded_after_cancellation():
    root = Path(__file__).resolve().parents[2]
    workflow = yaml.safe_load((root / ".github/workflows/python-tests.yml").read_text())
    steps = workflow["jobs"]["test"]["steps"]
    commands = [step["run"] for step in steps if "-p pytest_ci_timing" in step.get("run", "")]
    assert len(commands) == 3
    assert all("--ci-progress-dir=test-worker-progress" in command for command in commands)
    upload = next(step for step in steps if step.get("name") == "Upload test results")
    assert upload["if"] == "always()"
    assert "Server/test-worker-progress/" in upload["with"]["path"].splitlines()
