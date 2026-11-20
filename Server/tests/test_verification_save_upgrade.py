"""Synthetic subprocess controls for save ownership and migration evidence admission."""

from hashlib import sha256
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from cli.utils import play_scenario_player as player
from .test_play_scenario_player import bundle
from .test_play_scenario_player_assurance import verified_bundle
from .test_verification_player_candidates import registered
from .test_verification_portable_candidate import invoke
from .test_verification_reproduce import review, write


SYNTHETIC_CHILD = """
import json
import os
import sys
import time
from pathlib import Path

request_path, bundle_path = map(Path, sys.argv[1:3])
fault = sys.argv[3]
request = json.loads(request_path.read_bytes())
bundle = json.loads(bundle_path.read_bytes())
identity = {
    key: request[key]
    for key in ('build_id', 'payload_hash', 'build_source_revision', 'definition_hash')
}
if fault != 'no-heartbeat':
    progress = {
        **identity, 'schema_version': 1, 'job_id': request['job_id'],
        'process_id': os.getpid(), 'sequence': 1,
        'main_loop_sequence': 1, 'heartbeat_unix_ms': int(time.time() * 1000),
        'elapsed_ms': 1, 'phase': 'running', 'iteration': 1, 'stage': 'steps',
        'step_index': 0, 'status': 'running',
    }
    temporary = request_path.with_name('progress.tmp')
    temporary.write_text(json.dumps(progress), encoding='utf-8')
    temporary.replace(request_path.with_name('progress.json'))
storage = Path(os.environ['UNITY_MCP_VERIFICATION_SAVE_DIRECTORY'])
assert storage == request_path.parents[2] / 'storage'
save = storage / 'owned.json'
previous = request_path.parents[1].name == 'baseline'
unrelated = not previous and fault == request_path.parents[1].name + '-scene-failure'
if previous:
    assert list(storage.iterdir()) == []
    expected = {'credits': 47, 'migration_count': 0}
    if fault != 'no-save':
        save.write_text(json.dumps(expected))
    state = expected
    failed = False
else:
    state = json.loads(save.read_bytes()) if save.exists() else {}
    old = state.get('migration_count') == 0
    failed = unrelated or not state or (fault == 'migration-failure' and old)
    if fault == 'lost' and old:
        save.unlink()
        failed = True
    elif not failed and (old or fault == 'duplicate'):
        state['migration_count'] += 1
        save.write_text(json.dumps(state))
    state = json.loads(save.read_bytes()) if save.exists() else {}
    if fault == 'checkpoint-only-failure':
        failed = True
if previous or (fault != 'no-state' and not unrelated):
    print('SAVE ' + json.dumps(state), flush=True)
code = int(failed)
report = {
    'job_id': request['job_id'], 'scenario': bundle['definition'],
    'repeat_count': request['repeat_count'], 'timeout_seconds': request['timeout_seconds'],
    'status': 'failed' if failed else 'succeeded', 'execution_environment': 'player',
    'finalization_state': 'completed', 'runner_resources_released': True,
    'exit_code': code, 'started_unix_ms': 1, 'finished_unix_ms': 2, 'player_schema_version': 2,
    'reproduction': {**identity, 'source_revision': request['source_revision'],
                      'unity_version': bundle['unity_version'],
                      'package_version': bundle['package_version'], 'payload_verification': 'launcher_admission'},
    'steps': [{'stage': 'main', 'name': 'persisted', 'action': 'wait_state',
               'status': 'failed' if failed else 'passed'}],
}
if unrelated:
    report['steps'][0]['status'] = 'skipped'
    report['steps'].insert(0, {'stage': 'main', 'name': 'load scene', 'action': 'load_scene', 'status': 'failed'})
request_path.with_name('run.json').write_text(json.dumps(report))
sys.exit(code)
"""


def _prepare_upgrade(review):
    _, _, root, pair = review
    # Each reviewed side carries a different synthetic payload, despite sharing a test program.
    directory = root / "candidate"
    bundle_path = directory / "build/scenario-bundle.json"
    frozen = json.loads(bundle_path.read_bytes())
    executable = directory / "build" / frozen["executable"]
    executable.write_bytes(b"candidate placeholder, never executed")
    entry = frozen["payload_inventory"][0]
    entry.update(
        size_bytes=executable.stat().st_size, sha256=sha256(executable.read_bytes()).hexdigest()
    )
    inventory = json.dumps(frozen["payload_inventory"], sort_keys=True, separators=(",", ":"))
    frozen.update(
        payload_inventory_json=inventory, payload_hash=sha256(inventory.encode()).hexdigest()
    )
    proof_path = directory / "proof.json"
    proof = json.loads(proof_path.read_bytes())
    proof["bundle_manifest_sha256"] = write(bundle_path, frozen)
    proof["registration"]["payload_hash"] = frozen["payload_hash"]
    pair["candidate"].update(
        payload_hash=frozen["payload_hash"],
        bundle_manifest_sha256=proof["bundle_manifest_sha256"],
        proof_sha256=write(proof_path, proof),
    )
    for phase, count in (("baseline", 0), ("candidate", 1)):
        pair[phase].update(
            status="succeeded",
            checkpoint="persisted",
            action="wait_state",
            state={"log_marker": "SAVE ", "expected": {"credits": 47, "migration_count": count}},
        )
    pair["save_upgrade"] = {
        "previous_file": "owned.json",
        "candidate_file": "owned.json",
        "preserve": ["credits"],
        "restart": {
            key: pair["candidate"][key] for key in ("status", "checkpoint", "action", "state")
        },
    }
    path = root / "save-upgrade.json"
    return path, write(path, pair)


