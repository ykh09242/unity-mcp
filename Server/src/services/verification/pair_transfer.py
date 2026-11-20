"""Capture trusted builder outputs and assemble a transfer for independent review."""

from hashlib import sha256
from pathlib import Path
import shutil
import stat

from cli.utils.play_scenario_analysis_io import open_artifact, safe_local_path, write_new
from cli.utils.play_scenario_player import MANIFEST_LIMIT, PlayerBundle
from services.verification import reproduce
from services.verification.git_draft import _Git
from services.verification.input_chunks import SourceSample, decode_object
from services.verification.pair_project import bundle_digest
from services.verification.portable_candidate import (
    _directory,
    _encode,
    _publish,
    export_candidate_inputs,
    read_portable_candidate,
)
from services.verification.reproduce import (
    SIDES,
    BuildCapture,
    PlayerPair,
    _admit,
    _head,
    _unchanged,
)


def _check_sources(project, package, capture, *, historical=False):
    git, native = _Git(project), _Git(package)
    for repository, commit, paths in (
        (git, capture.project_commit, ("Assets", "Packages", "ProjectSettings")),
        (native, capture.package_commit, ("MCPForUnity",)),
    ):
        _unchanged(repository, commit, commit if historical else _head(repository), paths)


def package_checkout_digest(package, commit, sample):
    git = _Git(package)
    if git.read("ls-files", "--others", "--", "MCPForUnity", limit=4096):
        raise ValueError("capture_untracked_package_input")
    files = (
        git.read("ls-tree", "-r", "-z", commit + ":MCPForUnity", limit=4 * 1024**2)
        .rstrip(b"\0")
        .split(b"\0")
    )
    if not 1 <= len(files) <= 8192:
        raise ValueError("capture_package_inventory_limit")
    actual = sha256()
    remaining = 512 * 1024**2
    for record in files:
        header, name = record.split(b"\t", 1)
        mode, kind, _ = header.split()
        if mode not in {b"100644", b"100755"} or kind != b"blob":
            raise ValueError("capture_nonregular_package_input")
        content = sample.read(
            package / "MCPForUnity" / name.decode("utf-8"), min(remaining, 64 * 1024**2)
        )
        remaining -= len(content)
        actual.update(name + b"\n" + sha256(content).hexdigest().encode("ascii") + b"\n")
    return actual.hexdigest()


def capture_build(project, package, candidate_id, closure, output):
    project, package, closure, output = map(safe_local_path, (project, package, closure, output))
    source = _directory(candidate_id)
    if any(
        output.is_relative_to(root)
        for root in (source, project / "Assets", package / "MCPForUnity")
    ):
        raise ValueError("capture_output_overlaps_build_or_source")
    if not (package / "MCPForUnity/package.json").is_file():
        raise ValueError("capture_requires_native_package_checkout")
    project_commit, package_commit = _head(_Git(project)), _head(_Git(package))
    output.mkdir(exist_ok=False, parents=True)
    sample = SourceSample()
    closure_bytes = sample.read(closure, 2 * 1024**2)
    if decode_object(closure_bytes).get("candidate_commit") != project_commit:
        raise ValueError("capture_dependency_closure_commit_mismatch")
    write_new(output / "closure.json", closure_bytes)
    proof = output / "proof.json"
    receipt = export_candidate_inputs(project, candidate_id, proof)
    _, manifest_digest, _, registration = read_portable_candidate(
        project, proof, receipt["proof_sha256"], sample
    )
    bundle_bytes = sample.read(source / "build/scenario-bundle.json", MANIFEST_LIMIT)
    bundle = PlayerBundle.model_validate_json(bundle_bytes)
    if sha256(bundle_bytes).hexdigest() != manifest_digest or bundle.payload is None:
        raise ValueError("capture_bundle_identity_mismatch")
    write_new(output / "build/scenario-bundle.json", bundle_bytes)
    for entry in bundle.payload_inventory:
        original = source / "build" / entry.path
        target = safe_local_path(output / "build" / entry.path)
        target.parent.mkdir(exist_ok=True, parents=True)
        with (
            open_artifact(original, entry.size_bytes) as (reader, size),
            target.open("xb") as writer,
        ):
            if size != entry.size_bytes:
                raise ValueError("capture_payload_size_mismatch")
            shutil.copyfileobj(reader, writer, length=1024 * 1024)
        target.chmod(stat.S_IMODE(original.stat().st_mode) & 0o777)
    capture = BuildCapture(
        schema_version=1,
        kind="verification_player_build_capture",
        project_commit=project_commit,
        package_commit=package_commit,
        proof_sha256=receipt["proof_sha256"],
        closure_sha256=sha256(closure_bytes).hexdigest(),
        bundle_manifest_sha256=manifest_digest,
        build_id=registration.build_id,
        payload_hash=registration.payload_hash,
        source_revision=registration.source_revision,
        scenario=bundle.scenario_name,
        definition_hash=bundle.definition_hash,
    )
    check, admission = _admit(project, output, capture, capture, sample, True, package=package)
    if (
        package_checkout_digest(package, package_commit, sample)
        != admission["native_build_source"]["package_checkout_sha256"]
    ):
        raise ValueError("capture_native_package_checkout_bytes_mismatch")
    _check_sources(project, package, capture)
    if (_head(_Git(project)), _head(_Git(package))) != (project_commit, package_commit):
        raise ValueError("capture_builder_checkout_changed")
    content = _encode(capture.model_dump())
    _publish(output / "capture.json", content, before_link=check)
    return {**capture.model_dump(), "capture_sha256": sha256(content).hexdigest()}


