"""Real harmless subprocess and immutable-capture checks on isolated programs."""

from copy import deepcopy
import os
from pathlib import Path
import socket
import sys
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.tests.test_feature_program import HEAD, fp, make_small_program  # noqa: E402
from tools.tests.test_feature_program_verification import record, request  # noqa: E402
import feature_program_execution as execution  # noqa: E402


@pytest.fixture
def fixture(tmp_path, monkeypatch):
    monkeypatch.setattr(
        fp, "git", lambda _root, arguments: "" if arguments[0] == "status" else HEAD
    )
    result = make_small_program(tmp_path / "repo", "execution-fixture", ("S01", "S02"))
    assert result.claim() == 0
    return result


def prepare(fixture, script="pass", *, argv=None, closure=True, **invocation):
    program = fixture.program
    command = argv if argv is not None else [sys.executable, "-c", script]
    environment = execution.observe_environment(program, command, {"facts": {}, "unknowns": []})
    manifest = {
        "schema_version": 1,
        "checks": ["scoped_tests"],
        "groups": {
            "source": [{"path": "src/S01.py", "kind": "file", "required": True}],
            "test": [],
            "config": [],
        },
        "artifact_refs": [],
        "completeness": {
            "status": "complete" if closure else "unknown",
            "reason": "Synthetic declared scope",
            "unknowns": [] if closure else ["external_dependency"],
            "evidence_refs": [],
        },
    }
    method = {"argv": command, "cwd": str(program.repo), "execution_kind": "test"}
    record(
        fixture,
        request(
            fixture,
            check_ids=["scoped_tests"],
            input_manifest=manifest,
            environment_identity=environment,
            method_identity=method,
        ),
    )
    state = program.load()
    binding = {
        "task_id": "S01",
        "actor_id": "qa",
        "request_id": "request-1",
        "request_version": 1,
        "expected_revision": fp.phases.revision(state),
        "reason": "Actual synthetic check",
        "next_action": "Inspect actual evidence and request independent review",
    }
    live = {
        "argv": command,
        "cwd": ".",
        "execution_kind": "test",
        "capture_dir": "evidence/S01/attempt-1/run-1",
        **invocation,
    }
    return execution.prepare_run(program, state, live, binding)


def test_empty_success_captures_once_without_mutating_registry(fixture):
    # Given a real counter command with no output.
    prepared = prepare(fixture, "from pathlib import Path; Path('counter.txt').write_text('1')")
    original = fixture.program.path("backlog.json").read_bytes()
    # When the one prepared invocation executes.
    result = execution.execute_run(prepared)
    # Then actual empty output is valid evidence and no canonical task state changes.
    capture = result["capture"]
    assert capture["process"]["outcome"] == "exited"
    assert capture["process"]["exit_code"] == 0
    assert execution.summarize_capture(capture)["reported_check_result"] == "passed"
    assert capture["outputs"]["stdout"]["bytes_retained"] == 0
    assert (fixture.program.repo / "counter.txt").read_text() == "1"
    assert fixture.program.path("backlog.json").read_bytes() == original
    assert fixture.program.path(result["capture_ref"]["path"]).read_bytes() == fp.canonical(capture)


def test_same_prepared_invocation_cannot_replay(fixture):
    # Given a completed invocation that appends one byte.
    prepared = prepare(
        fixture, "from pathlib import Path; Path('counter.txt').open('a').write('x')"
    )
    execution.execute_run(prepared)
    # When the same reserved identity is used again.
    with pytest.raises(FileExistsError):
        execution.execute_run(prepared)
    # Then the command remains single execution.
    assert (fixture.program.repo / "counter.txt").read_text() == "x"


def test_nonzero_exit_and_binary_output_remain_actual_failure(fixture):
    # Given raw bytes and explicit exit seven.
    prepared = prepare(
        fixture, "import os,sys; os.write(1,b'\\x00\\xff'); os.write(2,b'error'); sys.exit(7)"
    )
    # When the real process runs.
    capture = execution.execute_run(prepared)["capture"]
    # Then neither binary output nor process failure is relabeled.
    assert capture["process"]["exit_code"] == 7
    assert execution.summarize_capture(capture)["reported_check_result"] == "failed"
    assert fixture.program.path(capture["outputs"]["stdout"]["path"]).read_bytes() == b"\x00\xff"
    assert fixture.program.path(capture["outputs"]["stderr"]["path"]).read_bytes() == b"error"


