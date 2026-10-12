"""Real loadfile workers preserve file grouping and results with opt-in priority."""

import json
import os
from pathlib import Path
import subprocess
import sys
import xml.etree.ElementTree as ET

import pytest


ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def suite(tmp_path):
    (tmp_path / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
    (tmp_path / "conftest.py").write_text(
        "import json\nimport os\nfrom pathlib import Path\nimport pytest\n"
        "def write(config, name, value, append=False):\n"
        "    folder = Path(os.environ['OWNED_RECEIPTS'])\n"
        "    worker = config.workerinput['workerid']\n"
        "    with (folder / (worker + '-' + name)).open('a' if append else 'w', encoding='utf-8') as stream:\n"
        "        stream.write(json.dumps(value) + '\\n')\n"
        "def pytest_collection_modifyitems(items):\n"
        "    items[:] = [item for item in items if 'heavy' not in item.nodeid] + [item for item in items if 'heavy' in item.nodeid]\n"
        "def pytest_collection_finish(session):\n"
        "    if hasattr(session.config, 'workerinput'):\n"
        "        write(session.config, 'collection.json', [item.nodeid for item in session.items])\n"
        "def pytest_runtest_setup(item):\n"
        "    write(item.config, 'protocol.jsonl', item.nodeid, True)\n"
        "@pytest.fixture(scope='module', autouse=True)\n"
        "def owned_module(request):\n"
        "    write(request.config, 'fixtures.jsonl', ['setup', request.node.nodeid], True)\n"
        "    yield\n"
        "    write(request.config, 'fixtures.jsonl', ['teardown', request.node.nodeid], True)\n",
        encoding="utf-8",
    )
    sources = {
        "test_a.py": "import pytest\n@pytest.mark.parametrize('value', [1, 2, 3])\ndef test_values(value):\n    assert value > 0\n",
        "test_b.py": "def test_first():\n    assert True\ndef test_second():\n    assert True\n",
        "test_c.py": "def test_first():\n    assert True\ndef test_second():\n    assert True\ndef test_third():\n    assert True\ndef test_fourth():\n    assert True\n",
        "test_heavy.py": "def test_owned_heavy():\n    assert True\n",
        "test_outcomes.py": "import pytest\ndef test_failed():\n    assert False\ndef test_skipped():\n    pytest.skip('owned skip')\n",
    }
    for name, source in sources.items():
        (tmp_path / name).write_text(source, encoding="utf-8")
    return tmp_path


def execute(suite, label, options):
    """Run only the owned miniature suite and retain its real worker receipts."""
    receipt = suite / label
    receipt.mkdir()
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-p",
            "xdist",
            "-p",
            "pytest_ci_schedule",
            "-p",
            "pytest_ci_timing",
            "--ci-timing-output",
            str(receipt / "workers.json"),
            "--max-worker-restart=0",
            "-q",
            "-W",
            "error",
            "--junitxml",
            str(receipt / "junit.xml"),
            *options,
            str(suite),
        ],
        cwd=suite,
        env={
            **os.environ,
            "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
            "PYTEST_ADDOPTS": "",
            "PYTHONPATH": str(ROOT / "tools"),
            "OWNED_RECEIPTS": str(receipt),
        },
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    (receipt / "output.txt").write_text(result.stdout + result.stderr, encoding="utf-8")
    return result, receipt


def outcomes(receipt):
    return sorted(
        (
            case.get("classname"),
            case.get("name"),
            "failure"
            if case.find("failure") is not None
            else "skipped"
            if case.find("skipped") is not None
            else "passed",
        )
        for case in ET.parse(receipt / "junit.xml").iter("testcase")
    )


