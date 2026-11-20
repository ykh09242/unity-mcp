"""Offline capture and assembly of selected Player input manifests."""

from hashlib import sha256
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Annotated

import click
from pydantic import Field, TypeAdapter

from cli.utils.play_scenario_analysis_io import read_bytes, safe_local_path
from models.play_scenarios import ScenarioName
from services.verification.input_chunks import (
    CHUNK_BYTES,
    CHUNK_PATHS,
    MAX_CHUNKS,
    ChunkIndex,
    ChunkRef,
    Digest,
    InputChunk,
    SourcePath,
    SourceSample,
    decode_object,
    load_chunks,
    source_revision,
    validate_input_paths,
)
from services.verification.player_candidate import live_sources


def candidate_directory(candidate_id):
    root = os.environ.get("UNITY_MCP_VERIFICATION_ROOT")
    if not root or not Path(root).is_absolute():
        raise ValueError("verification_operator_root_required")
    return safe_local_path(Path(root) / TypeAdapter(ScenarioName).validate_python(candidate_id))


def capture_chunk(directory, project, paths):
    paths = TypeAdapter(
        Annotated[list[SourcePath], Field(min_length=1, max_length=CHUNK_PATHS)]
    ).validate_python(list(paths))
    validate_input_paths(paths)
    if not project.is_absolute():
        raise ValueError("absolute_project_root_required")
    project = safe_local_path(project)
    if not safe_local_path(project / "ProjectSettings" / "ProjectVersion.txt").is_file():
        raise ValueError("unity_project_marker_required")
    sample = SourceSample()
    inputs = live_sources(project, paths, read=sample.read)
    if len(inputs) != len(paths):
        raise ValueError("candidate_capture_missing_source")
    content = json.dumps({"inputs": inputs}, separators=(",", ":"), ensure_ascii=False).encode()
    ref = ChunkRef(sha256=sha256(content).hexdigest(), count=len(inputs), size_bytes=len(content))
    folder = safe_local_path(directory / "input-chunks")
    folder.mkdir(parents=True, exist_ok=True)
    destination = safe_local_path(folder / (ref.sha256 + ".json"))
    with TemporaryDirectory(dir=folder) as staging:
        temporary = Path(staging) / "chunk"
        temporary.write_bytes(content)
        sample.finish()
        try:
            os.link(safe_local_path(temporary), safe_local_path(destination))
        except FileExistsError:
            if read_bytes(destination, CHUNK_BYTES) != content:
                raise ValueError("candidate_existing_chunk_digest_mismatch") from None
    return ref.model_dump()


def assemble_index(directory, digests):
    identities = TypeAdapter(
        Annotated[list[Digest], Field(min_length=1, max_length=MAX_CHUNKS)]
    ).validate_python(list(digests))
    refs = []
    for digest in identities:
        content = read_bytes(directory / "input-chunks" / (digest + ".json"), CHUNK_BYTES)
        if sha256(content).hexdigest() != digest:
            raise ValueError("candidate_chunk_digest_mismatch")
        inputs = InputChunk.model_validate(decode_object(content)).inputs
        refs.append(ChunkRef(sha256=digest, count=len(inputs), size_bytes=len(content)))
    index = ChunkIndex(input_chunks=refs)
    inputs, rows = load_chunks(directory, refs)
    if any(row.state != "verified" for row in rows):
        raise ValueError("candidate_chunk_inventory_incomplete")
    return {"schema_version": 2, "source_revision": source_revision(inputs), **index.model_dump()}


@click.group("input-chunks")
def input_chunks():
    """Capture and assemble a large explicit input selection."""


@input_chunks.command("capture")
@click.option("--candidate-id", required=True)
@click.option("--project", required=True, type=click.Path(path_type=Path, file_okay=False))
@click.option(
    "--path",
    "paths",
    multiple=True,
    required=True,
    help="Repeat for each selected source or metadata file.",
)
def capture(candidate_id, project, paths):
    """Publish one immutable source chunk and print its descriptor."""
    try:
        click.echo(json.dumps(capture_chunk(candidate_directory(candidate_id), project, paths)))
    except (OSError, ValueError) as error:
        raise click.ClickException(str(error)) from None


@input_chunks.command("index")
@click.option("--candidate-id", required=True)
@click.option(
    "--chunk", "digests", multiple=True, required=True, help="Repeat in globally sorted path order."
)
def index_command(candidate_id, digests):
    """Print registration fields for a complete ordered list of captured chunks."""
    try:
        click.echo(json.dumps(assemble_index(candidate_directory(candidate_id), digests)))
    except (OSError, ValueError) as error:
        raise click.ClickException(str(error)) from None
