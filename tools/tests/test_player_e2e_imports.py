"""Reject false concurrent-import evidence and prove owned reader cleanup."""

from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import sys
from threading import Event
import time

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from player_e2e_artifacts import read_json, write_json
from player_e2e_import_evidence import check_import_case
from player_e2e_imports import FIXTURE, ImportEditor, ImportE2EError, execute_with_imports
import player_e2e_imports
from player_e2e_process import Invocation, execute


@pytest.fixture
def imported(tmp_path: Path) -> Path:
    directory = tmp_path / "soak-import-overlap"
    reader = directory / "editor-import"
    native = directory / "cli-output" / ("a" * 32)
    reader.mkdir(parents=True)
    native.mkdir(parents=True)
    nonce = "d" * 32
    summary = {"job_id": "a" * 32, "journal_rows": 50, "status": "succeeded"}
    write_json(reader / "config.json", {"schema_version": 1, "nonce": nonce})
    write_json(
        reader / "ready.json", {"nonce": nonce, "editor_process_id": 100, "ready_unix_ms": 20}
    )
    write_json(reader / "finished.json", {"nonce": nonce, "finished_unix_ms": 950})
    write_json(
        reader / "launch.json",
        {
            "process_id": 100,
            "started_unix_ms": 10,
            "finished_unix_ms": 1000,
            "process_ended": True,
            "actual_exit_code": 0,
            "forced_termination": False,
            "log_truncated": False,
            "progress_read_errors": [],
        },
    )
    write_json(directory / "launch.json", {"started_unix_ms": 30, "finished_unix_ms": 940})
    write_json(
        native / "process.json",
        {"process_id": 101, "started_unix_ms": 50, "finished_unix_ms": 900},
    )
    write_json(native / "soak.json", summary)
    write_json(
        reader / "receipt.json",
        {
            "schema_version": 1,
            "nonce": nonce,
            "case": directory.name,
            "editor_process_id": 100,
            "completed_unix_ms": 960,
            "read_attempts": 100,
            "successful_imports": 99,
            "background_reads": True,
            "main_thread_delivery": True,
            "worker_disposed": True,
            "pump_released": True,
            "final_summary": summary,
            "final_complete": True,
            "observations": [
                {
                    "process_id": 101,
                    "job_id": "a" * 32,
                    "rows": count,
                    "started_unix_ms": 100 + count * 10,
                    "finished_unix_ms": 101 + count * 10,
                }
                for count in (1, 2, 3)
            ],
        },
    )
    (reader / "results.xml").write_text(
        f'<test-run result="Passed"><test-suite><test-case fullname="{FIXTURE}" result="Passed"/>'
        "</test-suite></test-run>",
        encoding="utf-8",
    )
    return directory


def test_actual_overlap_with_matching_final_evidence_is_accepted(imported: Path) -> None:
    check_import_case(imported)


@pytest.mark.parametrize(
    ("filename", "key", "value"),
    [
        ("ready.json", "nonce", "e" * 32),
        ("ready.json", "ready_unix_ms", 950),
        ("launch.json", "actual_exit_code", 1),
        ("launch.json", "process_ended", False),
        ("launch.json", "forced_termination", True),
        ("launch.json", "process_id", 101),
        ("receipt.json", "background_reads", False),
        ("receipt.json", "main_thread_delivery", False),
        ("receipt.json", "worker_disposed", False),
        ("receipt.json", "pump_released", False),
        ("receipt.json", "successful_imports", 1),
        ("receipt.json", "final_complete", False),
        ("receipt.json", "final_summary", {"status": "succeeded"}),
        ("receipt.json", "observations", []),
    ],
)
def test_rejects_stale_or_incomplete_reader(
    imported: Path, filename: str, key: str, value: str | int | bool | dict[str, str] | list[str]
) -> None:
    path = imported / "editor-import" / filename
    data = read_json(path)
    data[key] = value
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError):
        check_import_case(imported)


@pytest.mark.parametrize("mutation", ["post-exit", "wrong-pid", "same-prefix", "wrong-job"])
def test_rejects_unattributed_or_post_exit_only_reads(imported: Path, mutation: str) -> None:
    path = imported / "editor-import/receipt.json"
    data = read_json(path)
    for observed in data["observations"]:
        if mutation == "post-exit":
            observed["started_unix_ms"] = 951
            observed["finished_unix_ms"] = 952
        elif mutation == "wrong-pid":
            observed["process_id"] = 999
        elif mutation == "same-prefix":
            observed["rows"] = 1
        else:
            observed["job_id"] = "b" * 32
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError):
        check_import_case(imported)


@pytest.mark.parametrize("result", ["Failed", "Skipped", "Inconclusive"])
def test_rejects_reader_without_an_actual_pass(imported: Path, result: str) -> None:
    xml = imported / "editor-import/results.xml"
    xml.write_text(xml.read_text(encoding="utf-8").replace("Passed", result), encoding="utf-8")
    with pytest.raises(ValueError, match="NUnit"):
        check_import_case(imported)


def test_external_stop_reaps_the_actual_owned_process(tmp_path: Path) -> None:
    stop = Event()
    witness = tmp_path / "alive"
    script = "from pathlib import Path; import sys,time; Path(sys.argv[1]).touch(); time.sleep(60)"
    invocation = Invocation(
        [sys.executable, "-c", script, str(witness)], tmp_path, stop_requested=stop
    )
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(execute, invocation)
        try:
            deadline = time.monotonic() + 10
            while not witness.exists() and not future.done():
                assert time.monotonic() < deadline
                time.sleep(0.01)
        finally:
            stop.set()
        result = future.result(timeout=20)
    assert result["process_ended"] and result["forced_termination"]
    assert result["actual_exit_code"] != 0
    # Exclusive reopen also detects an unreleased output handle on Windows.
    (tmp_path / "process.log").unlink()


def test_cli_failure_always_stops_the_reader(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    released = Event()
    monkeypatch.setattr(ImportEditor, "validate", lambda self: None)
    invocation = Invocation(["owned-cli"], tmp_path)

    def fake_execute(current: Invocation) -> dict[str, int]:
        if current is invocation:
            raise ImportE2EError("injected CLI failure")
        nonce = read_json(current.directory / "config.json")["nonce"]
        write_json(current.directory / "ready.json", {"nonce": nonce})
        try:
            assert current.stop_requested.wait(5)
            return {"actual_exit_code": 1}
        finally:
            released.set()

    monkeypatch.setattr(player_e2e_imports, "execute", fake_execute)
    with pytest.raises(RuntimeError, match="injected CLI failure"):
        execute_with_imports(invocation, ImportEditor(Path(sys.executable), tmp_path))
    assert released.is_set()
