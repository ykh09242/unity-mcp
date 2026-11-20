"""Reproduce a reviewed pair of standalone Players against committed project inputs."""

from hashlib import sha256
from os.path import samestat
from pathlib import Path
from typing import Annotated, Literal
from uuid import uuid4
import json
import platform

from pydantic import Field, model_validator

from cli.utils.play_scenario_analysis_io import read_bytes, safe_local_path, write_new
from cli.utils.play_scenario_player import (
    PROCESS_LOG_LIMIT,
    REPORT_LIMIT,
    PlayerRunOptions,
    run_player,
)
from models.play_scenarios import JobId, ScenarioName
from models.script_quality import QualityOptions
from services.verification.checkout_diagnostics import CheckoutDivergence, CheckoutInputs
from services.verification.git_draft import _Git, _blob_ids, _snapshot
from services.verification.input_chunks import Digest, SourceSample, decode_object, source_revision
from services.verification.player_candidate import StateCheck
from services.verification.player_runner import _state_result
from services.verification.portable_candidate import _check_build, read_portable_candidate

Commit = Annotated[str, Field(pattern=r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")]
Label = Annotated[str, Field(min_length=1, max_length=128)]
SIDES = ("baseline", "candidate")


class PairExpectation(QualityOptions):
    status: Literal["succeeded", "failed", "timed_out"]
    checkpoint: Label
    action: Label
    state: StateCheck


class PairSide(PairExpectation):
    project_commit: Commit
    package_commit: Commit
    proof_sha256: Digest
    closure_sha256: Digest
    build_id: JobId
    source_revision: Digest
    payload_hash: Digest
    bundle_manifest_sha256: Digest


SavePath = Annotated[
    str,
    Field(
        min_length=1,
        max_length=256,
        pattern=r"^[A-Za-z0-9_-][A-Za-z0-9_.-]*(/[A-Za-z0-9_-][A-Za-z0-9_.-]*)*$",
    ),
]


class SaveUpgrade(QualityOptions):
    previous_file: SavePath
    candidate_file: SavePath
    preserve: Annotated[list[Label], Field(min_length=1, max_length=64)]
    restart: PairExpectation


class PlayerPair(QualityOptions):
    schema_version: Literal[1]
    kind: Literal["verification_player_pair"]
    scenario: ScenarioName
    definition_hash: Digest
    baseline: PairSide
    candidate: PairSide
    save_upgrade: SaveUpgrade | None = None

    @model_validator(mode="after")
    def check_preservation_contract(self) -> "PlayerPair":
        upgrade = self.save_upgrade
        if upgrade is None:
            return self
        phases = (self.baseline, self.candidate, upgrade.restart)
        if any(phase.status != "succeeded" for phase in phases):
            raise ValueError("save_upgrade_requires_successful_expectations")
        if len(set(upgrade.preserve)) != len(upgrade.preserve):
            raise ValueError("save_upgrade_duplicate_preservation_keys")
        for key in upgrade.preserve:
            if (
                any(key not in phase.state.expected for phase in phases)
                or len({json.dumps(phase.state.expected[key]) for phase in phases}) != 1
            ):
                raise ValueError("save_upgrade_preserved_expectations_must_agree: " + key)
        return self


def _head(git):
    return git.read("rev-parse", "--verify", "HEAD^{commit}").strip().decode("ascii")


def _unchanged(git, build_commit, checkout_commit, paths, *, require_clean=True):
    for commit in (build_commit, checkout_commit):
        resolved = git.read("rev-parse", "--verify", commit + "^{commit}").strip().decode("ascii")
        if resolved != commit:
            raise ValueError("reproduction_commit_unavailable: " + commit)
    # Comparing the tree also covers package configuration and imported assets outside
    # the smaller scenario inventory. Do not open the contents of unrelated files.
    trees = [
        git.read("--literal-pathspecs", "ls-tree", "-z", revision, "--", *paths)
        for revision in (build_commit, checkout_commit)
    ]
    if not trees[0] or trees[0] != trees[1]:
        raise ValueError(f"new_player_build_required: {build_commit} -> {checkout_commit}")
    if require_clean and git.read(
        "status", "--porcelain=v1", "-z", "--untracked-files=normal", "--", *paths
    ):
        raise ValueError("reproduction_requires_clean_checkout")


def _check_closure(raw, side, manifest):
    closure = decode_object(raw)
    hashes = closure.get("input_hashes")
    if (
        sha256(raw).hexdigest() != side.closure_sha256
        or closure.get("kind") != "verification_dependency_closure"
        or closure.get("schema_version") != 1
        or closure.get("candidate_commit") != side.project_commit
        or closure.get("expanded_source_revision") != side.source_revision
        or not isinstance(hashes, dict)
        or any(
            path not in manifest.inputs or manifest.inputs[path] != digest
            for path, digest in hashes.items()
        )
        or closure.get("candidate_source_revision")
        != source_revision({p: d for p, d in manifest.inputs.items() if p not in hashes})
    ):
        raise ValueError("reproduction_dependency_closure_mismatch")
    if closure.get("unresolved") != []:
        raise ValueError("reproduction_dependency_closure_unresolved")
    return {
        "sha256": side.closure_sha256,
        "verified_additional_files": len(hashes),
        "coverage": closure.get("coverage"),
        "graph_scope": closure.get("graph_scope"),
        "graph_revision": closure.get("graph_revision"),
        "graph_commit_binding": closure.get("graph_commit_binding", "unverified"),
    }


def _admit(project, directory, pair, side, sample, current):
    _, bundle_digest, _, manifest = read_portable_candidate(
        project, directory / "proof.json", side.proof_sha256, sample
    )
    if (
        manifest.scenario != pair.scenario
        or bundle_digest != side.bundle_manifest_sha256
        or manifest.build_id != side.build_id
        or manifest.source_revision != side.source_revision
        or manifest.payload_hash != side.payload_hash
    ):
        raise ValueError("reproduction_portable_identity_mismatch")
    closure = _check_closure(sample.read(directory / "closure.json", 2 * 1024**2), side, manifest)
    git = _Git(project)
    blobs = _blob_ids(git, side.project_commit, sorted(manifest.inputs))
    checkout_inputs = CheckoutInputs(project, manifest.inputs, sample) if current else None
    with git.blobs(blobs) as files:
        inputs, assets, configuration = _snapshot(
            checkout_inputs.compare(files) if checkout_inputs else files
        )
    if checkout_inputs:
        checkout_inputs.check()
    if inputs != manifest.inputs or any(
        assets.get(path) != guid for path, guid in manifest.assets.items()
    ):
        raise ValueError("reproduction_committed_inputs_mismatch")
    if (
        manifest.package_lock_evidence
        and configuration.registration(inputs).get("package_lock_evidence")
        != manifest.package_lock_evidence.model_dump()
    ):
        raise ValueError("reproduction_package_lock_mismatch")
    _, recheck = _check_build(
        directory, manifest, sample, bundle_digest, definition_hash=pair.definition_hash
    )
    return recheck, {"verified_git_inputs": len(inputs), "closure": closure}


def _execute(directory, output, pair, side, job, *, storage=None, expectation=None):
    expectation = expectation or side
    outcome = run_player(
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
    native = outcome.directory
    if native != output / job:
        raise ValueError("reproduction_native_directory_mismatch")
    sample = SourceSample()
    files = {}
    records = {}
    for filename, limit in (
        ("run.json", REPORT_LIMIT),
        ("process.json", 65536),
        ("diagnostics.json", 65536),
        ("player.log", PROCESS_LOG_LIMIT),
    ):
        content = sample.read(native / filename, limit)
        files[filename] = sha256(content).hexdigest()
        if filename.endswith(".json"):
            records[filename] = decode_object(content)
    raw, process, diagnostics = (
        records["run.json"],
        records["process.json"],
        records["diagnostics.json"],
    )
    evidence = raw.get("reproduction", {})
    if (
        raw != outcome.report
        or diagnostics != outcome.diagnostics
        or any(row.get("job_id") != job for row in (raw, process, diagnostics))
        or raw.get("execution_environment") != "player"
        or raw.get("finalization_state") != "completed"
        or raw.get("runner_resources_released") is not True
        or raw.get("report_error")
        or raw.get("progress_error")
        or diagnostics.get("cleanup_state") != "native_finalized"
        or diagnostics.get("payload_verification") != "launcher_admission"
        or diagnostics.get("deadline_reached") is not False
        or diagnostics.get("process_id") != process.get("process_id")
        or process.get("process_ended") is not True
        or process.get("forced_termination") is not False
        or process.get("actual_exit_code") != raw.get("exit_code")
        or diagnostics.get("process_exit_code") != raw.get("exit_code")
        or outcome.client_error
        or outcome.exit_code != raw.get("exit_code")
        or evidence.get("definition_hash") != pair.definition_hash
        or evidence.get("payload_hash") != side.payload_hash
        or evidence.get("build_source_revision") != side.source_revision
        or evidence.get("build_id") != side.build_id
    ):
        raise ValueError("reproduction_native_identity_or_finalization_mismatch")
    rows = raw.get("steps")
    if (
        not isinstance(rows, list)
        or not rows
        or any(
            not isinstance(row, dict)
            or not isinstance(row.get("stage"), str)
            or not isinstance(row.get("status"), str)
            for row in rows
        )
    ):
        raise ValueError("reproduction_native_steps_unavailable")
    steps = [row for row in rows if row["stage"] == "main"]
    checkpoints = [
        index
        for index, row in enumerate(steps)
        if row.get("name") == expectation.checkpoint and row.get("action") == expectation.action
    ]
    try:
        state, observed = _state_result(native, expectation.state)
    except (OSError, ValueError) as error:
        if storage is None:
            raise
        state, observed = "incomplete", {"reason": type(error).__name__}
    sample.finish()
    if state == "incomplete" and storage is None:
        raise ValueError("reproduction_state_observation_incomplete: " + observed["reason"])
    expected_exit = 0 if expectation.status == "succeeded" else 1
    expected_step = "passed" if expectation.status == "succeeded" else expectation.status
    checkpoint_index = checkpoints[0] if len(checkpoints) == 1 else None
    checkpoint_status = None if checkpoint_index is None else steps[checkpoint_index].get("status")
    checkpoint_reached = (
        checkpoint_status in {"passed", "failed", "timed_out"}
        and all(row.get("status") == "passed" for row in steps[:checkpoint_index])
        and all(
            row.get("status") in {"passed", "skipped"} for row in rows if row["stage"] == "setup"
        )
    )
    checkpoint_matched = (
        raw.get("status") == expectation.status
        and raw.get("exit_code") == expected_exit
        and checkpoint_reached
        and checkpoint_status == expected_step
        and all(
            row.get("status") in {"passed", "skipped"}
            for row in rows
            if row is not steps[checkpoint_index]
        )
    )
    return {
        "matched": checkpoint_matched and state == "passed",
        "job_id": job,
        "status": raw.get("status"),
        "exit_code": raw.get("exit_code"),
        "state": observed,
        "state_status": state,
        "checkpoint_reached": checkpoint_reached,
        "checkpoint_status": checkpoint_status,
        "files": files,
        "artifacts": f"{output.name}/{job}",
        "checkpoint": expectation.checkpoint,
        "definition_hash": pair.definition_hash,
        "build_id": side.build_id,
        "payload_hash": side.payload_hash,
        "source_revision": side.source_revision,
        "environment": evidence.get("environment"),
        "launcher_host": process.get("launch_environment"),
    }


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


def _execute_pair(artifacts, output, pair, report):
    """Run two independent sides or three ordered processes with a single owned save slot."""
    upgrade = pair.save_upgrade
    owned_slot = None
    phases = [(name, name, getattr(pair, name), None) for name in SIDES]
    if upgrade is not None:
        storage = safe_local_path(output / "storage")
        for relative in (upgrade.previous_file, upgrade.candidate_file):
            safe_local_path(storage / relative)
        storage.mkdir(mode=0o700)
        owned_slot = storage, storage.stat()
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
        if owned_slot is None:
            report["runs"][phase] = _execute(artifacts / name, output / phase, pair, side, job)
            continue
        storage, identity = owned_slot
        result = _execute(
            artifacts / name,
            output / phase,
            pair,
            side,
            job,
            storage=storage,
            expectation=expectation,
        )
        report["runs"][phase] = result
        if not samestat(identity, safe_local_path(storage).stat()):
            raise ValueError("save_upgrade_storage_replaced")
        result["save"] = saved = _save_fingerprint(storage, save_file)
        result["matched"] = result["matched"] and saved["status"] == "present"
        if phase == "baseline" and not result["matched"]:
            raise ValueError("save_upgrade_previous_save_unavailable")


def _observed_upgrade_failure(runs):
    """Require both opened-run oracles before attributing a failure to the save."""
    candidates = (runs["candidate"], runs["restart"])
    return all(
        row["checkpoint_reached"] and row["state_status"] in {"passed", "failed"}
        for row in candidates
    ) and any(
        row["state_status"] == "failed" or row["save"]["status"] != "present" for row in candidates
    )


def reproduce_pair(
    project, manifest_path, expected_sha256, artifacts, output, *, save_upgrade=False
):
    project, artifacts, output = (safe_local_path(p) for p in (project, artifacts, output))
    raw = read_bytes(manifest_path, 64 * 1024)
    if sha256(raw).hexdigest() != expected_sha256:
        raise ValueError("reproduction_manifest_digest_mismatch")
    pair = PlayerPair.model_validate(decode_object(raw))
    if (pair.save_upgrade is not None) != save_upgrade:
        raise ValueError(
            "Use save-upgrade for a manifest declaring save_upgrade, or reproduce for an ordinary pair"
        )
    if pair.baseline.build_id == pair.candidate.build_id:
        raise ValueError("reproduction_distinct_builds_required")
    if save_upgrade and (
        pair.baseline.payload_hash == pair.candidate.payload_hash
        or pair.baseline.project_commit == pair.candidate.project_commit
    ):
        raise ValueError("save_upgrade_requires_distinct_versions_and_payloads")
    if save_upgrade and any(output.is_relative_to(artifacts / name / "build") for name in SIDES):
        raise ValueError("reproduction_output_cannot_overlap_payload")
    output.mkdir(parents=True, exist_ok=False)
    report = {
        "schema_version": 1,
        "kind": "verification_player_save_upgrade"
        if save_upgrade
        else "verification_player_reproduction",
        "manifest_sha256": expected_sha256,
        "scenario": pair.scenario,
        "required_builds": {
            name: getattr(pair, name).model_dump(exclude={"state"}) for name in SIDES
        },
        "runs": {},
        "status": "unavailable",
    }
    code = 2
    if pair.save_upgrade is not None:
        report["preservation_keys"] = pair.save_upgrade.preserve
    try:
        checkout = _head(_Git(project))
        report["project_checkout_commit"] = checkout
        # Source checkouts identify the exact implementation running this command.
        # Keep an installed wheel outside Git from borrowing another checkout's HEAD.
        verifier = Path(__file__).resolve().parents[4]
        if not (verifier / "MCPForUnity/package.json").is_file():
            raise ValueError("reproduction_verifier_source_checkout_required")
        verifier_git = _Git(verifier)
        verifier_commit = _head(verifier_git)
        report["verifier_commit"] = verifier_commit
        if verifier_git.read(
            "status",
            "--porcelain=v1",
            "-z",
            "--untracked-files=normal",
            "--",
            "Server/src",
            "Server/uv.lock",
            "Server/pyproject.toml",
        ):
            raise ValueError("reproduction_requires_clean_verifier")
        report["python_version"] = platform.python_version()
        report["verifier_uv_lock_sha256"] = sha256(
            read_bytes(verifier / "Server/uv.lock", 16 * 1024**2)
        ).hexdigest()
        required = ("proof.json", "closure.json", "build/scenario-bundle.json")
        missing = [
            f"{name}/{path}"
            for name in SIDES
            for path in required
            if not safe_local_path(artifacts / name / path).is_file()
        ]
        if missing:
            report["missing"] = missing
            raise ValueError("reproduction_artifacts_unavailable")

        # The baseline stays pinned to its own historical commit; the candidate represents
        # the selected checkout and requires the native package in the verifier checkout.
        def check_revisions(*, require_clean=True):
            if _head(_Git(project)) != checkout or _head(_Git(verifier)) != verifier_commit:
                raise ValueError("reproduction_checkout_changed")
            _unchanged(
                _Git(project),
                pair.candidate.project_commit,
                checkout,
                ("Assets", "Packages", "ProjectSettings"),
                require_clean=require_clean,
            )
            _unchanged(
                _Git(verifier),
                pair.baseline.package_commit,
                pair.baseline.package_commit,
                ("MCPForUnity",),
            )
            _unchanged(
                _Git(verifier), pair.candidate.package_commit, verifier_commit, ("MCPForUnity",)
            )

        check_revisions(require_clean=False)
        sample = SourceSample()
        checks = []
        report["admissions"] = {}
        for name in SIDES:
            check, admission = _admit(
                project, artifacts / name, pair, getattr(pair, name), sample, name == "candidate"
            )
            checks.append(check)
            report["admissions"][name] = admission
        for check in checks:
            check()
        check_revisions()
        sample.finish()
        _execute_pair(artifacts, output, pair, report)
        check_revisions()
        sample.finish()
        if all(row["matched"] for row in report["runs"].values()):
            report["status"], code = ("save_preserved" if save_upgrade else "reproduced"), 0
        elif not save_upgrade:
            report["status"], code = "expectation_mismatch", 1
        elif _observed_upgrade_failure(report["runs"]):
            report["status"], code = "migration_failed", 1
        else:
            report["reason"] = "save_upgrade_migration_evidence_incomplete"
    except CheckoutDivergence as error:
        report["reason"] = str(error)[:1024]
        report["checkout_diagnostics"] = error.findings
    except (OSError, ValueError, RuntimeError, TimeoutError) as error:
        report["reason"] = str(error)[:1024]
    filename = "save-upgrade.json" if save_upgrade else "reproduction.json"
    write_new(output / filename, (json.dumps(report, indent=2) + "\n").encode("utf-8"))
    return report, code
