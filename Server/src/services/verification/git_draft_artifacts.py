"""Encode Git draft input inventories and validate them before transferring a side."""

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field

from cli.utils.play_scenario_analysis_io import read_bytes, safe_local_path
from models.play_scenarios import JobId
from models.script_quality import QualityOptions
from services.verification.input_chunks import (
    CHUNK_BYTES,
    CHUNK_PATHS,
    MAX_CHUNKS,
    MAX_INPUT_PATHS,
    ChunkIndex,
    ChunkRef,
    Digest,
    SourcePath,
    decode_object,
    encode_input_chunk,
    load_chunks,
    source_revision,
    validate_input_paths,
)
from services.verification.project_configuration import LOCK_PATH, PackageLockEvidence


_DRAFT_BYTES = 8 * 1024 * 1024
DRAFT_PATH_LIMIT = (
    f"candidate_draft_selection_limit: Up to {MAX_INPUT_PATHS} inputs per side in "
    f"{MAX_CHUNKS} chunks of {CHUNK_BYTES // 1024} KiB each. Split the changes across "
    "smaller staging selections or commit ranges and retry."
)


def pack_inputs(inputs: dict[str, str]) -> list[tuple[ChunkRef, bytes]]:
    """Pack whole entries in source-revision order, counting UTF-8 bytes and paths."""
    if len(inputs) > MAX_INPUT_PATHS:
        raise ValueError(DRAFT_PATH_LIMIT)
    chunks = []
    batch = {}
    empty_size = len(b'{"inputs":{}}')
    size = empty_size
    for path, digest in sorted(inputs.items()):
        encoded = json.dumps({path: digest}, separators=(",", ":"), ensure_ascii=False).encode()
        entry_size = len(encoded) - 2  # Braces belong to the chunk, not to each entry.
        if len(batch) == CHUNK_PATHS or size + entry_size + bool(batch) > CHUNK_BYTES:
            chunks.append(encode_input_chunk(batch))
            batch = {}
            size = empty_size
        size += entry_size + bool(batch)
        batch[path] = digest
    if batch:
        chunks.append(encode_input_chunk(batch))
    if len(chunks) > MAX_CHUNKS:
        raise ValueError(DRAFT_PATH_LIMIT)
    return chunks


class DraftSideFields(QualityOptions):
    source_revision: Digest
    assets: dict[SourcePath, JobId]
    package_lock_evidence: PackageLockEvidence | None = None

    def validate_inputs(self, inputs):
        validate_input_paths(inputs)
        if self.source_revision != source_revision(inputs):
            raise ValueError("candidate_draft_revision_mismatch")
        if any(path not in inputs or path + ".meta" not in inputs for path in self.assets):
            raise ValueError("candidate_asset_requires_source_and_metadata")
        if len(set(self.assets.values())) != len(self.assets):
            raise ValueError("candidate_ambiguous_asset_guid")
        if self.package_lock_evidence and (
            inputs.get(LOCK_PATH) != self.package_lock_evidence.raw_sha256
        ):
            raise ValueError("candidate_package_lock_evidence_mismatch")


class InlineDraftSide(DraftSideFields):
    inputs: Annotated[dict[SourcePath, Digest], Field(max_length=CHUNK_PATHS)]
    assets: Annotated[dict[SourcePath, JobId], Field(max_length=CHUNK_PATHS // 2)]


class ChunkedDraftSide(DraftSideFields, ChunkIndex):
    assets: Annotated[dict[SourcePath, JobId], Field(max_length=MAX_INPUT_PATHS // 2)]


@dataclass(frozen=True)
class DraftEvidence:
    document: dict
    inputs: dict[str, str]
    fields: dict
    chunks: list[tuple[ChunkRef, bytes]]


def read_draft_side(
    path: Path, side: Literal["baseline", "candidate"], *, chunk_directory: Path | None = None
) -> DraftEvidence:
    path = safe_local_path(path)
    document = decode_object(read_bytes(path, _DRAFT_BYTES))
    version = document.get("schema_version")
    if (
        document.get("kind") != "git_candidate_draft"
        or type(version) is not int
        or version not in (1, 2)
    ):
        raise ValueError("candidate_invalid_git_draft")
    raw_side = document.get(side)
    if not isinstance(raw_side, dict):
        raise ValueError("candidate_draft_side_missing")

    chunks = []
    if version == 2 and "input_chunks" in raw_side:
        evidence = ChunkedDraftSide.model_validate(raw_side)
        captured = {}

        def read_chunk(chunk_path, limit):
            content = read_bytes(chunk_path, limit)
            captured[chunk_path.stem] = content
            return content

        inputs, rows = load_chunks(
            chunk_directory if chunk_directory is not None else path.parent,
            evidence.input_chunks,
            read=read_chunk,
        )
        if any(row.state != "verified" for row in rows):
            raise ValueError("candidate_draft_chunk_inventory_incomplete")
        chunks = [(ref, captured[ref.sha256]) for ref in evidence.input_chunks]
    else:
        evidence = InlineDraftSide.model_validate(raw_side)
        inputs = evidence.inputs
    evidence.validate_inputs(inputs)
    fields = {"schema_version": 2 if chunks else 1, **evidence.model_dump(exclude_none=True)}
    return DraftEvidence(document, inputs, fields, chunks)
