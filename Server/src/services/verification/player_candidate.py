"""Explicit, operator-admitted Player inputs for source-linked verification."""

from dataclasses import dataclass
from hashlib import sha256
import os
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, field_validator, model_validator

from cli.utils.play_scenario_analysis_io import read_bytes, safe_local_path
from models.play_scenarios import JobId, ScenarioName
from models.script_quality import QualityOptions
from services.script_quality.pages import encoded_size
from services.verification.affected_checks import select_affected_checks

Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
Text = Annotated[str, Field(min_length=1, max_length=512)]
Scalar = Text | int | bool | None
StateValues = Annotated[dict[Text, Scalar], Field(max_length=64)]
SourcePath = Annotated[str, Field(min_length=1, max_length=512)]


class StateCheck(QualityOptions):
    log_marker: Annotated[str, Field(min_length=1, max_length=128, pattern=r"^[^\r\n\x00]+$")]
    expected: Annotated[StateValues, Field(min_length=1)]


class CheckMapping(QualityOptions):
    path: SourcePath
    tests: Annotated[list[Text], Field(max_length=32)] = Field(default_factory=list)
    scenarios: Annotated[list[ScenarioName], Field(max_length=32)] = Field(default_factory=list)


def source_revision(inputs):
    manifest = "".join(f"{path}\n{inputs[path]}\n" for path in sorted(inputs))
    return sha256(manifest.encode()).hexdigest()


class PlayerCandidateManifest(QualityOptions):
    schema_version: Literal[1]
    project: Annotated[str, Field(min_length=1, max_length=4096)]
    scenario: ScenarioName
    source_revision: Digest
    inputs: Annotated[dict[SourcePath, Digest], Field(min_length=1, max_length=128)]
    assets: Annotated[dict[SourcePath, JobId], Field(max_length=64)] = Field(default_factory=dict)
    observations: StateValues = Field(default_factory=dict)
    build_id: JobId
    payload_hash: Digest
    state_check: StateCheck | None = None
    mappings: Annotated[list[CheckMapping], Field(max_length=128)] = Field(default_factory=list)
    broader_check: ScenarioName | None = None

    @field_validator("inputs")
    @classmethod
    def allowed_inputs(cls, inputs):
        for path in inputs:
            parts = path.split("/")
            if (
                parts[0] not in {"Assets", "Packages"}
                or any(
                    not part or part.startswith(".") or part.endswith((" ", ".", "~"))
                    for part in parts
                )
                or any(ord(char) < 32 or char in ':*?"<>|\\' for char in path)
                or Path(path.removesuffix(".meta")).suffix.lower()
                not in {
                    ".cs",
                    ".prefab",
                    ".unity",
                    ".asset",
                    ".asmdef",
                    ".asmref",
                    ".controller",
                    ".mat",
                    ".shader",
                }
            ):
                raise ValueError("candidate_source_path_outside_scope")
        if len(inputs) != len({path.casefold() for path in inputs}):
            raise ValueError("candidate_duplicate_source_path")
        return inputs

    @model_validator(mode="after")
    def valid_capture(self):
        if source_revision(self.inputs) != self.source_revision:
            raise ValueError("candidate_input_revision_mismatch")
        if any(
            path not in self.inputs or path + ".meta" not in self.inputs for path in self.assets
        ):
            raise ValueError("candidate_asset_requires_source_and_metadata")
        if len(set(self.assets.values())) != len(self.assets):
            raise ValueError("candidate_ambiguous_asset_guid")
        return self


def read_manifest(root, candidate_id, project):
    path = root / candidate_id / "candidate.json"
    content = read_bytes(path, 64 * 1024)
    manifest = PlayerCandidateManifest.model_validate_json(content)
    if Path(manifest.project) != project:
        raise ValueError("candidate_project_mismatch")
    return manifest, sha256(content).hexdigest()


def live_sources(project, paths):
    sources = {}
    total = 0
    for path in sorted(paths):
        try:
            content = read_bytes(project / path, 4 * 1024 * 1024)
        except FileNotFoundError:
            continue
        total += len(content)
        if total > 32 * 1024 * 1024:
            raise ValueError("candidate_source_bytes_limit")
        sources[path] = sha256(content).hexdigest()
    return sources


@dataclass
class PreparedCandidate:
    root: Path
    project: Path
    candidate_id: str
    baseline_candidate_id: str | None
    candidate: PlayerCandidateManifest
    baseline: PlayerCandidateManifest | None
    manifest_digest: str
    baseline_digest: str | None
    live: dict

    @property
    def paths(self):
        return self.candidate.inputs.keys() | (
            self.baseline.inputs.keys() if self.baseline else set()
        )

    def comparison(self):
        current, baseline = self.candidate, self.baseline
        changed = None
        moves = []
        if baseline:
            changed = sorted(
                path for path in self.paths if baseline.inputs.get(path) != current.inputs.get(path)
            )
            original = {guid: path for path, guid in baseline.assets.items()}
            moves = [
                {"guid": guid, "from": original[guid], "to": path}
                for path, guid in current.assets.items()
                if guid in original and original[guid] != path
            ]
        selection = select_affected_checks(
            changed or [],
            [row.model_dump() for row in current.mappings],
            broader_check=current.broader_check,
            impact_complete=baseline is not None,
        )
        if encoded_size(selection) > 64 * 1024:
            selection = {"mapping_complete": False, "outstanding": ["selection_evidence_limit"]}
        return {
            "baseline_status": "captured"
            if baseline
            else ("missing" if self.baseline_candidate_id else "not_requested"),
            "baseline_candidate_id": self.baseline_candidate_id,
            "baseline_source_revision": baseline.source_revision if baseline else None,
            "baseline_inputs": baseline.inputs if baseline else None,
            "baseline_observations": baseline.observations if baseline else None,
            "source_revision": current.source_revision,
            "candidate_inputs": current.inputs,
            "candidate_observations": current.observations,
            "asset_moves": moves,
            "changed_paths": changed,
            "selected_checks": selection,
            "live_matches_candidate": self.live == current.inputs,
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
    candidate, digest = read_manifest(root, identity, project)
    baseline_id = selection.get("baseline_candidate_id")
    baseline = baseline_digest = None
    if baseline_id:
        try:
            baseline, baseline_digest = read_manifest(root, baseline_id, project)
        except FileNotFoundError:
            pass
    prepared = PreparedCandidate(
        root, project, identity, baseline_id, candidate, baseline, digest, baseline_digest, {}
    )
    prepared.live = live_sources(project, prepared.paths)
    return prepared
