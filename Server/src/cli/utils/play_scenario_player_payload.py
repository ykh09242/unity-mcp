"""Verify the exact bounded local v2 Player payload before each owned process launch."""

from hashlib import sha256
import json
import os
from pathlib import Path
import stat
import time
from typing import Annotated, Final

from models.play_scenarios import JobId, PlayScenarioCommand
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

PAYLOAD_FILE_LIMIT: Final = 1024
PAYLOAD_DIRECTORY_LIMIT: Final = 4096
PAYLOAD_BYTES_LIMIT: Final = 16 * 1024**3
PAYLOAD_MANIFEST_LIMIT: Final = 1048576


class PlayerPayloadError(RuntimeError):
    """A selected payload does not match its frozen admission fingerprint."""


class PayloadFile(BaseModel):
    """One exact regular file covered by the build payload inventory."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    path: Annotated[str, Field(strict=True, min_length=1, max_length=512)]
    size_bytes: Annotated[int, Field(strict=True, ge=0, le=PAYLOAD_BYTES_LIMIT)]
    sha256: Annotated[str, Field(strict=True, pattern=r"^[0-9a-f]{64}$")]

    @field_validator("path")
    @classmethod
    def canonical_path(cls, value: str) -> str:
        segments = value.split("/")
        if (
            len(value.encode("utf-16-le", errors="surrogatepass")) // 2 > 512
            or "\\" in value
            or any(ord(char) < 32 or 127 <= ord(char) <= 159 for char in value)
            or any(segment in ("", ".", "..") for segment in segments)
            or any(char in value for char in ':*?"<>|')
            or any(segment.endswith((".", " ")) for segment in segments)
            or any(
                segment.split(".")[0].upper()
                in {
                    "CON",
                    "PRN",
                    "AUX",
                    "NUL",
                    *[f"COM{i}" for i in range(1, 10)],
                    *[f"LPT{i}" for i in range(1, 10)],
                }
                for segment in segments
            )
            or any(segment.casefold().startswith(".env") for segment in segments)
            or any(
                [part.casefold() for part in segments[index : index + 3]]
                == ["assets", "resources", "gamedata"]
                for index in range(len(segments) - 2)
            )
        ):
            raise PlayerPayloadError(
                "Payload inventory path is outside its canonical permitted scope"
            )
        return value


class PlayerPayload(BaseModel):
    """Frozen v2 fingerprint; caller labels are separate from proof of repository cleanliness."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    build_id: JobId
    build_source_revision: Annotated[str, Field(strict=True, max_length=128)] | None
    payload_inventory: Annotated[
        list[PayloadFile], Field(min_length=1, max_length=PAYLOAD_FILE_LIMIT)
    ]
    payload_inventory_json: Annotated[str, Field(strict=True, max_length=PAYLOAD_MANIFEST_LIMIT)]
    payload_hash: Annotated[str, Field(strict=True, pattern=r"^[0-9a-f]{64}$")]

    @field_validator("build_source_revision")
    @classmethod
    def bounded_revision(cls, value: str | None) -> str | None:
        return PlayScenarioCommand.check_revision(value)

    @model_validator(mode="after")
    def canonical_inventory(self) -> "PlayerPayload":
        encoded = self.payload_inventory_json.encode("utf-8")
        if len(encoded) > PAYLOAD_MANIFEST_LIMIT:
            raise PlayerPayloadError("Payload inventory JSON exceeds its byte bound")
        names = [entry.path for entry in self.payload_inventory]
        if names != sorted(
            names, key=lambda value: value.encode("utf-16-be", errors="surrogatepass")
        ):
            raise PlayerPayloadError("Payload inventory paths are not ordinal sorted")
        paths = [entry.path.casefold() for entry in self.payload_inventory]
        if len(set(paths)) != len(paths) or "scenario-bundle.json" in paths:
            raise PlayerPayloadError(
                "Payload inventory contains duplicate or self-referencing files"
            )
        if sum(entry.size_bytes for entry in self.payload_inventory) > PAYLOAD_BYTES_LIMIT:
            raise PlayerPayloadError("Payload exceeds the bounded build byte limit")
        canonical = json.dumps(
            [entry.model_dump() for entry in self.payload_inventory],
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
        )
        if self.payload_inventory_json != canonical:
            raise PlayerPayloadError(
                "Payload inventory canonical JSON does not match its file entries"
            )
        if sha256(self.payload_inventory_json.encode("utf-8")).hexdigest() != self.payload_hash:
            raise PlayerPayloadError("Payload inventory fingerprint mismatch")
        return self


def check_deadline(deadline: float | None) -> None:
    """Stop bounded preflight work before admitting a process after its caller deadline."""
    if deadline is not None and time.monotonic() >= deadline:
        raise PlayerPayloadError("Player admission deadline expired")


def _regular_path(path: Path, root: Path) -> None:
    """Reject links/reparse points and require resolved containment before reading file bytes."""
    current = path
    while current != root:
        info = current.lstat()
        if (
            stat.S_ISLNK(info.st_mode)
            or getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
        ):
            raise PlayerPayloadError("Player payload contains a linked or reparse path")
        current = current.parent
    if not path.resolve(strict=True).is_relative_to(root):
        raise PlayerPayloadError("Player payload leaves the selected build directory")


def verify_player_payload(
    root: Path, payload: PlayerPayload, deadline: float | None = None
) -> None:
    """Stream actual file bytes and reject missing/extra files at each launch boundary."""
    check_deadline(deadline)
    actual: set[str] = set()
    pending = [root]
    directories = 1
    while pending:
        check_deadline(deadline)
        directory = pending.pop()
        with os.scandir(directory) as entries:
            for entry in entries:
                check_deadline(deadline)
                path = Path(entry.path)
                _regular_path(path, root)
                relative = path.relative_to(root).as_posix()
                if path.is_dir():
                    PayloadFile.canonical_path(relative)
                    directories += 1
                    if directories > PAYLOAD_DIRECTORY_LIMIT:
                        raise PlayerPayloadError("Player payload contains too many directories")
                    pending.append(path)
                elif path.is_file():
                    if relative == "scenario-bundle.json":
                        continue
                    PayloadFile(path=relative, size_bytes=path.stat().st_size, sha256="0" * 64)
                    actual.add(relative)
                    if len(actual) > PAYLOAD_FILE_LIMIT:
                        raise PlayerPayloadError("Player payload contains too many files")
                else:
                    raise PlayerPayloadError("Player payload contains a nonregular file")
    if actual != {entry.path for entry in payload.payload_inventory}:
        raise PlayerPayloadError("Player payload contains missing or extra files")
    for entry in payload.payload_inventory:
        check_deadline(deadline)
        path = root / entry.path
        _regular_path(path, root)
        if not path.is_file() or path.stat().st_size != entry.size_bytes:
            raise PlayerPayloadError("Player payload size mismatch")
        digest = sha256()
        total = 0
        with path.open("rb") as stream:
            while chunk := stream.read(65536):
                check_deadline(deadline)
                total += len(chunk)
                if total > entry.size_bytes:
                    raise PlayerPayloadError("Player payload changed while being verified")
                digest.update(chunk)
        if total != entry.size_bytes or digest.hexdigest() != entry.sha256:
            raise PlayerPayloadError("Player payload content fingerprint mismatch")
