"""Run admitted Player builds in fresh job directories and own the save-upgrade slot."""

from hashlib import sha256
from os.path import samestat
from pathlib import Path
from uuid import uuid4

from cli.utils.play_scenario_analysis_io import read_bytes, safe_local_path
from cli.utils.play_scenario_player import PlayerRunOptions
from services.verification.reproduction_verdict import evaluate_native


def _launch(directory, output, pair, side, job, *, launch, storage):
    return launch(
        PlayerRunOptions(
            build_directory=directory / "build",
            output_directory=output,
            timeout_seconds=120,
            cleanup_wait_seconds=10,
            player_job_id=job,
            expected_build_id=side.build_id,
            expected_build_revision=side.source_revision,
            source_revision=side.source_revision,
            require_verified_payload=True,
            expected_payload_hash=side.payload_hash,
            expected_scenario=pair.scenario,
            verification_save_directory=storage,
        )
    )


def _save_fingerprint(storage: Path, relative: str):
    """Fingerprint an owned save after process exit, retaining only its size and digest."""
    path = safe_local_path(storage / relative)
    try:
        links = path.stat().st_nlink
    except FileNotFoundError:
        return {"path": relative, "status": "missing"}
    if links != 1 or not path.is_file():
        raise ValueError("save_upgrade_requires_regular_owned_file: " + relative)
    content = read_bytes(path, 32 * 1024**2)
    return {
        "path": relative,
        "status": "present" if content else "empty",
        "size": len(content),
        "sha256": sha256(content).hexdigest(),
    }


def execute_pair(artifacts, output, pair, report, *, launch):
    """Run two independent sides or three ordered processes with a single owned save slot."""
    upgrade = pair.save_upgrade
    storage = None
    storage_identity = None
    phases = [(name, name, getattr(pair, name), None) for name in ("baseline", "candidate")]
    if upgrade is not None:
        storage = safe_local_path(output / "storage")
        for relative in (upgrade.previous_file, upgrade.candidate_file):
            safe_local_path(storage / relative)
        storage.mkdir(mode=0o700)
        storage_identity = storage.stat()
        phases = [
            ("baseline", "baseline", pair.baseline, upgrade.previous_file),
            ("candidate", "candidate", pair.candidate, upgrade.candidate_file),
            ("restart", "candidate", upgrade.restart, upgrade.candidate_file),
        ]
    for phase, name, expectation, save_file in phases:
        job = uuid4().hex
        report["runs"][phase] = {
            "job_id": job,
            "artifacts": f"{phase}/{job}",
            "matched": False,
            "status": "incomplete",
        }
        side = getattr(pair, name)
        phase_output = output / phase
        outcome = _launch(
            artifacts / name, phase_output, pair, side, job, launch=launch, storage=storage
        )
        result = evaluate_native(
            outcome,
            phase_output,
            pair,
            side,
            job,
            expectation=expectation,
            save_upgrade=upgrade is not None,
        )
        report["runs"][phase] = result
        if storage is None:
            continue
        if not samestat(storage_identity, safe_local_path(storage).stat()):
            raise ValueError("save_upgrade_storage_replaced")
        result["save"] = saved = _save_fingerprint(storage, save_file)
        result["matched"] = result["matched"] and saved["status"] == "present"
        if phase == "baseline" and not result["matched"]:
            raise ValueError("save_upgrade_previous_save_unavailable")
