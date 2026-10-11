"""Boundary coverage for actual offline CLI process evidence and immutable native inputs."""

from hashlib import sha256
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "Server/src"))

from player_e2e_budget import check_budget_checks, run_budget_checks, BudgetEvidenceError
from player_e2e_artifacts import digest
from player_e2e_profiles import ExecutionWindow
from Server.tests.play_scenario_soak_budget_fixtures import _known_evidence


def test_missing_budget_phase_cannot_pass_final_verification(tmp_path: Path) -> None:
    # Given: an evidence root whose required budget phase was skipped.
    # When: independent final verification requires all three CLI checks.
    # Then: missing evidence cannot be accepted as success.
    with pytest.raises(ValueError):
        check_budget_checks(tmp_path, "a" * 32)


def _source(case: Path, values: list[int], pid: int) -> None:
    """Reuse admitted synthetic native-contract fixtures without a Unity/Player launch."""
    output = case / "cli-output"
    output.mkdir(parents=True)
    directory = _known_evidence(output / "source", values)
    rows = [json.loads(line) for line in (directory / "iterations.jsonl").read_text().splitlines()]
    for row in rows:
        row["process_id"] = pid
    journal = b"".join((json.dumps(row, separators=(",", ":")) + "\n").encode() for row in rows)
    (directory / "iterations.jsonl").write_bytes(journal)
    summary = json.loads((directory / "soak.json").read_text())
    summary.update(
        process_id=pid, journal_bytes=len(journal), journal_sha256=sha256(journal).hexdigest()
    )
    (directory / "soak.json").write_text(json.dumps(summary))
    report = json.loads((directory / "run.json").read_text())
    report["soak"] = summary
    (directory / "run.json").write_text(json.dumps(report))
    for filename in ("client-soak.json", "process.json"):
        raw = json.loads((directory / filename).read_text())
        raw["process_id"] = pid
        if filename == "client-soak.json":
            raw.update(journal_bytes=len(journal), journal_sha256=summary["journal_sha256"])
        (directory / filename).write_text(json.dumps(raw))
    job = json.loads((directory / "request.json").read_text())["job_id"]
    directory.rename(output / job)
    (case / "source-note.txt").write_text("Synthetic test fixture; no native Player process.\n")
    (case / "launch.json").write_text(
        json.dumps(
            {
                "command": ["synthetic-source-fixture"],
                "process_id": pid + 1,
                "started_unix_ms": 100,
                "finished_unix_ms": 5000,
                "actual_exit_code": 0,
                "process_ended": True,
                "forced_termination": False,
                "log_truncated": False,
                "progress_read_errors": [],
            }
        )
    )