def test_priority_preserves_inventory_outcomes_grouping_and_initial_dispatch(suite):
    # Given an existing collection hook that moves the single-case heavy file last.
    standard = ["-n", "2", "--dist=loadfile"]
    baseline, control_receipt = execute(suite, "control", standard)
    # When opting into exact first-file priority with stock loadfile scheduling.
    promoted, priority_receipt = execute(
        suite, "priority", [*standard, "--no-loadscope-reorder", "--ci-first-file=test_heavy.py"]
    )
    # Then inventory, failures/skips and their exit status remain unchanged.
    assert baseline.returncode == promoted.returncode == 1
    assert outcomes(control_receipt) == outcomes(priority_receipt)
    assert len(outcomes(priority_receipt)) == 12
    assert json.loads((control_receipt / "workers.json").read_bytes())["ordering"] == "case-count"
    assert (
        json.loads((priority_receipt / "workers.json").read_bytes())["ordering"]
        == "first-file:test_heavy.py"
    )
    collected = [
        json.loads(path.read_text(encoding="utf-8"))
        for path in priority_receipt.glob("*-collection.json")
    ]
    assert len(collected) == 2 and collected[0] == collected[1]
    files = list(dict.fromkeys(node.split("::", 1)[0] for node in collected[0]))
    assert files == ["test_heavy.py", "test_c.py", "test_a.py", "test_b.py", "test_outcomes.py"]
    control_collection = json.loads(
        next(control_receipt.glob("*-collection.json")).read_text(encoding="utf-8")
    )
    for name in files:
        assert [node for node in collected[0] if node.split("::", 1)[0] == name] == [
            node for node in control_collection if node.split("::", 1)[0] == name
        ]
    control_first = {
        json.loads(path.read_text(encoding="utf-8").splitlines()[0]).split("::", 1)[0]
        for path in control_receipt.glob("*-protocol.jsonl")
    }
    assert control_first == {"test_c.py", "test_a.py"}
    workers_by_file = {}
    for path in priority_receipt.glob("*-protocol.jsonl"):
        protocols = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
        for name in {node.split("::", 1)[0] for node in protocols}:
            workers_by_file.setdefault(name, []).append(path.name)
            assert [node for node in protocols if node.split("::", 1)[0] == name] == [
                node for node in collected[0] if node.split("::", 1)[0] == name
            ]
        if any(node.startswith("test_heavy.py::") for node in protocols):
            assert protocols[0] == "test_heavy.py::test_owned_heavy"
    assert set(workers_by_file) == set(files)
    assert all(len(workers) == 1 for workers in workers_by_file.values())
    fixtures = [
        json.loads(line)
        for path in priority_receipt.glob("*-fixtures.jsonl")
        for line in path.read_text(encoding="utf-8").splitlines()
    ]
    for name in files:
        assert fixtures.count(["setup", name]) == fixtures.count(["teardown", name]) == 1


@pytest.mark.parametrize(
    "options",
    [
        ["-n", "0", "--dist=loadfile", "--no-loadscope-reorder"],
        ["-n", "2", "--dist=load", "--no-loadscope-reorder"],
        ["-n", "2", "--dist=loadfile"],
    ],
)
def test_invalid_configuration_reports_explicit_usage_error(suite, options):
    # Given an explicit priority request incompatible with active loadfile ordering.
    # When invoking the real pytest CLI.
    result, _ = execute(suite, "invalid", [*options, "--ci-first-file=test_heavy.py"])
    # Then no silent ignored priority is permitted.
    assert result.returncode == 4
    assert "requires active workers, --dist=loadfile" in result.stderr


@pytest.mark.parametrize("selected", ["missing.py", "nested/test_heavy.py"])
def test_missing_exact_file_reports_failure_instead_of_silent_priority(suite, selected):
    # Given a requested file absent from the selected nodeid paths.
    # When running a real two-worker collection.
    result, _ = execute(
        suite,
        "missing",
        ["-n", "2", "--dist=loadfile", "--no-loadscope-reorder", f"--ci-first-file={selected}"],
    )
    # Then the configuration error is visible and no misleading successful run is returned.
    assert result.returncode == 4
    assert "does not match a selected collected file" in result.stdout + result.stderr