def test_dual_stream_bounds_drain_without_deadlock(fixture):
    # Given stdout and stderr each larger than their retained-byte limit.
    script = "import os; [(os.write(1,b'a'*65536),os.write(2,b'b'*65536)) for _ in range(4)]"
    prepared = prepare(fixture, script, stream_limit_bytes=1024, timeout_seconds=10)
    # When both real pipes are drained.
    capture = execution.execute_run(prepared)["capture"]
    # Then memory/disk retention is finite while the byte-count/truncation facts stay explicit.
    assert capture["process"]["exit_code"] == 0
    for name in ("stdout", "stderr"):
        assert capture["outputs"][name]["bytes_seen"] == 262144
        assert capture["outputs"][name]["bytes_retained"] == 1024
        assert capture["outputs"][name]["truncated"] is True
    assert execution.summarize_capture(capture)["reported_check_result"] == "unknown"


def test_timeout_kills_descendant_and_preserves_partial_output(fixture):
    # Given a parent and descendant that intentionally remain alive.
    script = (
        "import subprocess,sys,time; from pathlib import Path; "
        "p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)']); "
        "Path('descendant.pid').write_text(str(p.pid)); print('started',flush=True); time.sleep(60)"
    )
    prepared = prepare(fixture, script, timeout_seconds=0.7)
    # When the explicit timeout expires.
    capture = execution.execute_run(prepared)["capture"]
    # Then the supervised tree stops and partial output remains.
    assert capture["process"]["outcome"] == "timed_out"
    assert capture["process"]["cleanup_status"] == "complete"
    assert b"started" in fixture.program.path(capture["outputs"]["stdout"]["path"]).read_bytes()
    pid = int((fixture.program.repo / "descendant.pid").read_text())
    if os.name == "nt":
        api = execution._WindowsJob()
        try:
            handle = api.api.OpenProcess(0x101000, False, pid)
            if handle:
                try:
                    assert api.api.WaitForSingleObject(handle, 1000) == 0
                finally:
                    api.api.CloseHandle(handle)
        finally:
            api.close()
    else:
        stat_path = Path(f"/proc/{pid}/stat")
        assert not stat_path.exists() or stat_path.read_text().split()[2] == "Z"


def test_cancel_before_launch_has_no_process_interval(fixture):
    # Given cancellation before the command is launched.
    prepared = prepare(fixture, "raise RuntimeError('must not run')")
    canceled = threading.Event()
    canceled.set()
    # When execution sees that cancellation.
    capture = execution.execute_run(prepared, cancel=canceled)["capture"]
    # Then no process start/exit facts are invented.
    assert capture["process"]["outcome"] == "canceled"
    assert capture["process"]["started_at"] is None
    assert capture["process"]["exit_code"] is None


def test_invalid_executable_records_real_spawn_failure(fixture):
    # Given an existing file that the OS cannot load as a native executable.
    invalid = fixture.program.repo / "invalid.exe"
    invalid.write_bytes(b"invalid executable format")
    invalid.chmod(0o755)
    prepared = prepare(fixture, argv=[str(invalid)])
    # When the OS attempts the explicit launch.
    capture = execution.execute_run(prepared)["capture"]
    # Then the actual spawn failure and absent exit code are sealed.
    assert capture["process"]["outcome"] == "spawn_failed"
    assert capture["process"]["exit_code"] is None
    assert capture["process"]["started_at"] is None
    assert execution.summarize_capture(capture)["reported_check_result"] == "failed"


def test_source_drift_retains_unequal_input_captures(fixture):
    # Given a check that changes a declared source input.
    prepared = prepare(
        fixture, "from pathlib import Path; Path('src/S01.py').write_text('changed')"
    )
    # When that process returns zero.
    capture = execution.execute_run(prepared)["capture"]
    # Then its actual before/after identity cannot become passing reliance.
    before = fp.read_json(fixture.program.path(capture["input_capture_before"]["path"]))
    after = fp.read_json(fixture.program.path(capture["input_capture_after"]["path"]))
    assert before["input_digest"] != after["input_digest"]
    assert capture["comparison"]["status"] == "changed"
    assert execution.summarize_capture(capture)["reported_check_result"] == "unknown"