@pytest.mark.parametrize(
    ("fault", "status", "code"),
    [
        ("none", "save_preserved", 0),
        ("migration-failure", "migration_failed", 1),
        ("duplicate", "migration_failed", 1),
        ("lost", "migration_failed", 1),
        ("no-heartbeat", "unavailable", 2),
        ("no-save", "unavailable", 2),
        ("no-state", "unavailable", 2),
        ("candidate-scene-failure", "unavailable", 2),
        ("restart-scene-failure", "unavailable", 2),
        ("checkpoint-only-failure", "unavailable", 2),
    ],
)
def test_save_upgrade_verdict_uses_fresh_processes_and_disk_state(
    review, tmp_path, monkeypatch, fault, status, code
):
    manifest, pin = _prepare_upgrade(review)
    output = tmp_path / "evidence"
    script = tmp_path / "synthetic_player.py"
    script.write_text(SYNTHETIC_CHILD, encoding="utf-8")

    def launch(arguments, **options):
        return subprocess.Popen(
            [
                sys._base_executable,
                str(script),
                arguments[2],
                str(Path(options["cwd"]) / "scenario-bundle.json"),
                fault,
            ],
            **options,
        )

    monkeypatch.setattr(
        player,
        "subprocess",
        SimpleNamespace(
            Popen=launch,
            PIPE=subprocess.PIPE,
            STDOUT=subprocess.STDOUT,
            TimeoutExpired=subprocess.TimeoutExpired,
        ),
    )
    result = invoke(
        "save-upgrade", review[0], "--manifest", manifest, "--sha256", pin, "--output", output
    )
    assert result.exit_code == code, (
        result.output,
        result.exception,
        [json.loads(path.read_bytes()) for path in output.glob("*/*/diagnostics.json")],
    )
    report = json.loads((output / "save-upgrade.json").read_bytes())
    assert report["status"] == status
    runs = report["runs"]
    if fault == "no-heartbeat":
        assert list(runs) == ["baseline"]
        diagnostics = json.loads(
            (output / runs["baseline"]["artifacts"] / "diagnostics.json").read_bytes()
        )
        assert diagnostics["cleanup_state"] == "native_finalized"
        assert diagnostics["heartbeat_observed"] is False and diagnostics["process_exit_code"] == 0
        assert report["reason"] == "reproduction_native_identity_or_finalization_mismatch"
        return
    if fault == "no-save":
        assert list(runs) == ["baseline"]
        assert runs["baseline"]["save"]["status"] == "missing"
        return
    assert len({run["job_id"] for run in runs.values()}) == 3
    assert runs["restart"]["build_id"] == runs["candidate"]["build_id"]
    if fault == "none":
        assert runs["baseline"]["save"]["sha256"] != runs["candidate"]["save"]["sha256"]
        assert runs["restart"]["save"] == runs["candidate"]["save"]
    elif fault == "duplicate":
        assert runs["candidate"]["matched"] and not runs["restart"]["matched"]
        assert runs["restart"]["state"]["observed"]["migration_count"] == 2
    elif fault == "lost":
        assert runs["restart"]["save"]["status"] == "missing"
    elif fault == "no-state":
        assert runs["restart"]["state_status"] == "incomplete"
    elif fault.endswith("-scene-failure"):
        phase = fault.split("-", 1)[0]
        assert runs[phase]["status"] == "failed"
        assert (
            runs[phase]["checkpoint_status"] == "skipped" and not runs[phase]["checkpoint_reached"]
        )
    elif fault == "checkpoint-only-failure":
        assert runs["candidate"]["checkpoint_reached"]
        assert runs["candidate"]["checkpoint_status"] == "failed"
        assert runs["candidate"]["state_status"] == "passed"
    else:
        assert runs["candidate"]["status"] == runs["restart"]["status"] == "failed"