def _contents(directory, expected):
    if {p.name for p in directory.iterdir()} != set(expected):
        raise ValueError("unexpected_transfer_directory_contents: " + directory.name)


def pack_pair(project, directory, expectations):
    project, directory, expectations = map(safe_local_path, (project, directory, expectations))
    sample = SourceSample()
    spec = decode_object(sample.read(expectations, 64 * 1024))
    project_bundle = safe_local_path(directory / "project.bundle")
    bundle_stamp = sample.stamp(project_bundle)
    _contents(directory, (*SIDES, "project.bundle") if project_bundle.exists() else SIDES)
    package = Path(reproduce.__file__).resolve().parents[4]
    checks = []
    records = {}
    for name in SIDES:
        side = directory / name
        raw = sample.read(side / "capture.json", 64 * 1024)
        capture = BuildCapture.model_validate(decode_object(raw))
        digest = sha256(raw).hexdigest()
        records[name] = capture
        _, _, _, registration = read_portable_candidate(
            project, side / "proof.json", capture.proof_sha256, sample
        )
        expected = {"capture.json", "proof.json", "closure.json", "build"}
        if chunks := getattr(registration, "input_chunks", None):
            expected.add("input-chunks")
            _contents(side / "input-chunks", (entry.sha256 + ".json" for entry in chunks))
        _contents(side, expected)
        # Only project-owned expected outcomes belong in the review input.
        expected_outcome = reproduce.PairExpectation.model_validate(spec.get(name))
        spec[name] = {
            **expected_outcome.model_dump(),
            **capture.identity(),
            "capture_sha256": digest,
        }
    baseline, candidate = (records[name] for name in SIDES)
    if (baseline.scenario, baseline.definition_hash) != (
        candidate.scenario,
        candidate.definition_hash,
    ) or baseline.build_id == candidate.build_id:
        raise ValueError("capture_pair_requires_distinct_builds_of_one_definition")
    if set(spec) - {
        "baseline",
        "candidate",
        "preserved_guids",
        "serialized_references",
        "save_upgrade",
    }:
        raise ValueError("unexpected_pair_expectations_field")
    if project_bundle.exists():
        spec["project_bundle_sha256"] = bundle_digest(
            project_bundle, baseline.project_commit, candidate.project_commit
        )
    pair = PlayerPair(
        schema_version=1,
        kind="verification_player_pair",
        scenario=candidate.scenario,
        definition_hash=candidate.definition_hash,
        **spec,
    )
    references = []
    for name in SIDES:
        _check_sources(project, package, records[name], historical=name == "baseline")
        check, admission = _admit(
            project, directory / name, pair, getattr(pair, name), sample, name == "candidate"
        )
        checks.append(check)
        references.append(admission["serialized_references"])
    if references[0] != references[1]:
        raise ValueError("reproduction_serialized_reference_changed")

    def before_publication():
        if sample.stamp(project_bundle) != bundle_stamp:
            raise ValueError("reproduction_project_bundle_changed")
        for check in checks:
            check()

    content = _encode(pair.model_dump(exclude_none=True))
    _publish(directory / "pair.json", content, before_link=before_publication)
    return {
        "pair_sha256": sha256(content).hexdigest(),
        "builds": {name: records[name].build_id for name in SIDES},
    }
