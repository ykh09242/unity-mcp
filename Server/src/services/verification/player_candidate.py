"""Explicit, operator-admitted Player inputs for source-linked verification."""

from dataclasses import dataclass, field
from hashlib import sha256
import os
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, PrivateAttr, TypeAdapter, field_validator, model_validator

from cli.utils.play_scenario_analysis_io import read_bytes, safe_local_path
from models.play_scenarios import JobId, ScenarioName
from models.script_quality import QualityOptions
from services.script_quality.pages import encoded_size
from services.verification.affected_checks import bounded_selection, select_affected_checks
from services.verification.project_configuration import (
    LOCK_PATH,
    PackageLockEvidence,
    configuration_changes,
    package_lock_evidence,
)
from services.verification.input_chunks import (
    CHUNK_BYTES,
    MAX_INPUT_PATHS,
    ChunkIndex,
    Digest,
    Inputs,
    LoadedChunk,
    SourcePath,
    SourceSample,
    decode_object,
    load_chunks,
    source_revision,
    validate_input_paths,
)

Text = Annotated[str, Field(min_length=1, max_length=512)]
Scalar = Text | int | bool | None
StateValues = Annotated[dict[Text, Scalar], Field(max_length=64)]
# A chunked registration can name a GUID for every source/metadata pair, each
# with a 512-character path. UTF-8 names and JSON escaping affect encoded size.
CANDIDATE_MANIFEST_LIMIT = 2 * 1024 * 1024


class StateCheck(QualityOptions):
    log_marker: Annotated[str, Field(min_length=1, max_length=128, pattern=r"^[^\r\n\x00]+$")]
    expected: Annotated[StateValues, Field(min_length=1)]


class CheckMapping(QualityOptions):
    path: SourcePath
    tests: Annotated[list[Text], Field(max_length=32)] = Field(default_factory=list)
    scenarios: Annotated[list[ScenarioName], Field(max_length=32)] = Field(default_factory=list)


class CandidateFields(QualityOptions):
    project: Annotated[str, Field(min_length=1, max_length=4096)]
    scenario: ScenarioName
    source_revision: Digest
    assets: Annotated[dict[SourcePath, JobId], Field(max_length=64)] = Field(default_factory=dict)
    observations: StateValues = Field(default_factory=dict)
    build_id: JobId
    payload_hash: Digest
    state_check: StateCheck | None = None
    mappings: Annotated[list[CheckMapping], Field(max_length=128)] = Field(default_factory=list)
    broader_check: ScenarioName | None = None
    package_lock_evidence: PackageLockEvidence | None = None

    def validate_capture(self, inputs):
        if source_revision(inputs) != self.source_revision:
            raise ValueError("candidate_input_revision_mismatch")
        if any(path not in inputs or path + ".meta" not in inputs for path in self.assets):
            raise ValueError("candidate_asset_requires_source_and_metadata")
        if len(set(self.assets.values())) != len(self.assets):
            raise ValueError("candidate_ambiguous_asset_guid")
        if (
            self.package_lock_evidence
            and inputs.get(LOCK_PATH) != self.package_lock_evidence.raw_sha256
        ):
            raise ValueError("candidate_package_lock_digest_mismatch")


class PlayerCandidateManifest(CandidateFields):
    schema_version: Literal[1]
    inputs: Inputs

    @field_validator("inputs")
    @classmethod
    def allowed_inputs(cls, inputs):
        validate_input_paths(inputs)
        return inputs

    @model_validator(mode="after")
    def valid_capture(self):
        self.validate_capture(self.inputs)
        return self


