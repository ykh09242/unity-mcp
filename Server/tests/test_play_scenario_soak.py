"""One-owned-child CLI admission, journal corruption and bounded soak reporting."""

from dataclasses import asdict
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from xml.etree import ElementTree

from click.testing import CliRunner
from pydantic import ValidationError
import pytest

from cli.main import cli
from cli.utils import play_scenario_player as player
from cli.utils.play_scenario_player import PlayerRunOptions
from cli.utils.play_scenario_player_progress import ProgressIdentity, ProgressObserver
from cli.utils.play_scenario_soak import SoakContractError, SoakIdentity, SoakRequest, SoakRow
from cli.utils.play_scenario_soak_reports import run_player_soak
from .test_play_scenario_player import bundle, child
from .test_play_scenario_player_assurance import verified_bundle


@pytest.fixture
def soak_child(monkeypatch, tmp_path):
    script = tmp_path / "soak-child.py"
    script.write_text(
        """import hashlib, json, os, pathlib, sys, time
path = pathlib.Path(sys.argv[1])
request = json.loads(path.read_text())
manifest = json.loads(pathlib.Path(sys.argv[2]).read_text())
mode = sys.argv[3]
root = path.parent
config = request['soak']
identity = {key: request[key] for key in ('job_id', 'definition_hash', 'build_id', 'build_source_revision', 'payload_hash')}
identity['process_id'] = os.getpid()
if mode == 'never-progress': time.sleep(30)
if mode == 'delayed-timeout': time.sleep(1.2)
started = int(time.time() * 1000)
if mode == 'delayed-timeout':
    initial = dict(identity, schema_version=1, sequence=1, main_loop_sequence=1,
        heartbeat_unix_ms=started, elapsed_ms=0, phase='running', iteration=1,
        stage='steps', step_index=0, status='running')
    (root / 'progress.json').write_text(json.dumps(initial))
    time.sleep(config['max_runtime_seconds'] + .1)
if mode == 'cancel':
    while not (root / 'cancel').exists(): time.sleep(.01)
rows, reports = [], []
for index in range(1, config['iterations'] + 1):
    status = 'timed_out' if mode == 'delayed-timeout' else ('cancelled' if mode == 'cancel' or (mode == 'unstarted-after-success' and index == 2) else ('failed' if mode == 'unstarted' or (index == 2 and mode in ('failure', 'failure-control')) else 'succeeded'))
    begin = started + index * 10
    end = begin + 5
    reproduction = {key: manifest[key] for key in ('definition_hash', 'unity_version', 'package_version', 'build_id', 'build_source_revision', 'payload_hash')}
    reproduction.update(source_revision=request.get('source_revision'), payload_verification='launcher_admission')
    metrics_enabled = manifest['definition']['metrics']['enabled']
    sample = dict(iteration=1, timestamp_unix_ms=end, managed_bytes=None, allocated_bytes=None,
        object_count=None, runner_subscription_count=None, runner_handle_count=None,
        ignored_for_trend=index <= manifest['definition']['metrics']['warmup_iterations'], error=None)
    report = dict(job_id=request['job_id'], scenario=manifest['definition'], status=status,
        execution_environment='player', finalization_state='completed', runner_resources_released=True,
        exit_code=0 if status == 'succeeded' else 1, started_unix_ms=begin, finished_unix_ms=end,
        repeat_count=1, timeout_seconds=request['timeout_seconds'], reproduction=reproduction,
        player_schema_version=2, iteration_results_version=1,
        iteration_results=[dict(iteration=1, status='passed' if status == 'succeeded' else status, started_unix_ms=begin, finished_unix_ms=end)],
        metrics=[sample] if metrics_enabled else [])
    if mode == 'unstarted' or (mode == 'unstarted-after-success' and index == 2):
        report['iteration_results'][0].update(status='skipped', started_unix_ms=None, finished_unix_ms=None)
    filename = 'iteration-%04d.json' % index
    error = 'fixture failure\\u0001<&' if status == 'failed' else None
    row = dict(identity, schema_version=1, iteration=index, status=status, started_unix_ms=begin,
        finished_unix_ms=end, runner_resources_released=True, failure_code=None, error=error,
        cleanup_error=None, unexpected_log_count=0, query_counts=None, metrics=[dict(sample, iteration=index)] if metrics_enabled else [], report_file=filename)
    if mode == 'unstarted' or (mode == 'unstarted-after-success' and index == 2): row.update(started_unix_ms=None, finished_unix_ms=None)
    rows.append(row)
    reports.append(report)
    (root / filename).write_text(json.dumps(report))
    if status in ('timed_out', 'cancelled') or (status != 'succeeded' and config['failure_policy'] == 'stop'):
        break
failed = [row['iteration'] for row in rows if row['started_unix_ms'] is not None and row['status'] != 'succeeded']
started_rows = [row for row in rows if row['started_unix_ms'] is not None]
selected = failed[0] if failed else (started_rows[-1]['iteration'] if started_rows else 1)
retained = [row['report_file'] for row in rows[-config['retain_reports']:]]
first = rows[selected - 1]['report_file'] if started_rows else None
if first and first not in retained:
    retained = [first] + (retained[-(config['retain_reports'] - 1):] if config['retain_reports'] > 1 else [])
for row in rows:
    if row['report_file'] not in retained:
        (root / row['report_file']).unlink()
journal = b''.join((json.dumps(row, separators=(',', ':')) + '\\n').encode() for row in rows)
summary = dict(identity, schema_version=1, process_scope='same-process', requested_iterations=config['iterations'],
    recorded_iterations=len(rows), attempted_iterations=len(started_rows), passed_iterations=len(started_rows)-len(failed),
    failed_iterations=len(failed), skipped_iterations=config['iterations']-len(started_rows),
    first_failure_iteration=failed[0] if failed else None, started_unix_ms=started,
    deadline_unix_ms=started+config['max_runtime_seconds']*1000, finished_unix_ms=rows[-1]['finished_unix_ms'],
    status='failed' if failed else 'succeeded', finalization_state='completed', failure_policy=config['failure_policy'],
    retain_reports=config['retain_reports'], journal_file='iterations.jsonl', journal_rows=len(rows),
    journal_bytes=len(journal), journal_sha256=hashlib.sha256(journal).hexdigest(), journal_complete=True,
    runner_resources_released=True, selected_iteration=selected, retained_report_files=retained, error=None, metrics_summary=None)
code = 1 if failed or len(started_rows) != config['iterations'] else 0
if mode == 'unstarted':
    summary.update(attempted_iterations=0, passed_iterations=0, failed_iterations=0,
        skipped_iterations=config['iterations'], first_failure_iteration=None, selected_iteration=None,
        status='failed', finished_unix_ms=started+15, error='Startup failed before iteration admission')
if mode == 'cancel': summary['status'] = 'cancelled'
if mode == 'delayed-timeout': summary['status'] = 'timed_out'
if mode == 'unstarted-after-success':
    summary.update(status='cancelled', finished_unix_ms=started+25, error='Cancelled before next iteration admission')
if metrics_enabled:
    samples = [sample for row in rows for sample in row['metrics']]
    warmup = manifest['definition']['metrics']['warmup_iterations']
    summary['metrics_summary'] = dict(sample_count=len(samples), usable_samples=sum(sample['iteration'] > warmup for sample in samples),
        warmup_iterations=warmup, bounded_samples=samples[-10:], warnings=[], summary='Unavailable diagnostic measurements remain null')
if mode == 'hash': summary['journal_sha256'] = 'f'*64
if mode == 'count': summary['attempted_iterations'] -= 1
if mode == 'pid': summary['process_id'] += 1
if mode == 'incomplete': summary['finalization_state'] = 'running'
if mode == 'schema': summary['schema_version'] = True
if mode == 'truncate': journal = journal[:-1]
if mode == 'duplicate': journal = journal.replace(b'"schema_version":1', b'"schema_version":1,"schema_version":1', 1)
if mode == 'order': journal = journal.replace(b'"iteration":2', b'"iteration":1', 1)
if mode == 'missing': sys.exit(7)
if mode == 'boundary-timeout': summary['status']='timed_out';summary['error']='Outer deadline expired';code=1
(root / 'iterations.jsonl').write_bytes(journal)
(root / 'soak.json').write_text(json.dumps(summary))
projection = dict(reports[selected - 1], soak=summary, exit_code=code)
if mode == 'selected': projection['started_unix_ms'] += 1
if mode == 'retained':
    retained_report = json.loads((root / retained[0]).read_text())
    retained_report['iteration_results'][0]['started_unix_ms'] += 1
    (root / retained[0]).write_text(json.dumps(retained_report))
(root / 'run.json').write_text(json.dumps(projection))
progress = dict(identity, schema_version=1, sequence=len(rows)+1, main_loop_sequence=len(rows)+1,
    heartbeat_unix_ms=summary['finished_unix_ms'], elapsed_ms=len(rows)*10, phase='finished',
    iteration=len(rows), stage='cleanup', step_index=-1, status=summary['status'])
(root / 'progress.json').write_text(json.dumps(progress))
if mode == 'forced': time.sleep(30)
sys.exit(code)
""",
        encoding="utf-8",
    )
    real_popen = subprocess.Popen
    modes, owned = ["success"], []

    def start(arguments, **kwargs):
        process = real_popen(
            [
                sys._base_executable,
                str(script),
                arguments[2],
                str(Path(kwargs["cwd"]) / "scenario-bundle.json"),
                modes[0],
            ],
            **kwargs,
        )
        owned.append(process)
        return process

    monkeypatch.setattr(player.subprocess, "Popen", start)
    return modes, owned


