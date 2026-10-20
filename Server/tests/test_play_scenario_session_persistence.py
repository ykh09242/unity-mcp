"""Real owned-child regressions for session storage failure boundaries."""

import json
import os
from hashlib import sha256
from pathlib import Path
from xml.etree import ElementTree

from cli.main import cli
from cli.utils import play_scenario_player_session as session_module
from cli.utils.play_scenario_player_session import PlayerSessionOptions, run_player_session
from cli.utils.play_scenario_session_recovery import recover_player_session
from click.testing import CliRunner
import pytest

from .test_play_scenario_player_assurance import bundle, child, verified_bundle


class FaultStream:
    """Delegate real file operations while failing one selected operation once."""

    def __init__(self, stream, operation, triggered):
        self.stream = stream
        self.operation = operation
        self.triggered = triggered

    def __getattr__(self, name):
        return getattr(self.stream, name)

    def __enter__(self):
        return self

    def __exit__(self, *arguments):
        return self.stream.__exit__(*arguments)

    def write(self, encoded):
        if self.operation in ("write", "partial") and not self.triggered:
            self.triggered.append(True)
            if self.operation == "partial":
                return self.stream.write(encoded[: len(encoded) // 2])
            raise OSError("injected journal write failure")
        return self.stream.write(encoded)

    def flush(self):
        if self.operation == "flush" and not self.triggered:
            self.triggered.append(True)
            raise OSError("injected journal flush failure")
        return self.stream.flush()


@pytest.fixture
def storage_fault(monkeypatch):
    """Patch actual file operations, leaving session/process orchestration intact."""
    triggered = []

    def inject(stage):
        real_open, real_write, real_replace, real_fsync = (
            Path.open,
            Path.write_bytes,
            Path.replace,
            os.fsync,
        )
        checkpoint = []
        descriptors = set()

        def opened(path, mode="r", *arguments, **keywords):
            if stage == "diagnostics-read" and path.name == "diagnostics.json" and mode == "rb":
                triggered.append(True)
                raise OSError("injected diagnostics read failure")
            stream = real_open(path, mode, *arguments, **keywords)
            name, _, operation = stage.partition(":")
            if path.name == name and mode == "xb":
                descriptors.add(stream.fileno())
                return FaultStream(stream, operation, triggered)
            if (
                stage == "junit-write"
                and path.name in ("junit.xml", "junit.xml.tmp")
                and not any(parent.name == "runs" for parent in path.parents)
                and mode == "wb"
            ):
                return FaultStream(stream, "partial", triggered)
            return stream

        def written(path, encoded):
            if path.name == "session.json.tmp":
                checkpoint.append(json.loads(encoded))
                selected = (
                    stage == "initial-checkpoint"
                    or stage == "admission-checkpoint"
                    and checkpoint[-1]["active_child"]
                    or stage == "outcome-checkpoint"
                    and checkpoint[-1]["outcomes_recorded"]
                    or stage == "final-checkpoint"
                    and checkpoint[-1]["finished_unix_ms"]
                )
                if selected and not triggered:
                    triggered.append(True)
                    raise OSError("injected checkpoint write failure")
            if (
                stage == "initial-header"
                and path.name == "session-start.json.tmp"
                and not triggered
            ):
                triggered.append(True)
                raise OSError("injected immutable header write failure")
            return real_write(path, encoded)

        def replaced(path, target):
            if stage == "junit-replace" and path.name == "junit.xml.tmp" and not triggered:
                triggered.append(True)
                raise OSError("injected JUnit replace failure")
            if stage.endswith("-replace") and path.name == "session.json.tmp" and not triggered:
                summary = json.loads(path.read_bytes())
                selected = (
                    stage == "initial-checkpoint-replace"
                    or stage == "admission-checkpoint-replace"
                    and summary["active_child"]
                    or stage == "outcome-checkpoint-replace"
                    and summary["outcomes_recorded"]
                    or stage == "final-checkpoint-replace"
                    and summary["finished_unix_ms"]
                )
                if selected:
                    triggered.append(True)
                    raise OSError("injected checkpoint replace failure")
            if (
                stage == "initial-header-replace"
                and path.name == "session-start.json.tmp"
                and not triggered
            ):
                triggered.append(True)
                raise OSError("injected immutable header replace failure")
            return real_replace(path, target)

        def synced(descriptor):
            if stage.endswith(":fsync") and descriptor in descriptors and not triggered:
                triggered.append(True)
                raise OSError("injected journal fsync failure")
            return real_fsync(descriptor)

        monkeypatch.setattr(Path, "open", opened)
        monkeypatch.setattr(Path, "write_bytes", written)
        monkeypatch.setattr(Path, "replace", replaced)
        monkeypatch.setattr(os, "fsync", synced)
        return triggered

    return inject


@pytest.mark.parametrize(
    "stage,launched",
    [
        ("initial-checkpoint", 0),
        ("initial-checkpoint-replace", 0),
        ("initial-header", 0),
        ("initial-header-replace", 0),
        ("admissions.jsonl:write", 0),
        ("admissions.jsonl:partial", 0),
        ("admissions.jsonl:flush", 0),
        ("admissions.jsonl:fsync", 0),
        ("admission-checkpoint", 0),
        ("admission-checkpoint-replace", 0),
        ("diagnostics-read", 1),
        ("outcomes.jsonl:write", 1),
        ("outcomes.jsonl:partial", 1),
        ("outcomes.jsonl:flush", 1),
        ("outcomes.jsonl:fsync", 1),
        ("outcome-checkpoint", 1),
        ("outcome-checkpoint-replace", 1),
        ("final-checkpoint", 2),
        ("final-checkpoint-replace", 2),
        ("junit-write", 2),
        ("junit-replace", 2),
    ],
)
def test_session_finalizes_and_stops_admission_when_storage_fails(
    verified_bundle, child, storage_fault, stage, launched
):
    # Given a real owned child and one failure in an actual file operation.
    build, output, _manifest = verified_bundle
    triggered = storage_fault(stage)
    # When the real finite session runs with continuation enabled.
    summary, directory, code = run_player_session(
        PlayerSessionOptions(
            build_directory=build,
            output_directory=output,
            mode="fresh-process",
            iterations=2,
            failure_policy="continue",
            interval_seconds=0.0,
        )
    )
    # Then the returned receipt is final and no later owned child was admitted.
    assert triggered
    assert code == 1 and summary["status"] == "failed"
    assert summary["finished_unix_ms"] is not None
    assert len(child[1]) == launched
    assert all(process.poll() is not None for process in child[1])
    assert summary["children_started"] == summary["children_finalized"] == launched
    snapshot = json.loads((directory / "session.json").read_bytes())
    assert snapshot == summary
    if (directory / "junit.xml").exists():
        ElementTree.parse(directory / "junit.xml")
    if stage.startswith("outcomes.jsonl:"):
        assert summary["outcomes_recorded"] == 0
        assert summary["iteration_counts"]["fresh-process"]["executed"] == 0
        assert summary["iteration_counts"]["fresh-process"]["unknown"] == 1


def test_cli_returns_final_failure_when_checkpoint_fails(verified_bundle, child, storage_fault):
    # Given an admitted batch whose session checkpoint cannot be written.
    build, output, _manifest = verified_bundle
    storage_fault("outcome-checkpoint")
    # When the public Click command runs the real session.
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
            "fresh-process",
            "--iterations",
            "2",
            "--interval-seconds",
            "0",
        ],
    )
    # Then a nonzero CLI response still exposes a final truthful receipt.
    assert result.exit_code == 1
    summary = json.loads(result.output)["data"]
    assert summary["status"] == "failed" and summary["finished_unix_ms"] is not None
    assert len(child[1]) == 1 and child[1][0].poll() == 0