class ChunkedCandidate(CandidateFields, ChunkIndex):
    schema_version: Literal[2]
    assets: Annotated[dict[SourcePath, JobId], Field(max_length=MAX_INPUT_PATHS // 2)] = Field(
        default_factory=dict
    )
    _inputs: dict = PrivateAttr(default_factory=dict)
    _loaded: list[LoadedChunk] = PrivateAttr(default_factory=list)

    @property
    def inputs(self):
        return self._inputs

    @property
    def complete(self):
        return len(self._loaded) == len(self.input_chunks) and all(
            row.state == "verified" for row in self._loaded
        )

    def resolve(self, directory, allow_partial, read):
        self._inputs, self._loaded = load_chunks(directory, self.input_chunks, read)
        if self.complete:
            self.validate_capture(self.inputs)
        elif not allow_partial:
            raise ValueError("candidate_chunk_inventory_incomplete")

    def inventory(self, live):
        chunks = []
        for row in self._loaded:
            snapshot = {path: live[path] for path in row.paths if path in live}
            state = "unknown"
            if row.state == "verified":
                if len(snapshot) < len(row.paths):
                    state = "missing"
                elif any(digest != self.inputs[path] for path, digest in snapshot.items()):
                    state = "stale"
                else:
                    state = "matched"
            chunks.append(
                {
                    **row.ref.model_dump(),
                    "registration_state": row.state,
                    "live_state": state,
                    "live_count": len(snapshot),
                    "live_revision": source_revision(snapshot) if row.state == "verified" else None,
                }
            )
        return {
            "complete": self.complete,
            "source_revision": self.source_revision,
            "path_count": sum(ref.count for ref in self.input_chunks),
            "verified_path_count": len(self.inputs),
            "chunks": chunks,
        }


def parse_manifest(
    content: bytes, directory: Path, project: Path, *, allow_partial=False, read=read_bytes
):
    if len(content) > CANDIDATE_MANIFEST_LIMIT:
        raise ValueError("candidate_manifest_size_limit")
    data = decode_object(content)
    if data.get("schema_version") != 2 and len(content) > CHUNK_BYTES:
        raise ValueError("candidate_manifest_size_limit")
    model = ChunkedCandidate if data.get("schema_version") == 2 else PlayerCandidateManifest
    manifest = model.model_validate(data)
    if Path(manifest.project) != project:
        raise ValueError("candidate_project_mismatch")
    if isinstance(manifest, ChunkedCandidate):
        manifest.resolve(directory, allow_partial, read)
    return manifest


def read_manifest(root, candidate_id, project, *, allow_partial=False, read=read_bytes):
    directory = root / TypeAdapter(ScenarioName).validate_python(candidate_id)
    content = read(directory / "candidate.json", CANDIDATE_MANIFEST_LIMIT)
    manifest = parse_manifest(content, directory, project, allow_partial=allow_partial, read=read)
    return manifest, sha256(content).hexdigest()


def live_sources(project, paths, *, read=read_bytes, lock_evidence=()):
    sources = {}
    total = 0
    for path in sorted(paths):
        # Each manifest rejects aliases. The union can contain both sides of a case-only rename.
        validate_input_paths([path])
        try:
            content = read(project / path, min(4 * 1024 * 1024, 32 * 1024 * 1024 - total))
        except FileNotFoundError:
            continue
        total += len(content)
        sources[path] = sha256(content).hexdigest()
        if path == LOCK_PATH:
            matching = [row for row in lock_evidence if row and row.raw_sha256 == sources[path]]
            if matching:
                observed = package_lock_evidence(content)
                if any(row != observed for row in matching):
                    raise ValueError("candidate_package_lock_semantics_mismatch")
    return sources


@dataclass
class PreparedCandidate:
    root: Path
    project: Path
    candidate_id: str
    baseline_candidate_id: str | None
    candidate: PlayerCandidateManifest | ChunkedCandidate
    baseline: PlayerCandidateManifest | ChunkedCandidate | None
    manifest_digest: str
    baseline_digest: str | None
    live: dict
    changed_paths: list[str] | None = field(init=False, default=None)
    selected_checks: dict = field(init=False)
    configuration_changes: list[dict] = field(init=False, default_factory=list)

    def __post_init__(self):
        if self.baseline and self.complete:
            self.changed_paths = sorted(
                path
                for path in self.paths
                if self.baseline.inputs.get(path) != self.candidate.inputs.get(path)
            )
            self.configuration_changes = configuration_changes(
                self.baseline.inputs,
                self.candidate.inputs,
                self.baseline.package_lock_evidence,
                self.candidate.package_lock_evidence,
            )
        representation_changes = {
            row["path"]
            for row in self.configuration_changes
            if row["effect"] == "representation_only"
        }
        self.selected_checks = bounded_selection(
            select_affected_checks(
                set(self.changed_paths or []) - representation_changes,
                [row.model_dump() for row in self.candidate.mappings],
                broader_check=self.candidate.broader_check,
                impact_complete=self.baseline is not None and self.complete,
                known_no_effect=bool(representation_changes),
            )
        )

    @property
    def paths(self):
        return self.candidate.inputs.keys() | (
            self.baseline.inputs.keys() if self.baseline else set()
        )

    @property
    def complete(self):
        return all(
            not isinstance(item, ChunkedCandidate) or item.complete
            for item in (self.candidate, self.baseline)
        )

    def inventories(self):
        return {
            name: manifest.inventory(self.live)
            for name, manifest in (("candidate", self.candidate), ("baseline", self.baseline))
            if isinstance(manifest, ChunkedCandidate)
        }

    def comparison(self):
        current, baseline = self.candidate, self.baseline
        changed = self.changed_paths
        moves = []
        if baseline and self.complete:
            original = {guid: path for path, guid in baseline.assets.items()}
            moves = [
                {"guid": guid, "from": original[guid], "to": path}
                for path, guid in current.assets.items()
                if guid in original and original[guid] != path
            ]
        inventories = self.inventories()
        bounded_changes = changed
        if inventories and encoded_size(changed) > 32 * 1024:
            bounded_changes = None
        return {
            "baseline_status": "captured"
            if baseline
            else ("missing" if self.baseline_candidate_id else "not_requested"),
            "baseline_candidate_id": self.baseline_candidate_id,
            "baseline_source_revision": baseline.source_revision if baseline else None,
            "baseline_inputs": baseline.inputs
            if isinstance(baseline, PlayerCandidateManifest)
            else None,
            "baseline_observations": baseline.observations if baseline else None,
            "source_revision": current.source_revision,
            "candidate_inputs": current.inputs
            if isinstance(current, PlayerCandidateManifest)
            else None,
            "candidate_observations": current.observations,
            "asset_moves": moves,
            "changed_paths": bounded_changes,
            **(
                {"configuration_changes": self.configuration_changes}
                if self.configuration_changes
                else {}
            ),
            **({"changed_path_count": len(changed)} if inventories and changed is not None else {}),
            **({"input_inventory": inventories} if inventories else {}),
            "selected_checks": self.selected_checks,
            "live_matches_candidate": self.complete and self.live == current.inputs,
            "manifest_sha256": self.manifest_digest,
            "baseline_manifest_sha256": self.baseline_digest,
        }


def prepare_candidate(project_info, selection):
    configured = os.environ.get("UNITY_MCP_VERIFICATION_ROOT")
    if not configured or not Path(configured).is_absolute():
        raise ValueError("verification_operator_root_required")
    root = safe_local_path(Path(configured))
    selected = project_info.get("projectRoot")
    if not isinstance(selected, str) or not Path(selected).is_absolute():
        raise ValueError("verification_selected_project_unavailable")
    project = safe_local_path(Path(selected))
    identity = selection["candidate_id"]
    sample = SourceSample()
    candidate, digest = read_manifest(root, identity, project, allow_partial=True, read=sample.read)
    baseline_id = selection.get("baseline_candidate_id")
    baseline = baseline_digest = None
    if baseline_id:
        try:
            baseline, baseline_digest = read_manifest(
                root, baseline_id, project, allow_partial=True, read=sample.read
            )
        except FileNotFoundError:
            pass
    prepared = PreparedCandidate(
        root, project, identity, baseline_id, candidate, baseline, digest, baseline_digest, {}
    )
    prepared.live = live_sources(
        project,
        prepared.paths,
        read=sample.read,
        lock_evidence=[item.package_lock_evidence for item in (candidate, baseline) if item],
    )
    sample.finish()
    return prepared