def test_soak_cli_launches_one_owned_process_for_more_than_ten_iterations(
    verified_bundle, soak_child
):
    # Given: a verified bundle and a child that emits twenty actual local-1 runs.
    build, output, _manifest = verified_bundle
    # When: the public CLI invokes the explicit same-process mode.
    result = CliRunner().invoke(
        cli,
        [
            "--format",
            "json",
            "play-scenario",
            "player-soak",
            str(build),
            "--output-dir",
            str(output),
            "--iterations",
            "20",
            "--retain-reports",
            "2",
        ],
    )
    # Then: one PID owns all admitted rows and native JSON is preserved.
    assert result.exit_code == 0, result.output
    summary = json.loads(result.stdout)["data"]
    directory = output / summary["job_id"]
    assert len(soak_child[1]) == 1
    assert summary["process_id"] == soak_child[1][0].pid
    assert summary["attempted_iterations"] == summary["passed_iterations"] == 20
    assert summary["unknown_iterations"] == summary["skipped_iterations"] == 0
    assert len(list(directory.glob("iteration-*.json"))) == 2
    native = json.loads((directory / "run.json").read_bytes())
    assert native["repeat_count"] == 1 and native["iteration_results"][0]["iteration"] == 1
    assert native["soak"]["selected_iteration"] == 20
    xml = ElementTree.parse(directory / "junit.xml").getroot()
    assert xml.attrib["tests"] == "20" and xml.attrib["errors"] == "0"


