"""Bounded payload, progress ownership and finite session regressions."""

from dataclasses import asdict
from hashlib import sha256
import json
import os
from pathlib import Path
from xml.etree import ElementTree

from cli.main import cli
from cli.utils.play_scenario_player import (
    PlayerFinalReport,
    PlayerLaunchError,
    PlayerRunOptions,
    run_player,
)
from cli.utils.play_scenario_player_payload import PayloadFile, PlayerPayloadError
from cli.utils import play_scenario_player_payload as payload_module
from cli.utils import play_scenario_player_session as session_module
from cli.utils.play_scenario_player_progress import (
    ProgressIdentity,
    ProgressObserver,
    open_progress_snapshot,
    read_progress_bytes,
)
from cli.utils.play_scenario_player_session import PlayerSessionOptions, run_player_session
from click.testing import CliRunner
from pydantic import ValidationError
import pytest

from .test_play_scenario_player import bundle, child


@pytest.fixture
def verified_bundle(bundle):
    build, output, manifest = bundle
    executable = build / manifest["executable"]
    inventory = [
        {
            "path": executable.name,
            "size_bytes": executable.stat().st_size,
            "sha256": sha256(executable.read_bytes()).hexdigest(),
        }
    ]
    encoded = json.dumps(inventory, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    manifest.update(
        schema_version=2,
        build_id="a" * 32,
        build_source_revision="build-label",
        payload_inventory=inventory,
        payload_inventory_json=encoded,
        payload_hash=sha256(encoded.encode()).hexdigest(),
    )
    (build / "scenario-bundle.json").write_text(json.dumps(manifest))
    return build, output, manifest


def test_verified_player_preserves_payload_and_process_proof(verified_bundle, child):
    build, output, manifest = verified_bundle
    outcome = run_player(
        PlayerRunOptions(
            build, output, expected_build_revision="build-label", source_revision="caller-label"
        )
    )
    assert outcome.exit_code == 0
    assert outcome.report["reproduction"]["payload_hash"] == manifest["payload_hash"]
    assert outcome.report["reproduction"]["source_revision"] == "caller-label"
    proof = json.loads((outcome.directory / "process.json").read_bytes())
    assert proof["process_id"] == child[1][0].pid
    assert proof["process_ended"] is True and proof["actual_exit_code"] == 0
    assert proof["forced_termination"] is False
    assert proof["progress_observations"][0]["main_loop_sequence"] == 1
    assert len((outcome.directory / "process.json").read_bytes()) <= 65536


@pytest.mark.parametrize(
    "change", ["extra", "missing", "changed", "revision", "identity", "canonical"]
)
def test_payload_failures_prevent_child_admission(verified_bundle, child, change):
    build, output, manifest = verified_bundle
    kwargs = {}
    if change == "extra":
        (build / "extra.bin").write_bytes(b"extra")
    elif change == "missing":
        (build / manifest["executable"]).unlink()
    elif change == "changed":
        (build / manifest["executable"]).write_bytes(b"tampered")
    elif change == "revision":
        kwargs["expected_build_revision"] = "other-label"
    elif change == "identity":
        kwargs["expected_build_id"] = "b" * 32
    else:
        manifest["payload_inventory_json"] += " "
        manifest["payload_hash"] = sha256(manifest["payload_inventory_json"].encode()).hexdigest()
        (build / "scenario-bundle.json").write_text(json.dumps(manifest))
    with pytest.raises((PlayerLaunchError, PlayerPayloadError, ValidationError, OSError)):
        run_player(PlayerRunOptions(build, output, **kwargs))
    assert child[1] == []


@pytest.mark.parametrize(
    "value",
    [
        "../x",
        "x\\y",
        "con.txt",
        "foo.",
        "foo ",
        "a:x",
        "a\u007fb",
        ".env-secret",
        "Assets/Resources/GameData/x",
    ],
)
def test_inventory_rejects_path_aliases_before_reads(value):
    with pytest.raises((PlayerPayloadError, ValidationError)):
        PayloadFile(path=value, size_bytes=0, sha256="0" * 64)


def test_ordinary_gamedata_directory_is_allowed():
    assert PayloadFile(path="StreamingAssets/GameData/value.bin", size_bytes=0, sha256="0" * 64)


def test_compare_is_explicit_batches_and_eviction_is_bounded(verified_bundle, child):
    build, output, _manifest = verified_bundle
    summary, directory, code = run_player_session(
        PlayerSessionOptions(
            build_directory=build,
            output_directory=output,
            mode="compare",
            iterations=2,
            batch_size=2,
            retain_reports=2,
            interval_seconds=0.0,
        )
    )
    assert code == 0 and summary["status"] == "succeeded"
    assert summary["children_started"] == summary["children_finalized"] == 3
    assert summary["completed_iterations"] == {"shared-batches": 2, "fresh-process": 2}
    assert summary["iterations_per_process"] == {"shared-batches": 2, "fresh-process": 1}
    assert all(summary["arms_complete"].values())
    assert len(list((directory / "runs").iterdir())) == 2
    assert len(summary["retained_job_ids"]) == 2
    assert len(child[1]) == 3 and len({process.pid for process in child[1]}) == 3
    entries = [
        json.loads(line) for line in (directory / "outcomes.jsonl").read_bytes().splitlines()
    ]
    assert [entry["repeat_count"] for entry in entries] == [2, 1, 1]
    assert all(entry["native_report_available"] for entry in entries)
    evicted = entries[0]
    assert not (directory / evicted["artifact_directory"]).exists()
    for entry, process in zip(entries, child[1], strict=True):
        assert entry["process_id"] == process.pid
        assert entry["actual_exit_code"] == 0 and entry["process_ended"] is True
        assert entry["forced_termination"] is False
        assert entry["process_started_unix_ms"] <= entry["process_finished_unix_ms"]
        assert len(entry["request_sha256"]) == 64
        assert entry["definition_hash"] == _manifest["definition_hash"]
        assert entry["payload_hash"] == _manifest["payload_hash"]
        assert len(json.dumps(entry).encode()) <= session_module.OUTCOME_LIMIT
    retained = entries[-1]
    proof = json.loads((directory / retained["artifact_directory"] / "process.json").read_bytes())
    assert retained["process_started_unix_ms"] == proof["started_unix_ms"]
    assert retained["process_finished_unix_ms"] == proof["finished_unix_ms"]
    assert retained["request_sha256"] == proof["request_sha256"]

    xml = ElementTree.parse(directory / "junit.xml").getroot()
    assert xml.attrib["tests"] == "3" and xml.attrib["errors"] == "0"


@pytest.mark.parametrize("policy,count", [("stop", 1), ("continue", 2)])
def test_first_failure_is_pinned_without_retry_to_green(verified_bundle, child, policy, count):
    build, output, _manifest = verified_bundle
    child[0][0] = "failed"
    summary, directory, code = run_player_session(
        PlayerSessionOptions(
            build_directory=build,
            output_directory=output,
            mode="fresh-process",
            iterations=2,
            retain_reports=1,
            failure_policy=policy,
            interval_seconds=0.0,
        )
    )
    assert code == 1 and summary["status"] == "failed"
    assert summary["children_started"] == summary["children_finalized"] == count
    failure = dict(summary["first_failure"] or {})
    assert isinstance(failure, dict)
    assert failure["job_id"] == summary["retained_job_ids"][0]
    assert len(list((directory / "runs").iterdir())) == 1
    assert len(child[1]) == count


def test_crash_has_exit_evidence_and_incomplete_arm(verified_bundle, child):
    build, output, _manifest = verified_bundle
    child[0][0] = "crash"
    summary, directory, code = run_player_session(
        PlayerSessionOptions(
            build_directory=build,
            output_directory=output,
            mode="fresh-process",
            iterations=2,
            interval_seconds=0.0,
        )
    )
    assert code == 1 and summary["status"] == "failed"
    assert summary["children_finalized"] == 1 and not summary["arms_complete"]["fresh-process"]
    failure = dict(summary["first_failure"] or {})
    assert isinstance(failure, dict)
    proof = json.loads((directory / failure["artifact_directory"] / "process.json").read_bytes())
    assert proof["actual_exit_code"] == 7 and proof["process_ended"]
    assert ElementTree.parse(directory / "junit.xml").getroot().attrib["errors"] == "2"


@pytest.mark.parametrize(
    "key,value",
    [
        ("iterations", 1001),
        ("iterations", True),
        ("retain_reports", 0),
        ("batch_size", 1),
        ("interval_seconds", float("nan")),
        ("max_runtime_seconds", 86401),
    ],
)
def test_session_bounds_reject_before_launch(bundle, child, key, value):
    build, output, _manifest = bundle
    with pytest.raises(ValidationError):
        PlayerSessionOptions(build_directory=build, output_directory=output, **{key: value})
    assert child[1] == []


def test_session_cli_wires_verified_contract(verified_bundle, child):
    build, output, _manifest = verified_bundle
    result = CliRunner().invoke(
        cli,
        [
            "--format",
            "json",
            "play-scenario",
            "player-session",
            str(build),
            "--output-dir",
            str(output),
            "--mode",
            "compare",
            "--iterations",
            "2",
            "--batch-size",
            "2",
            "--interval-seconds",
            "0",
            "--expected-build-revision",
            "build-label",
        ],
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["data"]["children_started"] == 3


def test_snapshot_reader_bounds_and_ownership(tmp_path):
    path = tmp_path / "progress.json"
    path.write_bytes(b"snapshot")
    with open_progress_snapshot(path) as stream:
        assert stream.read() == b"snapshot"
    assert stream.closed
    path.unlink()
    path.write_bytes(b"x" * 16385)
    with pytest.raises(ValueError):
        read_progress_bytes(path)


@pytest.mark.skipif(os.name != "nt", reason="Windows replacement sharing contract")
def test_windows_reader_allows_atomic_replacement_while_open(tmp_path):
    import ctypes
    from ctypes import wintypes

    original = tmp_path / "progress.json"
    replacement = tmp_path / "replacement.json"
    original.write_bytes(b"original")
    replacement.write_bytes(b"replacement")
    replace = ctypes.WinDLL("kernel32", use_last_error=True).ReplaceFileW
    replace.argtypes = [
        wintypes.LPCWSTR,
        wintypes.LPCWSTR,
        wintypes.LPCWSTR,
        wintypes.DWORD,
        ctypes.c_void_p,
        ctypes.c_void_p,
    ]
    replace.restype = wintypes.BOOL
    with open_progress_snapshot(original) as reader:
        assert replace(str(original), str(replacement), None, 0, None, None), (
            ctypes.get_last_error()
        )
        assert reader.read() == b"original"
    assert read_progress_bytes(original) == b"replacement"


def test_observer_deduplicates_and_bounds_update_evidence(tmp_path):
    identity = ProgressIdentity("a" * 32, "b" * 64, "c" * 32, "d" * 64, "label", 123)
    observer = ProgressObserver(identity)
    path = tmp_path / "progress.json"
    for sequence in range(140):
        receipt = {
            "schema_version": 1,
            **asdict(identity),
            "sequence": sequence,
            "main_loop_sequence": sequence,
            "heartbeat_unix_ms": sequence,
            "elapsed_ms": sequence,
            "phase": "running",
            "iteration": 1,
            "stage": "steps",
            "step_index": 0,
            "status": "running",
        }
        path.write_text(json.dumps(receipt))
        observer.observe(path)
        observer.observe(path)
    assert len(observer.observations) == 128
    assert observer.observations[0]["sequence"] == 12
    assert observer.heartbeat_observed and observer.first_error is None


def test_directory_discovery_is_bounded_before_next_admission(verified_bundle, child, monkeypatch):
    build, output, _manifest = verified_bundle
    for index in range(4):
        (build / f"empty-{index}").mkdir()
    monkeypatch.setattr(payload_module, "PAYLOAD_DIRECTORY_LIMIT", 3)
    with pytest.raises(PlayerPayloadError, match="too many directories"):
        run_player(PlayerRunOptions(build, output))
    assert child[1] == []


def test_session_export_failure_preserves_native_bytes_and_first_failure(
    verified_bundle, child, monkeypatch
):
    build, output, _manifest = verified_bundle

    def fail_export(*_arguments):
        raise OSError("export fixture")

    monkeypatch.setattr(session_module, "write_player_artifacts", fail_export)
    summary, directory, code = run_player_session(
        PlayerSessionOptions(
            build_directory=build,
            output_directory=output,
            mode="fresh-process",
            iterations=2,
            interval_seconds=0.0,
        )
    )
    assert code == 1 and summary["children_started"] == 1
    failure = dict(summary["first_failure"] or {})
    assert isinstance(failure, dict)
    assert failure["native_report_available"] and failure["client_error"]
    native = directory / failure["artifact_directory"] / "run.json"
    assert sha256(native.read_bytes()).hexdigest() == failure["native_report_sha256"]


def test_player_junit_preserves_build_provenance(verified_bundle, child):
    from cli.utils.play_scenario_reports import write_player_artifacts

    build, output, manifest = verified_bundle
    outcome = run_player(PlayerRunOptions(build, output))
    original = (outcome.directory / "run.json").read_bytes()
    write_player_artifacts(outcome.report, outcome.directory)
    assert (outcome.directory / "run.json").read_bytes() == original
    values = {
        prop.attrib["name"]: prop.attrib["value"]
        for prop in ElementTree.parse(outcome.directory / "junit.xml").iter("property")
    }
    assert manifest["payload_hash"] in values.values()
    assert "launcher_admission" in values.values()


def test_session_cancellation_stops_admission_and_waits_for_owned_exit(
    verified_bundle, child, monkeypatch
):
    from cli.utils import play_scenario_player as player_module

    build, output, _manifest = verified_bundle
    child[0][0] = "cancel"
    real_sleep = player_module.time.sleep
    interrupted = []

    def interrupt_once(seconds):
        if not interrupted:
            interrupted.append(True)
            raise KeyboardInterrupt
        real_sleep(seconds)

    monkeypatch.setattr(player_module.time, "sleep", interrupt_once)
    summary, directory, code = run_player_session(
        PlayerSessionOptions(
            build_directory=build,
            output_directory=output,
            mode="fresh-process",
            iterations=2,
            cleanup_wait_seconds=1,
            interval_seconds=0.0,
        )
    )
    assert code == 130 and summary["status"] == "cancelled"
    assert summary["children_started"] == summary["children_finalized"] == 1
    assert len(child[1]) == 1 and child[1][0].poll() == 1
    failure = dict(summary["first_failure"] or {})
    assert (directory / failure["artifact_directory"] / "cancel").read_text() == "cancel\n"


def test_session_deadline_cancels_owned_child_and_marks_arm_incomplete(verified_bundle, child):
    build, output, _manifest = verified_bundle
    child[0][0] = "cancel"
    summary, _directory, code = run_player_session(
        PlayerSessionOptions(
            build_directory=build,
            output_directory=output,
            mode="fresh-process",
            iterations=2,
            max_runtime_seconds=2,
            cleanup_wait_seconds=1,
            interval_seconds=0.0,
        )
    )
    assert code == 1 and summary["status"] == "timed_out"
    assert summary["children_started"] == summary["children_finalized"] == 1
    assert summary["arms_complete"]["fresh-process"] is False


def test_continue_crash_advances_planned_batch_without_retry(verified_bundle, child):
    build, output, _manifest = verified_bundle
    child[0][0] = "crash"
    summary, _directory, code = run_player_session(
        PlayerSessionOptions(
            build_directory=build,
            output_directory=output,
            mode="fresh-process",
            iterations=2,
            failure_policy="continue",
            retain_reports=1,
            interval_seconds=0.0,
        )
    )
    assert code == 1 and summary["children_started"] == summary["children_finalized"] == 2
    assert summary["scheduled_iterations"]["fresh-process"] == 2
    assert summary["completed_iterations"]["fresh-process"] == 0
    assert dict(summary["first_failure"] or {})["job_id"] == summary["retained_job_ids"][0]


@pytest.mark.skipif(os.name != "nt", reason="Windows replacement retry contract")
@pytest.mark.parametrize("error,retry", [(2, True), (32, True), (33, True), (5, False)])
def test_snapshot_reader_retries_only_bounded_replacement_errors(
    tmp_path, monkeypatch, error, retry
):
    import ctypes
    from contextlib import contextmanager
    from cli.utils import play_scenario_player_progress as progress_module

    path = tmp_path / "progress.json"
    path.write_bytes(b"receipt")
    attempts = []

    @contextmanager
    def unavailable(_path):
        attempts.append(True)
        if attempts:
            raise ctypes.WinError(error)
        yield None  # The context-manager exception occurs before acquisition.

    sleeps = []
    monkeypatch.setattr(progress_module.time, "monotonic", lambda: 0.0)
    monkeypatch.setattr(progress_module.time, "sleep", sleeps.append)
    monkeypatch.setattr(progress_module, "open_progress_snapshot", unavailable)
    with pytest.raises(OSError):
        read_progress_bytes(path)
    assert len(attempts) == (3 if retry else 1)
    assert sleeps == ([0.01, 0.01] if retry else [])


def test_missing_progress_is_unavailable_without_claiming_an_integrity_failure(tmp_path):
    observer = ProgressObserver(ProgressIdentity("a" * 32, "b" * 64, None, None, None, 123))
    observer.observe(tmp_path / "missing.json")
    assert observer.first_error is None
    assert observer.unavailable_observations == 1 and not observer.heartbeat_observed


def test_progress_malformed_receipt_and_read_failure_are_not_retried(tmp_path, monkeypatch):
    import ctypes
    from contextlib import contextmanager
    from cli.utils import play_scenario_player_progress as progress_module

    path = tmp_path / "progress.json"
    path.write_bytes(b"{malformed")
    observer = ProgressObserver(ProgressIdentity("a" * 32, "b" * 64, None, None, None, 123))
    observer.observe(path)
    assert observer.first_error == "player_progress_invalid"
    acquisitions = []

    class BrokenReader:
        def read(self, _size):
            raise ctypes.WinError(32) if os.name == "nt" else OSError("read failed")

    @contextmanager
    def acquired(_path):
        acquisitions.append(True)
        yield BrokenReader()

    monkeypatch.setattr(progress_module, "open_progress_snapshot", acquired)
    with pytest.raises(OSError):
        read_progress_bytes(path)
    assert len(acquisitions) == 1


@pytest.mark.skipif(os.name != "nt", reason="Windows replacement retry deadline")
@pytest.mark.parametrize(
    "elapsed,sleeps_expected",
    [([0.0, 0.051], []), ([0.0, 0.045], []), ([0.0, 0.020, 0.051], [0.01])],
)
def test_snapshot_retry_stops_at_elapsed_deadline(tmp_path, monkeypatch, elapsed, sleeps_expected):
    import ctypes
    from contextlib import contextmanager
    from cli.utils import play_scenario_player_progress as progress_module

    clock = iter(elapsed)
    attempts = []
    sleeps = []

    @contextmanager
    def unavailable(_path):
        attempts.append(True)
        if attempts:
            raise ctypes.WinError(32)
        yield None

    monkeypatch.setattr(progress_module, "open_progress_snapshot", unavailable)
    monkeypatch.setattr(progress_module.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(progress_module.time, "sleep", sleeps.append)
    with pytest.raises(OSError):
        read_progress_bytes(tmp_path / "progress.json")
    assert len(attempts) == 1 and sleeps == sleeps_expected


@pytest.mark.parametrize(
    "key,value",
    [
        ("process_id", True),
        ("process_id", 2**32),
        ("actual_exit_code", float("inf")),
        ("actual_exit_code", 2**32),
        ("process_ended", 1),
        ("forced_termination", 1),
        ("started_unix_ms", -1),
        ("finished_unix_ms", 2**63),
        ("request_sha256", "bad"),
        ("job_id", "f" * 32),
    ],
)
def test_session_rejects_malformed_process_proof_without_claiming_finalization(
    verified_bundle, child, monkeypatch, key, value
):
    build, output, _manifest = verified_bundle
    read = session_module._read_small

    def changed_receipt(path):
        evidence = read(path)
        if path.name == "process.json" and evidence:
            return {**evidence, key: value}
        return evidence

    monkeypatch.setattr(session_module, "_read_small", changed_receipt)
    summary, directory, code = run_player_session(
        PlayerSessionOptions(
            build_directory=build,
            output_directory=output,
            mode="fresh-process",
            iterations=2,
            interval_seconds=0.0,
        )
    )
    assert code == 1 and len(child[1]) == 1
    assert summary["children_started"] == summary["children_finalized"] == 0
    row = json.loads((directory / "outcomes.jsonl").read_bytes())
    assert row["process_id"] is None and row["process_ended"] is False
    assert row["client_error"] == "session_process_evidence_invalid"
    assert row["native_report_available"] is True


def test_process_receipt_preserves_windows_crash_exit_and_rejects_reversed_interval():
    evidence = {
        "schema_version": 1,
        "job_id": "a" * 32,
        "process_id": 123,
        "actual_exit_code": 0xC0000409,
        "process_ended": True,
        "forced_termination": False,
        "started_unix_ms": 100,
        "finished_unix_ms": 101,
        "request_sha256": "b" * 64,
    }
    receipt = session_module.PlayerProcessReceipt.model_validate(evidence)
    assert receipt.outcome_fields()["actual_exit_code"] == 0xC0000409
    with pytest.raises(ValidationError):
        session_module.PlayerProcessReceipt.model_validate({**evidence, "finished_unix_ms": 99})
    with pytest.raises(ValidationError):
        session_module.PlayerProcessReceipt.model_validate({**evidence, "process_ended": False})


@pytest.mark.parametrize("length", [513, 2048])
def test_native_progress_failure_retains_the_native_error_bound(bundle, child, monkeypatch, length):
    from cli.utils import play_scenario_player as player_module
    from cli.utils.play_scenario_reports import write_player_artifacts

    build, output, _manifest = bundle
    child[0][0] = "failed"
    read = player_module._read_json
    message = "Progress receipt I/O failed: " + "x" * (
        length - len("Progress receipt I/O failed: ")
    )
    native_bytes = []

    def native_shaped_failure(path, limit):
        report = read(path, limit)
        if path.name == "run.json":
            report["progress_error"] = message
            encoded = json.dumps(report).encode("utf-8")
            path.write_bytes(encoded)
            native_bytes.append(encoded)
        return report

    monkeypatch.setattr(player_module, "_read_json", native_shaped_failure)
    outcome = run_player(PlayerRunOptions(build, output))
    assert outcome.exit_code == 1 and outcome.report["status"] == "failed"
    assert outcome.client_error == outcome.report["progress_error"] == message
    write_player_artifacts(outcome.report, outcome.directory, outcome.client_error)
    assert (outcome.directory / "run.json").read_bytes() == native_bytes[0]
    assert (outcome.directory / "junit.xml").is_file()


def test_progress_error_exceeding_native_bound_is_rejected():
    report = {
        "job_id": "a" * 32,
        "scenario": {
            "name": "bound",
            "steps": [
                {"name": "Load", "action": "load_scene", "scene": "Assets/Scenes/Fixture.unity"}
            ],
        },
        "status": "failed",
        "execution_environment": "player",
        "finalization_state": "completed",
        "exit_code": 1,
        "repeat_count": 1,
        "timeout_seconds": 300,
        "reproduction": {},
        "runner_resources_released": True,
        "started_unix_ms": 0,
        "finished_unix_ms": 1,
        "progress_error": "x" * 2049,
    }
    with pytest.raises(ValidationError):
        PlayerFinalReport.model_validate(report)
