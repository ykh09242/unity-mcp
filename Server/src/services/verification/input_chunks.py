"""Bounded, content-addressed manifests for explicitly selected Unity source files."""

from dataclasses import dataclass
from hashlib import sha256
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Annotated

from pydantic import Field, field_validator

from cli.utils.play_scenario_analysis_io import read_bytes, safe_local_path
from models.script_quality import QualityOptions

Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
SourcePath = Annotated[str, Field(min_length=1, max_length=512)]
CHUNK_BYTES = 64 * 1024
CHUNK_PATHS = 128
MAX_CHUNKS = 8
MAX_INPUT_PATHS = CHUNK_PATHS * MAX_CHUNKS
Inputs = Annotated[dict[SourcePath, Digest], Field(min_length=1, max_length=CHUNK_PATHS)]
LOCK_PATH = "Packages/packages-lock.json"
CONFIG_PATHS = frozenset(
    {LOCK_PATH, "ProjectSettings/GraphicsSettings.asset", "ProjectSettings/QualitySettings.asset"}
)


def validate_input_paths(paths):
    paths = list(paths)
    for path in paths:
        if path in CONFIG_PATHS:
            continue
        parts = path.split("/")
        if (
            parts[0] not in {"Assets", "Packages"}
            or any(
                not part or part.startswith(".") or part.endswith((" ", ".", "~")) for part in parts
            )
            or "/assets/resources/gamedata/" in f"/{path.casefold()}/"
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
                ".png",
                ".jpg",
                ".jpeg",
                ".tga",
            }
        ):
            raise ValueError("candidate_source_path_outside_scope")
    if len(paths) != len({path.casefold() for path in paths}):
        raise ValueError("candidate_duplicate_source_path")


def source_revision(inputs):
    manifest = "".join(f"{path}\n{inputs[path]}\n" for path in sorted(inputs))
    return sha256(manifest.encode()).hexdigest()


def decode_object(content):
    def unique(pairs):
        obj = dict(pairs)
        if len(obj) != len(pairs):
            raise ValueError("candidate_duplicate_json_key")
        return obj

    try:
        obj = json.loads(content.decode("utf-8"), object_pairs_hook=unique)
    except RecursionError:
        raise ValueError("candidate_json_too_deep") from None
    if not isinstance(obj, dict):
        raise ValueError("candidate_json_object_required")
    return obj


class InputChunk(QualityOptions):
    inputs: Inputs

    @field_validator("inputs")
    @classmethod
    def allowed_inputs(cls, inputs):
        validate_input_paths(inputs)
        return inputs


class ChunkRef(QualityOptions):
    sha256: Digest
    count: Annotated[int, Field(ge=1, le=CHUNK_PATHS)]
    size_bytes: Annotated[int, Field(ge=1, le=CHUNK_BYTES)]


class ChunkIndex(QualityOptions):
    input_chunks: Annotated[list[ChunkRef], Field(min_length=1, max_length=MAX_CHUNKS)]

    @field_validator("input_chunks")
    @classmethod
    def distinct(cls, refs):
        if len(refs) != len({row.sha256 for row in refs}):
            raise ValueError("candidate_duplicate_input_chunk")
        return refs


def encode_input_chunk(inputs):
    content = json.dumps({"inputs": inputs}, separators=(",", ":"), ensure_ascii=False).encode()
    ref = ChunkRef(sha256=sha256(content).hexdigest(), count=len(inputs), size_bytes=len(content))
    return ref, content


def publish_input_chunk(directory, ref, content, *, before_publish=None):
    """Publish an immutable chunk, reusing an existing byte-identical copy."""
    folder = safe_local_path(directory / "input-chunks")
    folder.mkdir(parents=True, exist_ok=True)
    destination = safe_local_path(folder / (ref.sha256 + ".json"))
    with TemporaryDirectory(dir=folder) as staging:
        temporary = Path(staging) / "chunk"
        temporary.write_bytes(content)
        if before_publish is not None:
            before_publish()
        try:
            os.link(safe_local_path(temporary), safe_local_path(destination))
        except FileExistsError:
            if read_bytes(destination, CHUNK_BYTES) != content:
                raise ValueError("candidate_existing_chunk_digest_mismatch") from None


@dataclass
class LoadedChunk:
    ref: ChunkRef
    state: str
    paths: tuple[str, ...] = ()


class SourceSample:
    """Detect ordinary replacements or edits during one multi-file observation."""

    def __init__(self):
        self.files = {}

    @staticmethod
    def stamp(path):
        try:
            stat = safe_local_path(path).lstat()
        except FileNotFoundError:
            return None
        return stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns

    def read(self, path, limit):
        self.files.setdefault(path, self.stamp(path))
        return read_bytes(path, limit)

    def finish(self):
        if any(self.stamp(path) != initial for path, initial in self.files.items()):
            raise ValueError("candidate_input_changed_during_sample")


def load_chunks(directory, refs, read=read_bytes):
    inputs = {}
    loaded = []
    last = None
    for ref in refs:
        try:
            content = read(directory / "input-chunks" / (ref.sha256 + ".json"), CHUNK_BYTES)
        except FileNotFoundError:
            loaded.append(LoadedChunk(ref, "missing"))
            continue
        except (OSError, ValueError):
            loaded.append(LoadedChunk(ref, "unavailable"))
            continue
        if len(content) != ref.size_bytes or sha256(content).hexdigest() != ref.sha256:
            loaded.append(LoadedChunk(ref, "stale"))
            continue
        chunk = InputChunk.model_validate(decode_object(content)).inputs
        ordered = tuple(sorted(chunk))
        if len(chunk) != ref.count:
            raise ValueError("candidate_chunk_count_mismatch")
        if last is not None and last >= ordered[0]:
            raise ValueError("candidate_chunk_order_or_duplicate")
        last = ordered[-1]
        inputs.update(chunk)
        loaded.append(LoadedChunk(ref, "verified", ordered))
    validate_input_paths(inputs)
    return inputs, loaded