@pytest.mark.parametrize("policy,attempted,skipped", [("stop", 2, 3), ("continue", 5, 0)])
def test_soak_failure_preserves_first_actual_failure_and_execution_counts(
    verified_bundle, soak_child, policy, attempted, skipped
):
    # Given: only the second iteration fails in one native process.
    build, output, _manifest = verified_bundle
    soak_child[0][0] = "failure"
    # When: stop/continue governs subsequent actual runs.
    outcome = run_player_soak(
        PlayerRunOptions(
            build, output, soak=SoakRequest(iterations=5, failure_policy=policy, retain_reports=1)
        )
    )
    # Then: the failed actual report is retained, and JUnit follows the durable rows.
    assert outcome.exit_code == 1
    assert outcome.summary.evidence_state == "complete"
    assert outcome.summary.attempted_iterations == attempted
    assert outcome.summary.failed_iterations == 1 and outcome.summary.skipped_iterations == skipped
    native = json.loads((outcome.directory / "run.json").read_bytes())
    assert native["status"] == "failed" and native["soak"]["selected_iteration"] == 2
    assert [path.name for path in outcome.directory.glob("iteration-*.json")] == [
        "iteration-0002.json"
    ]
    xml = ElementTree.parse(outcome.directory / "junit.xml").getroot()
    assert xml.attrib["failures"] == "1" and xml.attrib["skipped"] == str(skipped)


