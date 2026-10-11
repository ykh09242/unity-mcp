"""Strict bounded wire format shared by offline baseline consumers."""

from typing import Annotated, Literal, assert_never
from hashlib import sha256
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

Name = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")]
Digest = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
Text = Annotated[str, Field(min_length=1, max_length=4096, pattern=r"^[^\x00-\x1f\x7f]+$")]


class BaselineSchemaError(ValueError):
    """A registry violates immutable evidence or audit-chain constraints."""


class BaselineFile(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    name: Annotated[
        str,
        Field(
            pattern=r"^(request|definition|process|run|soak|client-soak)\.json$|^iterations\.jsonl$|^iteration-[0-9]{4}\.json$"
        ),
    ]
    sha256: Digest


class BaselineEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    name: Name
    directory: Text
    snapshot_file: Text
    registered_utc: Annotated[str, Field(min_length=1, max_length=64)]
    evidence_sha256: Digest
    files: Annotated[list[BaselineFile], Field(min_length=8, max_length=71)]
    provenance: dict[str, JsonValue]

    @model_validator(mode="after")
    def manifest(self) -> "BaselineEntry":
        """Require a canonical explicit snapshot and complete immutable file manifest."""
        directory = Path(self.directory)
        if (
            not directory.is_absolute()
            or str(directory.absolute()) != self.directory
            or self.snapshot_file != str(directory / "soak.json")
        ):
            raise BaselineSchemaError(
                "Baseline paths must identify an absolute root and its soak.json"
            )
        names = [file.name for file in self.files]
        required = {
            "request.json",
            "definition.json",
            "process.json",
            "run.json",
            "soak.json",
            "client-soak.json",
            "iterations.jsonl",
        }
        if names != sorted(set(names)) or not required.issubset(names):
            raise BaselineSchemaError(
                "Baseline manifest must contain sorted unique required evidence"
            )
        digest = sha256(
            "".join(file.name + ":" + file.sha256 + "\n" for file in self.files).encode()
        ).hexdigest()
        if digest != self.evidence_sha256:
            raise BaselineSchemaError("Baseline evidence digest disagrees with its manifest")
        return self


class BaselinePin(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    name: Name


class BaselineEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    revision: Annotated[int, Field(ge=1, le=4096)]
    action: Literal["register", "pin", "unpin"]
    name: Name
    previous_pins: Annotated[list[Name], Field(max_length=256)]
    pins: Annotated[list[Name], Field(max_length=256)]
    at_utc: Annotated[str, Field(min_length=1, max_length=64)]
    reason: Annotated[str, Field(min_length=1, max_length=512, pattern=r"^[^\x00-\x1f\x7f]+$")]


class BaselineRegistry(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    schema_version: Literal[1] = 1
    revision: Annotated[int, Field(ge=0, le=4096)] = 0
    entries: Annotated[list[BaselineEntry], Field(max_length=256)] = []
    pins: Annotated[list[BaselinePin], Field(max_length=256)] = []
    history: Annotated[list[BaselineEvent], Field(max_length=4096)] = []

    @model_validator(mode="after")
    def coherent(self) -> "BaselineRegistry":
        """Require unique immutable identities and replayable pin provenance."""
        names = [entry.name for entry in self.entries]
        pins = [pin.name for pin in self.pins]
        if len(set(names)) != len(names) or len(set(pins)) != len(pins):
            raise BaselineSchemaError("Registry names and pins must be unique")
        if self.revision != len(self.history):
            raise BaselineSchemaError("Registry revision must match its complete audit history")
        registered: set[str] = set()
        previous: list[str] = []
        for revision, event in enumerate(self.history, 1):
            if event.revision != revision or event.previous_pins != previous:
                raise BaselineSchemaError("Registry history has a broken revision or pin chain")
            expected = previous.copy()
            match event.action:
                case "register":
                    if event.name in registered:
                        raise BaselineSchemaError("Registry history repeats an immutable name")
                    registered.add(event.name)
                case "pin":
                    if event.name not in registered or event.name in expected:
                        raise BaselineSchemaError(
                            "Registry history pins an unknown or already pinned name"
                        )
                    expected.append(event.name)
                    expected.sort()
                case "unpin":
                    if event.name not in expected:
                        raise BaselineSchemaError("Registry history unpins an unpinned name")
                    expected.remove(event.name)
                case unreachable:
                    assert_never(unreachable)
            if event.pins != expected:
                raise BaselineSchemaError("Registry history pin outcome disagrees with its action")
            previous = event.pins
        if set(names) != registered or pins != previous:
            raise BaselineSchemaError("Registry entries or current pins disagree with history")
        return self