def test_selected_artifacts_preserve_array_xml_binary_and_empty_bytes(fixture):
    # Given explicitly selected outputs with different wire formats.
    script = (
        "from pathlib import Path; Path('array.json').write_bytes(b'[1,2]'); "
        "Path('junit.xml').write_bytes(b'<testsuite/>'); "
        "Path('raw.bin').write_bytes(b'\\x00\\xff'); Path('empty.txt').write_bytes(b'')"
    )
    names = ["array.json", "junit.xml", "raw.bin", "empty.txt"]
    prepared = prepare(fixture, script, artifacts=names)
    # When artifact bytes are frozen with the invocation.
    capture = execution.execute_run(prepared)["capture"]
    # Then JSON arrays are not repackaged and empty raw files remain valid.
    assert capture["artifacts"]["missing"] == []
    assert [
        fixture.program.path(item["path"]).read_bytes() for item in capture["artifacts"]["captured"]
    ] == [b"[1,2]", b"<testsuite/>", b"\x00\xff", b""]


def test_missing_selected_artifact_prevents_passing_capture(fixture):
    # Given a required selected artifact that the command never creates.
    prepared = prepare(fixture, artifacts=["missing.xml"])
    # When the command returns zero.
    capture = execution.execute_run(prepared)["capture"]
    # Then the absent artifact stays explicit and prevents automated pass.
    assert len(capture["artifacts"]["missing"]) == 1
    assert execution.summarize_capture(capture)["reported_check_result"] == "unknown"


def test_safe_execution_summary_excludes_raw_artifact_diagnostics(fixture):
    # Given retained diagnostics naming an explicit private output path.
    prepared = prepare(fixture, artifacts=["private-artifact.xml"])
    capture = execution.execute_run(prepared)["capture"]
    # When the ordinary dashboard summary is projected.
    summary = execution.summarize_capture(capture)
    # Then display reasons are classified without exposing raw artifact paths.
    assert "artifact_missing" in summary["reason_codes"]
    assert b"private-artifact.xml" not in fp.canonical(summary)


def test_runtime_environment_drift_during_process_is_nonpassing(fixture, monkeypatch):
    # Given a process waiting on a real rendezvous after launch.
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        listener.settimeout(10)
        port = listener.getsockname()[1]
        script = f"import socket; s=socket.create_connection(('127.0.0.1',{port})); s.send(b'R'); s.recv(1)"
        prepared = prepare(fixture, script)
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(execution.execute_run, prepared)
            with listener.accept()[0] as connection:
                assert connection.recv(1) == b"R"
                # When a declared observed runtime environment fact changes.
                monkeypatch.setenv("PYTHONPATH", "synthetic-changed-runtime")
                connection.send(b"F")
            capture = future.result(timeout=10)["capture"]
    # Then an exit-zero process cannot reuse the old environment identity.
    assert capture["process"]["exit_code"] == 0
    assert capture["comparison"]["status"] == "changed"
    assert execution.summarize_capture(capture)["reported_check_result"] == "unknown"


def test_unknown_declared_closure_cannot_appear_complete(fixture):
    # Given unknown ambient dependencies despite explicit runnable argv.
    prepared = prepare(fixture, closure=False)
    # When the harmless diagnostic process succeeds.
    capture = execution.execute_run(prepared)["capture"]
    # Then its unknown input closure remains nonpassing.
    assert capture["comparison"]["status"] == "not_assessed"
    assert execution.summarize_capture(capture)["reported_check_result"] == "unknown"


def test_changed_after_preflight_inputs_do_not_launch(fixture):
    # Given a valid reservation followed by changed checked input bytes.
    prepared = prepare(fixture, "from pathlib import Path; Path('invoked.txt').write_text('bad')")
    (fixture.program.repo / "src/S01.py").write_text("changed after preflight")
    # When the runner performs its immediately-before-launch observation.
    capture = execution.execute_run(prepared)["capture"]
    # Then no process is launched and actual changed inputs are retained.
    assert capture["process"]["outcome"] == "not_started"
    assert not (fixture.program.repo / "invoked.txt").exists()