@pytest.mark.parametrize(
    "mode",
    [
        "hash",
        "count",
        "pid",
        "incomplete",
        "schema",
        "truncate",
        "duplicate",
        "order",
        "selected",
        "retained",
        "missing",
    ],
)
def test_soak_untrusted_or_missing_evidence_cannot_report_success(
    verified_bundle, soak_child, mode
):
    # Given: one corrupt native artifact or a child exiting before finalization.
    build, output, _manifest = verified_bundle
    soak_child[0][0] = mode
    # When: the launcher admits native evidence.
    outcome = run_player_soak(PlayerRunOptions(build, output, soak=SoakRequest(iterations=3)))
    # Then: incomplete evidence remains explicit and the owned child has exited.
    assert outcome.exit_code == 1 and outcome.summary.evidence_state == "incomplete"
    assert outcome.summary.status == "incomplete" and outcome.summary.client_error
    assert len(soak_child[1]) == 1 and soak_child[1][0].poll() is not None
    assert (
        json.loads((outcome.directory / "client-soak.json").read_bytes())["evidence_state"]
        == "incomplete"
    )


def test_outer_timeout_after_passed_rows_keeps_successful_selected_native_run(
    verified_bundle, soak_child
):
    # Given: the boundary deadline expires after successful actual runs.
    build, output, _manifest = verified_bundle
    soak_child[0][0] = "boundary-timeout"
    # When: aggregate admission compares the actual exit with soak status.
    outcome = run_player_soak(PlayerRunOptions(build, output, soak=SoakRequest(iterations=2)))
    # Then: native selected success cannot erase the aggregate timeout.
    assert outcome.exit_code == 1 and outcome.summary.status == "timed_out"
    assert outcome.summary.evidence_state == "complete"
    assert json.loads((outcome.directory / "run.json").read_bytes())["status"] == "succeeded"
    assert ElementTree.parse(outcome.directory / "junit.xml").getroot().attrib["errors"] == "1"


@pytest.mark.parametrize(
    "field,value",
    [
        ("iterations", True),
        ("iterations", 1),
        ("iterations", 1001),
        ("max_runtime_seconds", "3600"),
        ("retain_reports", 0),
        ("retain_reports", 65),
    ],
)
def test_soak_request_rejects_invalid_bounds_before_launch(field, value):
    # Given: an invalid request boundary value.
    # When/Then: typed admission refuses it without coercion.
    with pytest.raises(ValidationError):
        SoakRequest.model_validate({field: value})


def test_normal_launch_still_rejects_soak_with_legacy_bundle(bundle, soak_child):
    # Given: a legacy bundle cannot carry a soak request.
    build, output, _manifest = bundle
    # When/Then: admission rejects before creating a child.
    with pytest.raises(player.PlayerLaunchError, match="verified v2"):
        run_player_soak(PlayerRunOptions(build, output, soak=SoakRequest(iterations=2)))
    assert soak_child[1] == []


def test_soak_progress_keeps_session_time_monotonic_across_engine_reset(tmp_path):
    # Given: session-wide receipt counters, beyond the normal repeat bound.
    identity = ProgressIdentity("a" * 32, "b" * 64, "c" * 32, "d" * 64, "label", os.getpid())
    observer = ProgressObserver(identity, max_iteration=20)
    path = tmp_path / "progress.json"
    base = dict(
        asdict(identity),
        schema_version=1,
        sequence=10,
        main_loop_sequence=10,
        heartbeat_unix_ms=10,
        elapsed_ms=10,
        phase="running",
        iteration=11,
        stage="steps",
        step_index=0,
        status="running",
    )
    path.write_text(json.dumps(base))
    observer.observe(path)
    # When: an engine incorrectly resets the outer clock.
    path.write_text(
        json.dumps(dict(base, sequence=11, main_loop_sequence=11, iteration=12, elapsed_ms=1))
    )
    observer.observe(path)
    # Then: the reset is rejected, rather than weakening monotonic progress admission.
    assert observer.first_error == "player_progress_sequence_regressed"