def test_first_native_failure_survives_outcome_write_failure(verified_bundle, child, storage_fault):
    # Given a genuine native failure followed by an outcome journal failure.
    build, output, _manifest = verified_bundle
    child[0][0] = "failed"
    storage_fault("outcomes.jsonl:write")
    # When the session attempts to persist that first failure.
    summary, directory, code = run_player_session(
        PlayerSessionOptions(
            build_directory=build,
            output_directory=output,
            mode="fresh-process",
            iterations=2,
            interval_seconds=0.0,
        )
    )
    # Then the original native failure and its owned exit evidence remain available.
    failure = dict(summary["first_failure"] or {})
    assert code == 1 and failure["status"] == "failed"
    assert failure["client_error"] is None and failure["native_report_available"] is True
    assert summary["outcomes_recorded"] == 0
    assert summary["outcome_counts"] == {"failures": 0, "errors": 0}
    assert (directory / failure["artifact_directory"] / "run.json").is_file()
    assert len(child[1]) == 1 and child[1][0].poll() == 1


@pytest.mark.parametrize("operation", ["partial", "flush", "fsync"])
def test_recovery_preserves_unknown_admission_after_failed_outcome_append(
    verified_bundle, child, storage_fault, operation
):
    # Given an admission whose real child ended but whose outcome was not durably appended.
    build, output, _manifest = verified_bundle
    storage_fault("outcomes.jsonl:" + operation)
    summary, directory, _code = run_player_session(
        PlayerSessionOptions(
            build_directory=build,
            output_directory=output,
            mode="fresh-process",
            iterations=2,
            interval_seconds=0.0,
        )
    )
    # When the actual recovery reader reconstructs the remaining durable journal prefix.
    recovered, recovered_directory, code = recover_player_session(
        directory, output.parent / "recovery"
    )
    # Then it retains uncertainty instead of fabricating the observed native execution.
    assert code == 1 and recovered["status"] == "interrupted"
    assert recovered["iteration_counts"] == summary["iteration_counts"]
    assert recovered["iteration_counts"]["fresh-process"]["unknown"] == 1
    ElementTree.parse(recovered_directory / "junit.xml")
    assert len(child[1]) == 1 and child[1][0].poll() == 0