def test_unrelated_registry_mutation_can_complete_while_child_runs(fixture):
    # Given a child waiting on a real loopback rendezvous.
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        listener.settimeout(10)
        port = listener.getsockname()[1]
        script = f"import socket; s=socket.create_connection(('127.0.0.1',{port})); s.send(b'R'); s.recv(1)"
        prepared = prepare(fixture, script)
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(execution.execute_run, prepared)
            with listener.accept()[0] as connection:
                assert connection.recv(1) == b"R"
                # When the actual canonical lock is acquired during execution.
                with fixture.program.lock():
                    state = fixture.program.load()
                    state["runtime_note"] = "Unrelated recorded note"
                    fixture.program.path("backlog.json").write_bytes(fp.canonical(state))
                connection.send(b"F")
            capture = future.result(timeout=10)["capture"]
    # Then process capture remains current without holding a long canonical lock.
    assert capture["process"]["exit_code"] == 0
    assert capture["comparison"]["status"] == "unchanged"


def test_stored_command_and_changed_live_method_cannot_execute(fixture):
    # Given a prepared harmless argv and a different live command.
    prepared = prepare(fixture)
    changed = {
        **prepared.invocation,
        "argv": [sys.executable, "-c", "raise RuntimeError()"],
        "cwd": ".",
        "capture_dir": "evidence/S01/attempt-1/other",
    }
    changed.pop("executable")
    state = fixture.program.load()
    # When somebody attempts a different live method against the frozen request.
    with pytest.raises(execution.ExecutionError):
        execution.prepare_run(fixture.program, state, changed, prepared.binding)
    # Then no second execution directory is reserved.
    assert not fixture.program.path("evidence/S01/attempt-1/other").exists()


def test_observation_freezes_transitive_outputs_and_rejects_drift(fixture):
    # Given an actual stable completed capture.
    prepared = prepare(fixture, "print('actual output')")
    result = execution.execute_run(prepared)
    stdout = fixture.program.path(result["capture"]["outputs"]["stdout"]["path"])
    stdout.write_bytes(b"changed")
    # When observation preparation resolves the sealed references.
    with pytest.raises(fp.ProgramError):
        execution.prepare_observation(
            fixture.program, fixture.program.load(), result["capture_ref"], prepared.binding
        )
    # Then changed output cannot be presented as the original captured success.


def test_environment_preparation_never_executes_and_keeps_unknowns(fixture):
    # Given an explicit executable whose arguments would write a counter.
    command = [sys.executable, "-c", "from pathlib import Path; Path('counter').write_text('x')"]
    # When only environment identity is prepared.
    environment = execution.observe_environment(fixture.program, command)
    # Then only read facts appear and ambient closure remains unknown.
    assert environment["unknowns"] == ["ambient_environment"]
    assert "executable_sha256" in environment["facts"]
    assert not (fixture.program.repo / "counter").exists()


def test_actual_passing_observation_can_record_through_existing_validator(fixture):
    # Given sealed actual stable execution and an adopted request.
    prepared = prepare(fixture)
    result = execution.execute_run(prepared)
    # When the ordinary existing verification transition publishes its prepared observation.
    observation = execution.prepare_observation(
        fixture.program, fixture.program.load(), result["capture_ref"], prepared.binding
    )
    saved = record(fixture, observation)
    # Then exact observed hashes and process output attribution are retained.
    assert saved["input"]["result"] == "passed"
    assert saved["input"]["input_before"] == saved["input"]["input_after"]
    assert saved["input"]["run_capture_ref"] == result["capture_ref"]


def test_truthful_nonpassing_drift_observation_can_record(fixture):
    # Given actual source drift during an exit-zero command.
    prepared = prepare(fixture, "from pathlib import Path; Path('src/S01.py').write_text('drift')")
    result = execution.execute_run(prepared)
    # When the nonpassing ordinary observation records its unequal executed input identities.
    observation = execution.prepare_observation(
        fixture.program, fixture.program.load(), result["capture_ref"], prepared.binding
    )
    saved = record(fixture, observation)
    # Then history remains truthful instead of rebinding the after hash to the request.
    assert saved["input"]["result"] == "unknown"
    assert saved["input"]["input_before"] != saved["input"]["input_after"]