def test_normal_progress_does_not_admit_global_soak_iteration(tmp_path):
    # Given: a default normal-run observer.
    identity = ProgressIdentity("a" * 32, "b" * 64, "c" * 32, "d" * 64, "label", os.getpid())
    observer = ProgressObserver(identity)
    path = tmp_path / "progress.json"
    path.write_text(
        json.dumps(
            dict(
                asdict(identity),
                schema_version=1,
                sequence=1,
                main_loop_sequence=1,
                heartbeat_unix_ms=1,
                elapsed_ms=1,
                phase="running",
                iteration=11,
                stage="steps",
                step_index=0,
                status="running",
            )
        )
    )
    # When: an unexpected global iteration is observed.
    observer.observe(path)
    # Then: normal native repetition remains bounded at ten.
    assert observer.first_error == "player_progress_invalid"


def test_resource_and_metric_options_are_admitted_for_verified_player(verified_bundle, child):
    # Given: a supported frozen opt-in resource and metric definition.
    from hashlib import sha256

    build, output, manifest = verified_bundle
    manifest["definition"]["resources"]["enabled"] = True
    manifest["definition"]["metrics"]["enabled"] = True
    canonical = json.dumps(manifest["definition"], sort_keys=True, separators=(",", ":"))
    manifest["definition_json"] = canonical
    manifest["definition_hash"] = sha256(canonical.encode()).hexdigest()
    (build / "scenario-bundle.json").write_text(json.dumps(manifest))
    # When: launcher admission sees the supported native capabilities.
    outcome = player.run_player(PlayerRunOptions(build, output))
    # Then: payload and definition identity are retained without a capability rejection.
    assert outcome.exit_code == 0 and len(child[1]) == 1
    assert outcome.report["scenario"]["metrics"]["enabled"] is True
    assert outcome.report["scenario"]["resources"]["enabled"] is True


def test_soak_metric_samples_preserve_unavailable_measurements(verified_bundle, soak_child):
    # Given: enabled diagnostics with no supported memory counters in the child fixture.
    from hashlib import sha256

    build, output, manifest = verified_bundle
    manifest["definition"]["metrics"]["enabled"] = True
    manifest["definition"]["resources"]["enabled"] = True
    canonical = json.dumps(manifest["definition"], sort_keys=True, separators=(",", ":"))
    manifest["definition_json"] = canonical
    manifest["definition_hash"] = sha256(canonical.encode()).hexdigest()
    (build / "scenario-bundle.json").write_text(json.dumps(manifest))
    # When: three boundary samples are admitted from the same process.
    outcome = run_player_soak(PlayerRunOptions(build, output, soak=SoakRequest(iterations=3)))
    # Then: exact nullable measurements and bounded global attribution are retained.
    assert outcome.exit_code == 0
    metrics = json.loads((outcome.directory / "soak.json").read_bytes())["metrics_summary"]
    assert metrics["sample_count"] == 3
    assert [sample["iteration"] for sample in metrics["bounded_samples"]] == [1, 2, 3]
    assert all(sample["managed_bytes"] is None for sample in metrics["bounded_samples"])


def test_forced_process_termination_cannot_admit_previously_written_success(
    verified_bundle, soak_child
):
    # Given: complete looking artifacts from a child that refuses to exit.
    build, output, _manifest = verified_bundle
    soak_child[0][0] = "forced"
    # When: the bounded cleanup wait expires and the owned child is killed.
    outcome = run_player_soak(
        PlayerRunOptions(
            build,
            output,
            cleanup_wait_seconds=0.05,
            soak=SoakRequest(iterations=2, max_runtime_seconds=1),
        )
    )
    # Then: file content cannot substitute for natural process completion.
    assert outcome.exit_code == 1 and outcome.summary.evidence_state == "incomplete"
    assert soak_child[1][0].poll() is not None
    assert (
        json.loads((outcome.directory / "process.json").read_bytes())["forced_termination"] is True
    )


