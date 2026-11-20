"""Reproduce a reviewed pair of standalone Players against committed project inputs."""

from hashlib import sha256
from pathlib import Path
from typing import Annotated, Literal
import json
import platform
import re

from pydantic import Field, model_validator

from cli.utils.play_scenario_analysis_io import read_bytes, safe_local_path, write_new
from cli.utils.play_scenario_player import run_player
from models.play_scenarios import JobId, ScenarioName
from models.script_quality import QualityOptions
from services.verification.checkout_diagnostics import CheckoutDivergence, CheckoutInputs
from services.verification.git_draft import _Git, _blob_ids, _snapshot
from services.verification.input_chunks import (
    Digest,
    SourcePath,
    SourceSample,
    decode_object,
    source_revision,
)
from services.verification.player_candidate import StateCheck
from services.verification.portable_candidate import _check_build, read_portable_candidate
from services.verification.reproduction_execution import execute_pair
from services.verification.reproduction_verdict import pair_verdict

Commit = Annotated[str, Field(pattern=r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")]
Label = Annotated[str, Field(min_length=1, max_length=128)]
SIDES = ("baseline", "candidate")


class PairExpectation(QualityOptions):
    status: Literal["succeeded", "failed", "timed_out"]
    checkpoint: Label
    action: Label
    state: StateCheck


class BuildIdentity(QualityOptions):
    project_commit: Commit
    package_commit: Commit
    proof_sha256: Digest
    closure_sha256: Digest
    build_id: JobId
    source_revision: Digest
    payload_hash: Digest
    bundle_manifest_sha256: Digest

    def identity(self):
        return self.model_dump(include=set(BuildIdentity.model_fields))


class PairSide(BuildIdentity, PairExpectation):
    capture_sha256: Digest | None = None


class BuildCapture(BuildIdentity):
    schema_version: Literal[1]
    kind: Literal["verification_player_build_capture"]
    scenario: ScenarioName
    definition_hash: Digest


class SerializedReference(QualityOptions):
    source: SourcePath
    field: Annotated[str, Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")]
    target: SourcePath


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
    project_bundle_sha256: Digest | None = None
    preserved_guids: Annotated[list[JobId], Field(max_length=64)] = Field(default_factory=list)
    serialized_references: Annotated[list[SerializedReference], Field(max_length=16)] = Field(
        default_factory=list
    )
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


def _serialized_references(git, blobs, assets, references):
    if not references:
        return []
    if any(ref.source not in blobs or ref.target not in assets for ref in references):
        raise ValueError("reproduction_serialized_reference_inputs_required")
    with git.blobs({ref.source: blobs[ref.source] for ref in references}) as files:
        documents = dict(files)
    observed = []
    for ref in references:
        # Unity text serialization writes top-level fields exactly two spaces in.
        # Require one reference and a nonzero fileID in this admitted scene or asset.
        pattern = (
            rb"^  "
            + ref.field.encode()
            + rb": \{fileID: (-?[0-9]{1,20}), guid: ([0-9a-f]{32}), type: [0-9]+\}\r?$"
        )
        body = documents[ref.source]
        hits = re.findall(pattern, body, re.MULTILINE)
        if (
            not body.startswith(b"%YAML 1.1")
            or len(re.findall(rb"^  " + ref.field.encode() + rb":", body, re.MULTILINE)) != 1
            or len(hits) != 1
            or int(hits[0][0]) == 0
            or hits[0][1].decode() != assets[ref.target]
        ):
            raise ValueError("reproduction_serialized_reference_mismatch: " + ref.source)
        observed.append(
            {**ref.model_dump(), "guid": assets[ref.target], "file_id": hits[0][0].decode()}
        )
    return observed


def _check_native_source(directory, side, sample, package):
    source = decode_object(sample.read(directory / "build/build-source.json", 4096))
    raw_digest = source.get("package_checkout_sha256")
    if not isinstance(raw_digest, str) or not re.fullmatch("[0-9a-f]{64}", raw_digest):
        raise ValueError("reproduction_native_package_checkout_digest_required")
    tree = (
        _Git(package)
        .read("rev-parse", "--verify", side.package_commit + ":MCPForUnity")
        .strip()
        .decode("ascii")
    )
    expected = {
        "schema_version": 1,
        "kind": "verification_player_build_source",
        "build_id": side.build_id,
        "source_revision": side.source_revision,
        "project_commit": side.project_commit,
        "package_commit": side.package_commit,
        "package_tree": tree,
        "package_checkout_sha256": raw_digest,
    }
    if source != expected:
        raise ValueError("reproduction_native_build_source_mismatch")
    return source


def _admit(project, directory, pair, side, sample, current, *, package=None):
    native_source = None
    if isinstance(side, BuildCapture) or getattr(side, "capture_sha256", None):
        native_source = _check_native_source(
            directory, side, sample, package or Path(__file__).resolve().parents[4]
        )
    if getattr(side, "capture_sha256", None):
        content = sample.read(directory / "capture.json", 64 * 1024)
        capture = BuildCapture.model_validate(decode_object(content))
        if (
            sha256(content).hexdigest() != side.capture_sha256
            or capture.scenario != pair.scenario
            or capture.definition_hash != pair.definition_hash
            or capture.identity() != side.identity()
        ):
            raise ValueError("reproduction_build_capture_mismatch")
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
    if set(getattr(pair, "preserved_guids", ())) - set(assets.values()):
        raise ValueError("reproduction_preserved_guid_missing")
    return recheck, {
        "native_build_source": native_source,
        "verified_git_inputs": len(inputs),
        "assets": assets,
        "serialized_references": _serialized_references(
            git, blobs, assets, getattr(pair, "serialized_references", [])
        ),
        "source_observations": manifest.observations,
        "closure": closure,
    }


def _admit_pair(project, artifacts, pair, report):
    """Verify both sides and return the source checks required after the children exit."""
    if pair.project_bundle_sha256:
        from services.verification.pair_project import bundle_digest

        if (
            bundle_digest(
                artifacts / "project.bundle",
                pair.baseline.project_commit,
                pair.candidate.project_commit,
            )
            != pair.project_bundle_sha256
        ):
            raise ValueError("reproduction_project_bundle_digest_mismatch")
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
        _unchanged(_Git(verifier), pair.candidate.package_commit, verifier_commit, ("MCPForUnity",))

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
    if (
        report["admissions"]["baseline"]["serialized_references"]
        != report["admissions"]["candidate"]["serialized_references"]
    ):
        raise ValueError("reproduction_serialized_reference_changed")
    for check in checks:
        check()

    def recheck_sources():
        check_revisions()
        sample.finish()

    recheck_sources()
    return recheck_sources


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
    if any(output.is_relative_to(artifacts / name / "build") for name in SIDES):
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
        recheck_sources = _admit_pair(project, artifacts, pair, report)
        execute_pair(artifacts, output, pair, report, launch=run_player)
        recheck_sources()
        verdict, code = pair_verdict(report["runs"], save_upgrade=save_upgrade)
        report.update(verdict)
    except CheckoutDivergence as error:
        report["reason"] = str(error)[:1024]
        report["checkout_diagnostics"] = error.findings
    except (OSError, ValueError, RuntimeError, TimeoutError) as error:
        report["reason"] = str(error)[:1024]
    filename = "save-upgrade.json" if save_upgrade else "reproduction.json"
    write_new(output / filename, (json.dumps(report, indent=2) + "\n").encode("utf-8"))
    return report, code
