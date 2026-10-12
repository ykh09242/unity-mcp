"""Worker timing validation and real serial/xdist outcome preservation."""

import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import xml.etree.ElementTree as ET

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ci_worker_timing import read_worker_timings, validate_worker_timings


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
    elif mode == "collection-error":
        cases = list(ET.parse(tmp_path / "junit.xml").iter("testcase"))
        assert any(case.find("error") is not None for case in cases)
        assert sum(worker["testCount"] for worker in report["workers"]) == sum(
            case.find("error") is None for case in cases
        )
    else:
        assert len(report["workers"]) < report["expectedWorkers"]