@pytest.mark.parametrize("cleanup_wait", [1, 2])
def test_soak_cli_grants_one_cleanup_wait_to_a_real_stalled_child(
    verified_bundle, soak_child, monkeypatch, cleanup_wait
):
    build, output, _manifest = verified_bundle
    soak_child[0][0] = "forced"
    observed = []
    stopped = []
    original_observer = player.ProgressObserver
    original_stop = player._stop_owned

    class TimedObserver(original_observer):
        def observe(self, path):
            super().observe(path)
            if self.last is not None and not observed:
                observed.append((time.monotonic(), self.last.elapsed_ms))

    def stop_owned(process):
        if process.poll() is None:
            stopped.append(time.monotonic())
        original_stop(process)

    monkeypatch.setattr(player, "ProgressObserver", TimedObserver)
    monkeypatch.setattr(player, "_stop_owned", stop_owned)
    result = CliRunner().invoke(
        cli,
        [
            "--format",
            "json",
            "play-scenario",
            "player-soak",
            str(build),
            "--output-dir",
            str(output),
            "--iterations",
            "2",
            "--timeout-seconds",
            "1",
            "--max-runtime-seconds",
            "1",
            "--cleanup-wait-seconds",
            str(cleanup_wait),
        ],
    )
    assert result.exit_code == 1, result.output
    assert len(observed) == len(stopped) == len(soak_child[1]) == 1
    assert soak_child[1][0].poll() is not None
    first_observed, native_elapsed_ms = observed[0]
    cleanup_elapsed = stopped[0] - first_observed - max(0, 1 - native_elapsed_ms / 1000)
    print(f"cleanup_wait={cleanup_wait}s, observed_cleanup={cleanup_elapsed:.3f}s")
    # Permit polling and scheduler jitter, but not a second full cleanup budget.
    assert cleanup_wait <= cleanup_elapsed < cleanup_wait + 0.75
    directory = next(output.iterdir())
    receipt = json.loads((directory / "process.json").read_bytes())
    assert receipt["forced_termination"] is True and receipt["process_ended"] is True
    assert ElementTree.parse(directory / "junit.xml").getroot().attrib["errors"] == "1"


@pytest.mark.parametrize("cleanup_wait", [1, 2])
@pytest.mark.parametrize(
    "native_elapsed_ms,cancel_mode",
    [
        (None, None),
        (0, None),
        (1000, None),
        (0, "deadline"),
        (1000, "deadline"),
        (0, "interrupt"),
        (1000, "interrupt"),
    ],
)
def test_soak_shutdown_keeps_its_original_cleanup_deadline(
    verified_bundle, monkeypatch, cleanup_wait, native_elapsed_ms, cancel_mode
):
    from io import BytesIO
    from types import SimpleNamespace

    build, output, _manifest = verified_bundle
    started = time.monotonic()
    clock = SimpleNamespace(now=started, interrupted=False)
    stopped = []
    process = SimpleNamespace(pid=123456, stdout=BytesIO(), returncode=None)
    process.poll = lambda: process.returncode
    process.wait = lambda timeout: process.returncode

    def kill():
        stopped.append(clock.now - started)
        process.returncode = 1

    def advance(seconds):
        if cancel_mode == "interrupt" and not clock.interrupted and clock.now >= started + 0.5:
            clock.interrupted = True
            raise KeyboardInterrupt
        clock.now += seconds

    def launch(arguments, **_kwargs):
        path = Path(arguments[2])
        request = json.loads(path.read_bytes())
        if native_elapsed_ms is not None:
            identity = {
                key: request[key]
                for key in (
                    "job_id",
                    "definition_hash",
                    "build_id",
                    "build_source_revision",
                    "payload_hash",
                )
            }
            progress = dict(
                identity,
                process_id=process.pid,
                schema_version=1,
                sequence=1,
                main_loop_sequence=1,
                heartbeat_unix_ms=int(time.time() * 1000),
                elapsed_ms=native_elapsed_ms,
                phase="running",
                iteration=1,
                stage="steps",
                step_index=0,
                status="running",
            )
            (path.parent / "progress.json").write_text(json.dumps(progress), encoding="utf-8")
        return process

    process.kill = kill
    monkeypatch.setattr(player.subprocess, "Popen", launch)
    monkeypatch.setattr(
        player, "time", SimpleNamespace(monotonic=lambda: clock.now, sleep=advance, time=time.time)
    )
    outcome = run_player_soak(
        PlayerRunOptions(
            build,
            output,
            timeout_seconds=1,
            cleanup_wait_seconds=cleanup_wait,
            poll_interval_seconds=0.05,
            deadline_monotonic=started + 0.5 if cancel_mode == "deadline" else None,
            soak=SoakRequest(iterations=2, max_runtime_seconds=1),
        )
    )
    runtime_left = 2 if native_elapsed_ms is None else max(0, 1 - native_elapsed_ms / 1000)
    expected_stop = min(runtime_left, 0.5) if cancel_mode else runtime_left
    assert len(stopped) == 1
    assert stopped[0] == pytest.approx(expected_stop + cleanup_wait, abs=0.11)
    assert outcome.exit_code != 0 and outcome.summary.evidence_state == "incomplete"