@pytest.fixture(scope="module")
def completed(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("budget-cli")
    (root / "session-start.json").write_text(
        json.dumps({"session_id": "a" * 32, "started_unix_ms": 1})
    )
    _source(
        root / "cases/soak-budget-baseline",
        [10000000 + iteration * 1000 for iteration in range(30)],
        1234,
    )
    _source(
        root / "cases/soak-budget-growth",
        [10000000 + iteration * 1048576 for iteration in range(30)],
        5678,
    )
    run_budget_checks(root, "a" * 32)
    return root


def test_real_cli_processes_verify_all_three_budget_outcomes(completed: Path) -> None:
    # Given: actual saved synthetic evidence and three naturally completed real CLI processes.
    # When: the read-only verifier reopens every policy, receipt, output and input hash.
    check_budget_checks(completed, "a" * 32)
    # Then: actual exit/status distinctions survive independent verification.
    statuses = []
    exits = []
    for case in ("within-budget", "budget-exceeded", "insufficient-evidence"):
        directory = completed / "budget-checks" / case
        statuses.append(json.loads((directory / "process.log").read_text())["status"])
        exits.append(json.loads((directory / "launch.json").read_text())["actual_exit_code"])
    assert statuses == ["within_budget", "budget_exceeded", "not_comparable"]
    assert exits == [0, 1, 2]


@pytest.mark.parametrize(
    "damage",
    [
        "missing",
        "session",
        "pid",
        "forced",
        "exit",
        "command",
        "time",
        "policy",
        "output",
        "rehash-output",
        "rehash-policy",
        "input",
    ],
)
def test_checker_rejects_changed_or_stale_budget_evidence(completed: Path, damage: str) -> None:
    # Given: valid real CLI evidence whose selected boundary is independently corrupted.
    directory = completed / "budget-checks/budget-exceeded"
    paths = [
        directory / name for name in ("receipt.json", "launch.json", "policy.json", "process.log")
    ]
    input_path = completed / "cases/soak-budget-growth/source-note.txt"
    originals = {path: path.read_bytes() for path in [*paths, input_path]}
    receipt = json.loads(paths[0].read_text())
    launch = json.loads(paths[1].read_text())
    policy = json.loads(paths[2].read_text())
    output = json.loads(paths[3].read_text())
    try:
        if damage == "missing":
            paths[0].unlink()
        elif damage == "session":
            receipt["session_id"] = "b" * 32
        elif damage == "pid":
            receipt["process_id"] += 1
        elif damage in {"forced", "exit", "command", "time"}:
            launch.update(
                {
                    "forced": {"forced_termination": True},
                    "exit": {"actual_exit_code": 0},
                    "command": {"command": ["fabricated-command"]},
                    "time": {"started_unix_ms": 0},
                }[damage]
            )
            paths[1].write_text(json.dumps(launch))
            receipt["launch_sha256"] = digest(paths[1])
        elif damage in {"policy", "rehash-policy"}:
            policy["metrics"]["managed_bytes"]["max_net_change_increase"] = 1
            paths[2].write_text(json.dumps(policy))
            if damage == "rehash-policy":
                receipt["policy_sha256"] = digest(paths[2])
        elif damage in {"output", "rehash-output"}:
            output["metrics"][0]["net_change_delta"] += 1
            paths[3].write_text(json.dumps(output))
            if damage == "rehash-output":
                receipt["output_sha256"] = digest(paths[3])
        elif damage == "input":
            input_path.write_text("Original native input artifact changed.\n")
        if damage != "missing":
            paths[0].write_text(json.dumps(receipt))
        # When: final verification reopens the damaged boundary.
        # Then: even rehashed receipts cannot make inconsistent CLI/policy evidence pass.
        with pytest.raises(ValueError):
            check_budget_checks(completed, "a" * 32)
    finally:
        for path, payload in originals.items():
            path.write_bytes(payload)


def test_checker_is_read_only_when_all_budget_checks_are_valid(completed: Path) -> None:
    # Given: byte snapshots of every existing evidence file.
    before = {path: path.read_bytes() for path in completed.rglob("*") if path.is_file()}
    # When: the checker reads all required budget evidence.
    check_budget_checks(completed, "a" * 32)
    # Then: source reports and every check artifact remain byte-identical.
    assert before == {path: path.read_bytes() for path in completed.rglob("*") if path.is_file()}


def test_elapsed_profile_deadline_prevents_starting_budget_cli(completed: Path) -> None:
    # Given: a profile deadline that has already elapsed before a new CLI admission.
    # When: the runner is asked to begin its required budget phase.
    # Then: the deadline is rejected before another CLI process or check root is created.
    with pytest.raises(BudgetEvidenceError, match="deadline"):
        run_budget_checks(completed, "a" * 32, deadline=0)


@pytest.mark.parametrize("boundary", ["before", "after"])
def test_cli_evidence_outside_the_profile_window_is_rejected(
    completed: Path, boundary: str
) -> None:
    # Given: a valid actual CLI process whose lifetime is outside the required profile window.
    directory = completed / "budget-checks/within-budget"
    launch = json.loads((directory / "launch.json").read_text())
    receipt = json.loads((directory / "receipt.json").read_text())
    start = launch["started_unix_ms"] + 1 if boundary == "before" else 1
    finish = (
        receipt["completed_unix_ms"] - 1
        if boundary == "after"
        else receipt["completed_unix_ms"] + 60000
    )
    window = ExecutionWindow(start, finish)
    # When: independent verification receives the externally validated execution window.
    # Then: freshly hashed files cannot make an out-of-window process count as profile evidence.
    with pytest.raises(BudgetEvidenceError, match="window"):
        check_budget_checks(completed, "a" * 32, window)
