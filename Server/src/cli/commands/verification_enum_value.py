"""Compare Unity's compiled enum maps against the same retained prefab component bytes."""

import hashlib
import json
import re
from pathlib import Path
from typing import Annotated, Literal

import click
from cli.utils.play_scenario_analysis_io import read_bytes, read_json
from pydantic import BaseModel, ConfigDict, Field

Guid = Annotated[str, Field(pattern=r"^[a-f0-9]{32}$")]
Digest = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
Int32 = Annotated[int, Field(ge=-(2**31), lt=2**31)]
Identifier = Annotated[str, Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")]


class EnumRead(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    schema_id: Literal["prefab_enum_read@1"] = Field(alias="schema")
    capture_id: Guid
    unity_version: str
    prefab: str
    prefab_guid: Guid
    file_id: int
    script: str
    script_guid: Guid
    component: str
    field: Identifier
    enum_type: str
    assembly: str
    mapping: Annotated[dict[str, Int32], Field(min_length=1, max_length=4096)]
    value: Int32
    inputs: dict[str, Digest]


def load_enum(root):
    observation = EnumRead.model_validate(read_json(root / "enum.json"))
    files = {}
    for path, suffix, guid in (
        (observation.prefab, ".prefab", observation.prefab_guid),
        (observation.script, ".cs", observation.script_guid),
    ):
        if (
            not path.startswith("Assets/")
            or "/resources/gamedata/" in path.casefold()
            or not path.endswith(suffix)
            or "\\" in path
            or ":" in path
            or any(part in {"", ".", ".."} for part in path.split("/"))
        ):
            raise ValueError("Invalid archived prefab or script path")
        for name in (path, path + ".meta"):
            content = read_bytes(root / name, 4 * 1024 * 1024)
            if hashlib.sha256(content).hexdigest() != observation.inputs.get(name):
                raise ValueError("Archived bytes differ from the imported prefab observation")
            files[name] = content
        if re.findall(
            r"(?m)^guid: ([a-f0-9]{32})\r?$", files[path + ".meta"].decode("utf-8-sig")
        ) != [guid]:
            raise ValueError("Archived Unity asset GUID differs from the imported object")
    if set(files) != set(observation.inputs):
        raise ValueError("Unexpected inputs in prefab enum capture")
    yaml = files[observation.prefab].decode("utf-8-sig")
    documents = re.split(r"(?m)^--- !u!", yaml)[1:]
    prefix = f"114 &{observation.file_id}"
    components = [d.splitlines() for d in documents if d.partition("\n")[0].rstrip("\r") == prefix]
    if len(components) != 1:
        raise ValueError("The selected component file ID is absent or ambiguous in the prefab")
    fields = components[0]
    if len(fields) < 2 or fields[1] != "MonoBehaviour:":
        raise ValueError("A directly serialized MonoBehaviour is required")
    values = [
        line.removeprefix(f"  {observation.field}: ")
        for line in fields
        if line.startswith(f"  {observation.field}: ")
    ]
    script = f"  m_Script: {{fileID: 11500000, guid: {observation.script_guid}, type: 3}}"
    if (
        fields.count(script) != 1
        or len(values) != 1
        or not re.fullmatch(r"-?[0-9]+", values[0])
        or int(values[0]) != observation.value
    ):
        raise ValueError(
            "Original prefab bytes do not match the selected script, field and imported integer"
        )
    return observation


def compare_enum_values(roots):
    captures = [load_enum(root) for root in roots]
    if len({c.capture_id for c in captures}) != len(captures):
        raise ValueError("Each phase requires a fresh native prefab read")
    first = captures[0]
    identity = (
        "unity_version",
        "prefab",
        "prefab_guid",
        "file_id",
        "script",
        "script_guid",
        "component",
        "field",
        "enum_type",
        "assembly",
        "value",
    )
    for other in captures[1:]:
        if any(getattr(other, key) != getattr(first, key) for key in identity) or any(
            first.inputs[name] != other.inputs[name]
            for name in (first.prefab, first.prefab + ".meta")
        ):
            raise ValueError(
                "Select the same original prefab bytes and component field for every enum read"
            )
    meanings = [sorted(n for n, v in c.mapping.items() if v == c.value) for c in captures]
    changed = meanings[0] != meanings[1] if all(len(names) == 1 for names in meanings[:2]) else None
    return {
        **{k: getattr(first, k) for k in identity},
        "original_prefab_sha256": first.inputs[first.prefab],
        "reads": {
            phase: {
                "capture_id": c.capture_id,
                "members_for_saved_integer": names,
                "compiled_mapping": c.mapping,
                "script_sha256": c.inputs[c.script],
            }
            for phase, c, names in zip(
                ("before", "after", "repaired")[: len(roots)], captures, meanings, strict=True
            )
        },
        "meaning_changed": changed,
        "original_meaning_restored": (
            changed and meanings[0] == meanings[2]
            if len(roots) == 3 and len(meanings[2]) == 1
            else None
        ),
    }


@click.command("enum-value-diff")
@click.argument("baseline", type=click.Path(path_type=Path))
@click.argument("candidate", type=click.Path(path_type=Path))
@click.option("--repaired", type=click.Path(path_type=Path))
def enum_value_diff(baseline, candidate, repaired):
    """Compare native enum reads of the original serialized prefab component."""
    try:
        roots = [baseline, candidate] + ([repaired] if repaired else [])
        click.echo(json.dumps(compare_enum_values(roots), indent=2))
    except (ValueError, OSError) as error:
        raise click.ClickException(str(error)) from None