def test_soak_junit_replacement_failure_propagates_and_preserves_existing_xml(
    verified_bundle, soak_child, monkeypatch
):
    # Given: a failed atomic replacement on the JUnit publication boundary.
    from cli.utils import play_scenario_soak_reports as reports

    build, output, _manifest = verified_bundle
    original_replace = reports.os.replace

    def fail_xml(source, target):
        if Path(target).name == "junit.xml":
            raise OSError("fixture JUnit replace failure")
        return original_replace(source, target)

    monkeypatch.setattr(reports.os, "replace", fail_xml)
    # When: successful native evidence reaches export.
    with pytest.raises(OSError, match="JUnit replace failure"):
        run_player_soak(PlayerRunOptions(build, output, soak=SoakRequest(iterations=2)))
    # Then: no partial JUnit or temporary file is published and the child is gone.
    directory = next(output.iterdir())
    assert not (directory / "junit.xml").exists()
    assert not list(directory.glob(".junit.xml.*.tmp"))
    assert (directory / "run.json").is_file() and soak_child[1][0].poll() == 0


def test_soak_existing_output_cannot_be_rewritten_on_launch_failure(verified_bundle, soak_child):
    # Given: previously retained evidence at a caller-selected job ID.
    build, output, _manifest = verified_bundle
    job_id = "f" * 32
    directory = output / job_id
    directory.mkdir(parents=True)
    (directory / "request.json").write_bytes(b"retained request")
    (directory / "client-soak.json").write_bytes(b"retained client summary")
    # When: a fresh launch collides with the existing output directory.
    with pytest.raises(FileExistsError):
        run_player_soak(
            PlayerRunOptions(build, output, player_job_id=job_id, soak=SoakRequest(iterations=2))
        )
    # Then: no child starts and the retained client evidence is unchanged.
    assert soak_child[1] == []
    assert (directory / "client-soak.json").read_bytes() == b"retained client summary"


def test_soak_startup_failure_counts_zero_actual_attempts(verified_bundle, soak_child):
    # Given: a native startup failure with a local skipped ledger and no actual start.
    build, output, _manifest = verified_bundle
    soak_child[0][0] = "unstarted"
    # When: the final native failed checkpoint is admitted.
    outcome = run_player_soak(PlayerRunOptions(build, output, soak=SoakRequest(iterations=2)))
    # Then: a durable startup row is not counted as an attempted or failed execution.
    assert outcome.exit_code == 1 and outcome.summary.evidence_state == "complete"
    assert outcome.summary.recorded_iterations == 1
    assert outcome.summary.attempted_iterations == outcome.summary.failed_iterations == 0
    assert outcome.summary.skipped_iterations == 2 and outcome.summary.unknown_iterations == 0
    native = json.loads((outcome.directory / "soak.json").read_bytes())
    assert native["first_failure_iteration"] is None and native["selected_iteration"] is None
    xml = ElementTree.parse(outcome.directory / "junit.xml").getroot()
    assert xml.attrib["skipped"] == "2" and xml.attrib["errors"] == "1"