def test_receipt_remains_final_when_all_summary_writes_fail(verified_bundle, child, monkeypatch):
    # Given storage that refuses every checkpoint, including finalization retries.
    build, output, _manifest = verified_bundle
    real_write = Path.write_bytes

    def unavailable(path, encoded):
        if path.name.endswith(".json.tmp"):
            raise OSError("injected unavailable checkpoint storage")
        return real_write(path, encoded)

    monkeypatch.setattr(Path, "write_bytes", unavailable)
    # When the actual session attempts admission and bounded finalization.
    summary, directory, code = run_player_session(
        PlayerSessionOptions(build_directory=build, output_directory=output, iterations=2)
    )
    # Then it honestly returns failure without claiming a persisted final checkpoint.
    assert code == 1 and summary["status"] == "failed"
    assert summary["finished_unix_ms"] is not None and summary["active_child"] is None
    assert summary["session_error"] == "session_persistence_failed: OSError"
    assert summary["finalization_error"] == "session_final_checkpoint_unavailable: OSError"
    assert not (directory / "session.json").exists() and child[1] == []


@pytest.mark.parametrize("native_mode", ["success", "failed", "export-error"])
def test_export_failure_stops_admission_under_continue_policy(
    verified_bundle, child, monkeypatch, native_mode
):
    # Given a genuine owned child whose artifact exporter raises an I/O failure.
    build, output, _manifest = verified_bundle
    child[0][0] = native_mode

    def fail_export(*_arguments):
        raise OSError("export fixture")

    monkeypatch.setattr(session_module, "write_player_artifacts", fail_export)
    # When the real session allows continuation of ordinary scenario failures.
    summary, directory, code = run_player_session(
        PlayerSessionOptions(
            build_directory=build,
            output_directory=output,
            mode="fresh-process",
            iterations=2,
            failure_policy="continue",
            interval_seconds=0.0,
        )
    )
    # Then storage failure stops admission while preserving the native first receipt.
    assert len(child[1]) == 1 and child[1][0].poll() is not None
    assert code == 1 and summary["status"] == "failed"
    assert summary["children_started"] == summary["children_finalized"] == 1
    assert summary["session_error"] == "session_child_artifact_failed"
    failure = dict(summary["first_failure"] or {})
    native = directory / failure["artifact_directory"] / "run.json"
    assert failure["native_report_available"] is True
    assert sha256(native.read_bytes()).hexdigest() == failure["native_report_sha256"]
    assert failure["status"] == json.loads(native.read_bytes())["status"]
    if native_mode == "export-error":
        assert failure["client_error"] == "player_native_report_export_failed"


def test_native_export_error_stops_admission_under_continue_policy(verified_bundle, child):
    # Given native evidence that its report export failed despite process completion.
    build, output, _manifest = verified_bundle
    child[0][0] = "export-error"
    # When continuation is enabled for scenario failures.
    summary, _directory, code = run_player_session(
        PlayerSessionOptions(
            build_directory=build,
            output_directory=output,
            mode="fresh-process",
            iterations=2,
            failure_policy="continue",
            interval_seconds=0.0,
        )
    )
    # Then native storage failure also prevents later owned launches.
    assert code == 1 and summary["status"] == "failed"
    assert len(child[1]) == 1 and child[1][0].poll() == 0
    assert summary["session_error"] == "session_child_artifact_failed"
    assert (
        dict(summary["first_failure"] or {})["client_error"] == "player_native_report_export_failed"
    )


@pytest.mark.parametrize("operation", ["open", "write"])
def test_wrapped_log_storage_failure_stops_admission_under_continue_policy(
    verified_bundle, child, monkeypatch, operation
):
    # Given an actual log file operation that fails after a genuine owned launch.
    build, output, _manifest = verified_bundle
    real_open = Path.open
    triggered = []

    def opened(path, mode="r", *arguments, **keywords):
        if path.name == "player.log" and mode == "xb":
            if operation == "open":
                triggered.append(True)
                raise OSError("injected owned log open failure")
            return FaultStream(real_open(path, mode, *arguments, **keywords), "write", triggered)
        return real_open(path, mode, *arguments, **keywords)

    monkeypatch.setattr(Path, "open", opened)
    # When the launcher wraps that storage error while scenario continuation is enabled.
    summary, _directory, code = run_player_session(
        PlayerSessionOptions(
            build_directory=build,
            output_directory=output,
            mode="fresh-process",
            iterations=2,
            failure_policy="continue",
            interval_seconds=0.0,
        )
    )
    # Then the storage cause stops admission and retains observed owned exit evidence.
    assert triggered
    assert len(child[1]) == 1 and child[1][0].poll() is not None
    assert code == 1 and summary["status"] == "failed"
    assert summary["children_started"] == summary["children_finalized"] == 1
    assert summary["session_error"] == "session_child_artifact_failed"
    assert dict(summary["first_failure"] or {})["client_error"].startswith("PlayerLaunchError:")
