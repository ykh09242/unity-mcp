"""Transfer a pinned input manifest between checkouts with separate local Player admission."""

from hashlib import sha256
import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile
from time import monotonic

from pydantic import TypeAdapter

from cli.utils.play_scenario_analysis_io import read_bytes, safe_local_path
from cli.utils.play_scenario_player import MANIFEST_LIMIT, PlayerBundle
from cli.utils.play_scenario_player_payload import payload_file_stamps, verified_payload_files
from cli.utils.play_scenario_player_platform import executable_path
from models.play_scenarios import ScenarioName
from services.verification.input_chunks import SourceSample, decode_object
from services.verification.player_candidate import (
    CANDIDATE_MANIFEST_LIMIT,
    Digest,
    live_sources,
    parse_manifest,
)


def _encode(value):
    return (
        json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")) + "\n"
    ).encode("utf-8")


def _directory(candidate_id):
    TypeAdapter(ScenarioName).validate_python(candidate_id)
    root = os.environ.get("UNITY_MCP_VERIFICATION_ROOT", "")
    if not Path(root).is_absolute():
        raise ValueError("verification_operator_root_required")
    return safe_local_path(Path(root) / candidate_id)


def _check_build(directory, manifest, sample, bundle_sha256=None, *, definition_hash=None):
    build = safe_local_path(directory / "build")
    raw = sample.read(build / "scenario-bundle.json", MANIFEST_LIMIT)
    digest = sha256(raw).hexdigest()
    if bundle_sha256 is not None and digest != bundle_sha256:
        raise ValueError("candidate_bundle_manifest_digest_mismatch")
    bundle = PlayerBundle.model_validate_json(raw)
    payload = bundle.payload
    if (
        payload is None
        or manifest.build_id != bundle.build_id
        or manifest.payload_hash != bundle.payload_hash
        or manifest.source_revision != bundle.build_source_revision
        or manifest.scenario != bundle.scenario_name
        or (definition_hash is not None and definition_hash != bundle.definition_hash)
    ):
        raise ValueError("candidate_build_identity_mismatch")
    executable_path(build, bundle.build_target)
    deadline = monotonic() + 180
    verified = verified_payload_files(build, payload, deadline)

    def check_before_publication():
        sample.finish()
        if payload_file_stamps(safe_local_path(build), deadline) != verified:
            raise ValueError("candidate_payload_changed_during_admission")

    return digest, check_before_publication


def _check_sources(project, manifest, sample):
    observed = live_sources(
        project, manifest.inputs, read=sample.read, lock_evidence=[manifest.package_lock_evidence]
    )
    for path, expected in sorted(manifest.inputs.items()):
        if path not in observed:
            raise ValueError(f"candidate_source_missing: {path}")
        if observed[path] != expected:
            raise ValueError(f"candidate_source_digest_mismatch: {path}")


def _publish(path, content, *, reuse=False, before_link=None):
    """Make the whole file visible at once. An existing registration always wins."""
    path = safe_local_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    safe_local_path(path.parent)
    temporary = NamedTemporaryFile(dir=path.parent, delete=False)
    staged = Path(temporary.name)
    try:
        with temporary:
            temporary.write(content)
        if before_link is not None:
            before_link()
        try:
            os.link(staged, path)
        except FileExistsError:
            if not reuse or read_bytes(path, len(content)) != content:
                raise
    finally:
        staged.unlink()


def _copy_chunks(document, source_directory, target_directory):
    # Called only after parse_manifest validated all descriptors and source paths.
    for chunk in document.get("input_chunks", []):
        digest = chunk["sha256"]
        relative = Path("input-chunks") / (digest + ".json")
        content = read_bytes(source_directory / relative, chunk["size_bytes"])
        if len(content) != chunk["size_bytes"] or sha256(content).hexdigest() != digest:
            raise ValueError("candidate_input_chunk_digest_mismatch")
        _publish(target_directory / relative, content, reuse=True)


def export_candidate_inputs(project, candidate_id, output):
    project, output = safe_local_path(project), safe_local_path(output)
    directory = _directory(candidate_id)
    sample = SourceSample()
    raw = sample.read(directory / "candidate.json", CANDIDATE_MANIFEST_LIMIT)
    manifest = parse_manifest(raw, directory, project, read=sample.read)
    _check_sources(project, manifest, sample)
    digest, check_before_publication = _check_build(directory, manifest, sample)
    registration = decode_object(raw)
    del registration["project"]
    content = _encode(
        {
            "schema_version": 1,
            "kind": "portable_player_candidate",
            "registration": registration,
            "bundle_manifest_sha256": digest,
        }
    )
    _copy_chunks(registration, directory, output.parent)
    _publish(output, content, before_link=check_before_publication)
    return {
        "proof_sha256": sha256(content).hexdigest(),
        "source_revision": manifest.source_revision,
    }


def read_portable_candidate(project, proof, expected_sha256, sample):
    """Read the reviewed proof and its chunks without trusting the local Player."""
    TypeAdapter(Digest).validate_python(expected_sha256)
    project, proof = safe_local_path(project), safe_local_path(proof)
    content = sample.read(proof, CANDIDATE_MANIFEST_LIMIT + 4 * 1024)
    if sha256(content).hexdigest() != expected_sha256:
        raise ValueError("candidate_proof_digest_mismatch")
    document = decode_object(content)
    if (
        not isinstance(document, dict)
        or set(document) != {"schema_version", "kind", "registration", "bundle_manifest_sha256"}
        or type(document["schema_version"]) is not int
        or document["schema_version"] != 1
        or document["kind"] != "portable_player_candidate"
        or not isinstance(document["registration"], dict)
        or "project" in document["registration"]
    ):
        raise ValueError("candidate_invalid_portable_proof")
    bundle_digest = TypeAdapter(Digest).validate_python(document["bundle_manifest_sha256"])
    raw = _encode({**document["registration"], "project": str(project)})
    if document["registration"].get("schema_version") != 2 and len(content) > 68 * 1024:
        raise ValueError("candidate_registration_size_limit")
    manifest = parse_manifest(raw, proof.parent, project, read=sample.read)
    return document, bundle_digest, raw, manifest


def inspect_candidate_inputs(project, proof, expected_sha256, *, register_as=None):
    project, proof = safe_local_path(project), safe_local_path(proof)
    sample = SourceSample()
    document, bundle_digest, raw, manifest = read_portable_candidate(
        project, proof, expected_sha256, sample
    )
    _check_sources(project, manifest, sample)
    result = {
        "proof_sha256": expected_sha256,
        "source_revision": manifest.source_revision,
        "verified_inputs": len(manifest.inputs),
        "registered": False,
    }
    if register_as is None:
        sample.finish()
        return result
    directory = _directory(register_as)
    target = safe_local_path(directory / "candidate.json")
    if target.exists():
        raise FileExistsError("candidate_registration_already_exists")
    _, check_before_publication = _check_build(directory, manifest, sample, bundle_digest)
    _copy_chunks(document["registration"], proof.parent, directory)
    parse_manifest(raw, directory, project, read=sample.read)
    _publish(target, raw, before_link=check_before_publication)
    result.update(registered=True, candidate_id=register_as)
    return result