def test_successful_selected_report_survives_unstarted_cancellation_and_retain_one(
    verified_bundle, soak_child
):
    # Given: one actual success followed by cancellation before the next start.
    build, output, _manifest = verified_bundle
    soak_child[0][0] = "unstarted-after-success"
    # When: retention is limited to the selected actual run.
    outcome = run_player_soak(
        PlayerRunOptions(build, output, soak=SoakRequest(iterations=2, retain_reports=1))
    )
    # Then: the successful actual report remains preserved while aggregate cancellation is truthful.
    assert outcome.exit_code == 1 and outcome.summary.evidence_state == "complete"
    assert outcome.summary.status == "cancelled"
    assert outcome.summary.recorded_iterations == 2
    assert outcome.summary.attempted_iterations == outcome.summary.passed_iterations == 1
    assert outcome.summary.failed_iterations == 0 and outcome.summary.skipped_iterations == 1
    native = json.loads((outcome.directory / "run.json").read_bytes())
    assert native["status"] == "succeeded" and native["soak"]["selected_iteration"] == 1
    assert [path.name for path in outcome.directory.glob("iteration-*.json")] == [
        "iteration-0001.json"
    ]


def test_native_outer_deadline_after_delayed_startup_is_not_preempted(verified_bundle, soak_child):
    # Given: native startup takes longer than the requested one-second native runtime.
    build, output, _manifest = verified_bundle
    soak_child[0][0] = "delayed-timeout"
    # When: the native engine reaches its own outer deadline and releases normally.
    outcome = run_player_soak(
        PlayerRunOptions(
            build,
            output,
            timeout_seconds=1,
            cleanup_wait_seconds=1,
            soak=SoakRequest(iterations=2, max_runtime_seconds=1),
        )
    )
    # Then: the supervisor admits the actual native timeout without sending early cancellation.
    assert outcome.exit_code == 1 and outcome.summary.evidence_state == "complete"
    assert outcome.summary.status == "timed_out"
    assert not (outcome.directory / "cancel").exists()
    receipt = json.loads((outcome.directory / "process.json").read_bytes())
    assert receipt["actual_exit_code"] == 1 and receipt["forced_termination"] is False


def test_never_started_player_remains_bounded_and_incomplete(verified_bundle, soak_child):
    # Given: startup never publishes a native progress clock or a final report.
    build, output, _manifest = verified_bundle
    soak_child[0][0] = "never-progress"
    # When: the bounded startup, runtime and cleanup supervision budgets expire.
    outcome = run_player_soak(
        PlayerRunOptions(
            build,
            output,
            timeout_seconds=1,
            cleanup_wait_seconds=0.05,
            soak=SoakRequest(iterations=2, max_runtime_seconds=1),
        )
    )
    # Then: only the owned child is terminated and unrecorded execution remains unknown.
    assert outcome.exit_code == 1 and outcome.summary.evidence_state == "incomplete"
    assert outcome.summary.unknown_iterations == 2
    receipt = json.loads((outcome.directory / "process.json").read_bytes())
    assert receipt["process_ended"] is True and receipt["forced_termination"] is True
    assert soak_child[1][0].poll() is not None


def test_user_cancellation_preserves_exit_130_and_confirmed_native_finalization(
    verified_bundle, soak_child, monkeypatch
):
    # Given: the owned native process waits for one cancellation request.
    build, output, _manifest = verified_bundle
    soak_child[0][0] = "cancel"
    original_sleep = player.time.sleep
    interrupted = []

    def interrupt_once(seconds):
        if not interrupted:
            interrupted.append(True)
            raise KeyboardInterrupt
        original_sleep(seconds)

    monkeypatch.setattr(player.time, "sleep", interrupt_once)
    # When: the user interrupts and native cleanup returns an actual cancelled report.
    outcome = run_player_soak(
        PlayerRunOptions(build, output, cleanup_wait_seconds=1, soak=SoakRequest(iterations=2))
    )
    # Then: interruption remains exit 130 and complete cancellation evidence is retained.
    assert outcome.exit_code == 130 and outcome.summary.status == "cancelled"
    assert outcome.summary.evidence_state == "complete"
    assert (outcome.directory / "cancel").read_text() == "cancel\n"
    assert soak_child[1][0].poll() == 1