def test_missing_after_capture_records_nonpassing_diagnostic(fixture, monkeypatch):
    # Given a real command and a failure observing its final inputs.
    prepared = prepare(fixture)
    original = execution.inputs.capture_inputs
    calls = 0

    def capture_or_fail(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise execution.ExecutionError("Synthetic final input-read failure")
        return original(*args, **kwargs)

    monkeypatch.setattr(execution.inputs, "capture_inputs", capture_or_fail)
    result = execution.execute_run(prepared)
    # When recording the actual incomplete run, no missing endpoint is synthesized.
    observation = execution.prepare_observation(
        fixture.program, fixture.program.load(), result["capture_ref"], prepared.binding
    )
    saved = record(fixture, observation)
    # Then the existing ledger retains the unknown diagnostic and immutable origin.
    assert result["capture"]["input_capture_after"] is None
    assert "input_capture_after" not in saved["input"]
    assert "input_after" not in saved["input"]
    assert saved["input"]["result"] == "unknown"


def test_project_capture_exposes_pending_result_without_publication_or_reexecution(fixture):
    # Given one already sealed actual invocation awaiting publication.
    prepared = prepare(fixture, "from pathlib import Path; Path('counter.txt').write_text('1')")
    result = execution.execute_run(prepared)
    registry = fixture.program.path("backlog.json").read_bytes()
    # When one explicit immutable capture is projected.
    projected = execution.project_capture(
        fixture.program, fixture.program.load(), result["capture_ref"], budget=fp.ReadBudget()
    )
    # Then its pending facts are visible without another command or canonical transition.
    assert projected["current"] is True
    assert projected["lane"] == "tests"
    assert projected["summary"]["publication"]["status"] == "pending"
    assert projected["observation_draft"]["status"] == "prepared"
    assert (fixture.program.repo / "counter.txt").read_text() == "1"
    assert fixture.program.path("backlog.json").read_bytes() == registry


def test_project_capture_rejects_invalid_selected_hash_and_nested_output(fixture):
    # Given a sealed capture and an incorrect selector hash.
    prepared = prepare(fixture, "print('actual')")
    result = execution.execute_run(prepared)
    bad_ref = {**result["capture_ref"], "sha256": "0" * 64}
    # When its caller attempts to project altered evidence.
    with pytest.raises(fp.ProgramError):
        execution.project_capture(fixture.program, fixture.program.load(), bad_ref)
    # Then even a correct top-level selector cannot conceal changed transitive output bytes.
    fixture.program.path(result["capture"]["outputs"]["stdout"]["path"]).write_bytes(b"tampered")
    with pytest.raises(fp.ProgramError):
        execution.project_capture(fixture.program, fixture.program.load(), result["capture_ref"])


def test_project_capture_rejects_forged_origin_binding(fixture):
    # Given a copied document that claims another task's origin.
    prepared = prepare(fixture)
    result = execution.execute_run(prepared)
    changed = deepcopy(result["capture"])
    changed["task_id"] = "S02"
    name = fixture.write("evidence/S01/attempt-1/forged-capture.json", changed)
    # When the altered document is selected with its actual byte hash.
    with pytest.raises(fp.ProgramError):
        execution.project_capture(
            fixture.program, fixture.program.load(), {"path": name, "sha256": fp.digest(changed)}
        )
    # Then matching bytes alone cannot authorize a forged task binding.


def test_project_capture_keeps_superseded_origin_noncurrent(fixture):
    # Given a valid execution whose origin request has since been superseded.
    prepared = prepare(fixture)
    result = execution.execute_run(prepared)
    newer = deepcopy(prepared.request["input"])
    newer.update(
        id="request-2",
        supersedes="request-1",
        expected_revision=fp.phases.revision(fixture.program.load()),
    )
    record(fixture, newer)
    # When the old sealed facts are explicitly inspected.
    projected = execution.project_capture(
        fixture.program, fixture.program.load(), result["capture_ref"]
    )
    # Then history stays available while no current result or publication is invented.
    assert projected["current"] is False
    assert projected["currentness"] == "stale"
    assert projected["observation_draft"]["status"] == "unavailable"


def test_project_capture_does_not_revive_same_capture_after_recorded_failure(fixture):
    # Given a later recorded nonpassing fact for the exact same immutable execution.
    prepared = prepare(fixture)
    result = execution.execute_run(prepared)
    event = execution.prepare_observation(
        fixture.program, fixture.program.load(), result["capture_ref"], prepared.binding
    )
    event.update(id="later-failure", result="failed")
    record(fixture, event)
    # When the raw originally passing process capture is selected again.
    projected = execution.project_capture(
        fixture.program, fixture.program.load(), result["capture_ref"]
    )
    # Then the new adverse record prevents old-success preparation or current display.
    assert projected["current"] is False
    assert projected["observation_draft"]["status"] == "unavailable"


def test_project_capture_honors_shared_evidence_budget(fixture):
    # Given an explicit selector with insufficient remaining read budget.
    prepared = prepare(fixture)
    result = execution.execute_run(prepared)
    # When capture projection tries to resolve its immutable bytes.
    with pytest.raises(fp.ProgramError):
        execution.project_capture(
            fixture.program, fixture.program.load(), result["capture_ref"], budget=fp.ReadBudget(1)
        )
    # Then the existing aggregate bound fails rather than starting a second unlimited ledger.


@pytest.mark.skipif(os.name != "nt", reason="Windows suspended-launch supervision")
def test_blocked_native_launch_returns_unknown_and_never_resumes_late_child(fixture, monkeypatch):
    # Given a native launch boundary that returns only after its deadline.
    prepared = prepare(
        fixture,
        "from pathlib import Path; Path('late-command').write_text('bad')",
        timeout_seconds=0.1,
    )
    original = execution.subprocess.Popen
    entered = threading.Event()
    release = threading.Event()
    returned = threading.Event()
    created = []

    def delayed_spawn(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        child = original(*args, **kwargs)
        created.append(child)
        returned.set()
        return child

    monkeypatch.setattr(execution.subprocess, "Popen", delayed_spawn)
    # When the runner reaches its finite launch deadline.
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(execution.execute_run, prepared)
        assert entered.wait(5)
        try:
            result = future.result(timeout=1)
        except TimeoutError:
            release.set()
            future.result(timeout=5)
            pytest.fail("Native launch was not bounded by the declared timeout")
        finally:
            release.set()
    # Then its unresolved original fact remains and a late suspended child cannot execute.
    assert result["capture"]["process"]["outcome"] == "outcome_unknown"
    assert result["capture"]["process"]["cleanup_status"] == "unresolved"
    assert returned.wait(5)
    created[0].wait(timeout=5)
    assert not (fixture.program.repo / "late-command").exists()


def test_selected_capture_loads_original_once_and_retains_prepared_authority(fixture, monkeypatch):
    # Given one actual sealed process and a current adopted request.
    prepared = prepare(fixture)
    result = execution.execute_run(prepared)
    state = fixture.program.load()
    original = execution._load_capture
    loaded = []

    def observe(*args, **kwargs):
        loaded.append(args[1])
        return original(*args, **kwargs)

    monkeypatch.setattr(execution, "_load_capture", observe)
    # When selected projection checks the original and its observation draft.
    projected = execution.project_capture(fixture.program, state, result["capture_ref"])
    # Then one original load serves both consumers without skipping current authority.
    assert loaded == [result["capture_ref"]]
    assert projected["current"] is True
    assert projected["observation_draft"]["status"] == "prepared"
    assert projected["summary"]["reported_check_result"] == "passed"


def test_standalone_capture_does_not_charge_sealed_json_to_transitive_budget(fixture, monkeypatch):
    # Given an actual producer capture whose transitive bytes exactly fill their own allowance.
    prepared = prepare(
        fixture,
        "from pathlib import Path; Path('artifact.bin').write_bytes(b'x' * 16384)",
        artifacts=["artifact.bin"],
    )
    result = execution.execute_run(prepared)
    state = fixture.program.load()
    capture = result["capture"]
    refs = [capture["start_ref"], capture["input_capture_before"], capture["input_capture_after"]]
    refs.extend(
        {"path": stream["path"], "sha256": stream["sha256"]}
        for stream in capture["outputs"].values()
        if stream.get("path") is not None
    )
    refs.extend(capture["artifacts"]["captured"])
    allowance = sum(
        len(fixture.program.path(name).read_bytes()) for name in {ref["path"] for ref in refs}
    )
    assert len(fixture.program.path(result["capture_ref"]["path"]).read_bytes()) < allowance
    original = execution.core.ReadBudget
    ledgers = []

    def bounded(*args, **kwargs):
        if args == (64 * 1024 * 1024,) and not kwargs:
            ledger = original(allowance)
            ledgers.append(ledger)
            return ledger
        return original(*args, **kwargs)

    monkeypatch.setattr(execution.core, "ReadBudget", bounded)
    # When standalone observation uses the original separate capture/transitive policy.
    observed = execution.prepare_observation(
        fixture.program, state, result["capture_ref"], prepared.binding
    )
    # Then the sealed JSON cannot consume the transitive allowance or narrow valid admission.
    assert observed["result"] == "passed"
    assert observed["run_capture_ref"] == result["capture_ref"]
    assert len(ledgers) == 2
    assert ledgers[1].remaining == 0
