"""Compare selected, native Editor compiler or texture-import observations."""

import base64
import hashlib
import json
from pathlib import Path
from typing import Annotated, Literal

import click
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, model_validator

from cli.utils.play_scenario_analysis_io import read_bytes, read_json

Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
Name = Annotated[str, Field(min_length=1, max_length=512)]


class Observation(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    schema_version: Literal[1]
    observation_revision: Digest
    project_identity: Digest
    path: Name
    target: Name
    guid: Annotated[str, Field(pattern=r"^[0-9a-f]{32}$")]
    unity_version: Name
    source_sha256: Digest


class Compiler(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    assemblies_type: Literal["Editor", "PlayerWithoutTestAssemblies"]
    included: bool
    source_included: bool
    defines: list[str] | None

    @model_validator(mode="after")
    def consistent(self):
        if (
            self.included != (self.defines is not None)
            or self.source_included
            and not self.included
        ):
            raise ValueError("inconsistent_compiler_observation")
        return self


class Assembly(Observation):
    kind: Literal["assembly"]
    assembly: Name
    source: Name
    asmdef_sha256: Digest
    conditions: dict[str, list]
    resource_versions: dict[str, str | None]
    editor_version_symbols: dict[str, bool]
    target_platforms: list[str]
    editor: Compiler
    player: Compiler

    @model_validator(mode="after")
    def contexts(self):
        if (
            self.editor.assemblies_type != "Editor"
            or self.player.assemblies_type != "PlayerWithoutTestAssemblies"
        ):
            raise ValueError("unexpected_compiler_context")
        if set(self.conditions) != {
            "includePlatforms",
            "excludePlatforms",
            "defineConstraints",
            "versionDefines",
        }:
            raise ValueError("incomplete_assembly_conditions")
        if not all(
            isinstance(v, str)
            for k in ("includePlatforms", "excludePlatforms", "defineConstraints")
            for v in self.conditions[k]
        ):
            raise ValueError("invalid_assembly_conditions")
        return self


class Reference(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    owner: Name
    target: Name


class Texture(Observation):
    kind: Literal["texture"]
    meta_sha256: Digest
    search_folder: Name
    platform: Name
    importer: dict[str, str | bool | int]
    references: list[Reference]
    owner_inputs: dict[Name, Digest | None]
    dependency_hashes: dict[str, str]
    coverage: Literal["direct_material_and_saved_scene_dependencies_in_selected_folder"]

    @model_validator(mode="after")
    def importer_fields(self):
        types = {
            "sRGBTexture": bool,
            "textureCompression": str,
            "overridden": bool,
            "platformFormat": str,
            "platformTextureCompression": str,
            "platformMaxSize": int,
            "platformCompressionQuality": int,
        }
        if set(self.importer) != set(types) or any(
            type(self.importer[k]) is not t for k, t in types.items()
        ):
            raise ValueError("invalid_texture_importer_observation")
        owners = {edge.owner for edge in self.references}
        if set(self.dependency_hashes) != owners or set(self.owner_inputs) != {
            p + s for p in owners for s in ("", ".meta")
        }:
            raise ValueError("incomplete_reference_hashes")
        return self


def changes(before, after):
    return {
        key: {"before": before.get(key), "after": after.get(key)}
        for key in sorted(before.keys() | after.keys())
        if before.get(key) != after.get(key)
    }


def fingerprint(value):
    def encode(text):
        return base64.b64encode(text.encode("utf-8")).decode("ascii")

    if isinstance(value, dict):
        return (
            "{"
            + "".join(encode(k) + ":" + fingerprint(value[k]) for k in sorted(value, key=encode))
            + "}"
        )
    if isinstance(value, list):
        return "[" + "".join(map(fingerprint, value)) + "]"
    if isinstance(value, str):
        return "s" + encode(value) + ";"
    return json.dumps(value, allow_nan=False) + ";"


def load_observation(document):
    if "success" in document:
        if document["success"] is not True:
            raise ValueError("configuration_observation_failed")
        document = document.get("data")
    model = TypeAdapter(Annotated[Assembly | Texture, Field(discriminator="kind")]).validate_python(
        document
    )
    expected = hashlib.sha256(
        fingerprint({k: v for k, v in document.items() if k != "observation_revision"}).encode(
            "ascii"
        )
    ).hexdigest()
    if model.observation_revision != expected:
        raise ValueError("configuration_observation_digest_mismatch")
    return model


def exclusions(snapshot):
    rules = snapshot.conditions
    platforms = set(snapshot.target_platforms)
    reasons = []
    if platforms:
        if rules["includePlatforms"] and not platforms.intersection(rules["includePlatforms"]):
            reasons.append({"condition": "includePlatforms", "value": rules["includePlatforms"]})
        if platforms.issubset(rules["excludePlatforms"]):
            reasons.append({"condition": "excludePlatforms", "value": rules["excludePlatforms"]})
    for condition in rules["defineConstraints"]:
        # Platform-independent package version defines are observed for the asmdef.
        # Unknown project defines and mixed positive/negative clauses remain unresolved.
        symbols = {**snapshot.editor_version_symbols, "UNITY_EDITOR": False}
        terms = [part.strip() for part in condition.split("||")]
        blocked = all(term in symbols and not symbols[term] for term in terms)
        if len(terms) == 1 and terms[0].startswith("!"):
            blocked = symbols.get(terms[0][1:]) is True
        if blocked:
            reasons.append(
                {
                    "condition": "defineConstraints",
                    "value": condition,
                    "resource_versions": snapshot.resource_versions,
                }
            )
    return reasons


class Pixel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    job_id: Annotated[str, Field(pattern=r"^[0-9a-f]{32}$")]
    scene: Name
    material: Name
    sample: Name
    method: Literal["render_texture_read_pixels"]
    device: Name
    color_space: Name
    rgba: Annotated[list[Annotated[int, Field(ge=0, le=255)]], Field(min_length=4, max_length=4)]


def read_pixel(directory, snapshot):
    report = read_json(directory / "run.json")
    request = read_json(directory / "request.json")
    process = read_json(directory / "process.json")
    diagnostics = read_json(directory / "diagnostics.json")
    reproduction = report.get("reproduction") or {}
    target = {
        "StandaloneWindows64": "WindowsPlayer",
        "StandaloneLinux64": "LinuxPlayer",
        "StandaloneOSX": "OSXPlayer",
    }.get(snapshot.target)
    if (
        not all(
            doc.get("job_id") == directory.name for doc in (report, request, process, diagnostics)
        )
        or not isinstance(reproduction, dict)
        or not target
        or snapshot.platform != "Standalone"
        or not isinstance(reproduction.get("environment"), dict)
        or reproduction["environment"].get("platform") != target
        or diagnostics.get("payload_verification") != "launcher_admission"
        or diagnostics.get("cleanup_state") != "native_finalized"
        or process.get("request_sha256")
        != hashlib.sha256(read_bytes(directory / "request.json", 64 * 1024)).hexdigest()
        or report.get("execution_environment") != "player"
        or report.get("finalization_state") != "completed"
        or report.get("status") != "succeeded"
        or process.get("process_ended") is not True
        or process.get("forced_termination") is not False
        or process.get("actual_exit_code") != 0
        or reproduction.get("build_source_revision") != snapshot.observation_revision
        or not all(
            request.get(k) and request[k] == reproduction.get(k)
            for k in ("build_id", "build_source_revision", "payload_hash", "definition_hash")
        )
    ):
        raise ValueError("player_run_does_not_match_observed_configuration")
    marker = "MCP_CONFIGURATION_PIXEL "
    pixels = [
        Pixel.model_validate_json(line[len(marker) :])
        for line in read_bytes(directory / "player.log", 4 * 1024 * 1024)
        .decode("utf-8", errors="replace")
        .splitlines()
        if line.startswith(marker)
    ]
    if (
        not pixels
        or any(p != pixels[0] for p in pixels)
        or pixels[0].device.lower() == "null"
        or pixels[0].job_id != directory.name
    ):
        raise ValueError("player_pixel_missing_or_ambiguous")
    pixel = pixels[0]
    edges = {(r.owner, r.target) for r in snapshot.references}
    if (pixel.scene, pixel.material) not in edges or (
        pixel.material,
        snapshot.path.removesuffix(".meta"),
    ) not in edges:
        raise ValueError("player_sample_outside_observed_reference_chain")
    return pixel


def compare(before, after, baseline_run=None, candidate_run=None):
    for key in ("project_identity", "kind", "target", "path", "guid"):
        if getattr(before, key) != getattr(after, key):
            raise ValueError(f"configuration_observations_differ_in_{key}")
    if isinstance(before, Assembly):
        if before.source != after.source or baseline_run or candidate_run:
            raise ValueError("incompatible_assembly_selection")
        reasons = exclusions(after) if not after.player.included else []
        return {
            "assembly": [before.assembly, after.assembly],
            "source": before.source,
            "source_unchanged": before.source_sha256 == after.source_sha256,
            "target": after.target,
            "conditions_changed": changes(before.conditions, after.conditions),
            "resource_versions_changed": changes(before.resource_versions, after.resource_versions),
            "version_symbols_changed": changes(
                before.editor_version_symbols, after.editor_version_symbols
            ),
            "compiler_inputs": {
                key: [getattr(s, key).model_dump() for s in (before, after)]
                for key in ("editor", "player")
            },
            "target_assembly_removed": before.player.included and not after.player.included,
            "target_exclusions": reasons,
            "exclusion_evidence": "matching_condition"
            if reasons
            else "unresolved"
            if not after.player.included
            else "included",
        }
    if before.platform != after.platform or before.search_folder != after.search_folder:
        raise ValueError("different_importer_platform_or_reference_scope")
    if bool(baseline_run) != bool(candidate_run):
        raise ValueError("provide_both_player_runs")
    changed = changes(before.importer, after.importer)
    inputs_changed = changes(before.owner_inputs, after.owner_inputs)
    incomplete = any(v is None for s in (before, after) for v in s.owner_inputs.values())
    result = {
        "path": after.path,
        "guid": after.guid,
        "platform": after.platform,
        "source_unchanged": before.source_sha256 == after.source_sha256,
        "meta_changed": before.meta_sha256 != after.meta_sha256,
        "owner_inputs_changed": inputs_changed,
        "only_meta_changed_in_selected_inputs": None
        if incomplete
        else (
            before.source_sha256 == after.source_sha256
            and before.meta_sha256 != after.meta_sha256
            and not inputs_changed
            and before.references == after.references
        ),
        "importer_changes": changed,
        "references": {
            side: [r.model_dump() for r in s.references]
            for side, s in (("before", before), ("after", after))
        },
        "reference_coverage": after.coverage,
        "prediction": "may_change_rendering" if changed else "no_observed_importer_field_change",
        "player_observation": {"state": "unavailable"},
    }
    if baseline_run:
        old, new = read_pixel(baseline_run, before), read_pixel(candidate_run, after)
        if old.model_dump(exclude={"rgba", "job_id"}) != new.model_dump(exclude={"rgba", "job_id"}):
            raise ValueError("different_render_sample_or_device")
        result["player_observation"] = {
            "state": "changed" if old.rgba != new.rgba else "unchanged",
            "before": old.model_dump(),
            "after": new.model_dump(),
            "job_ids": [baseline_run.name, candidate_run.name],
        }
    return result


@click.command("configuration-diff")
@click.argument("baseline", type=click.Path(path_type=Path, dir_okay=False))
@click.argument("candidate", type=click.Path(path_type=Path, dir_okay=False))
@click.option("--baseline-run", type=click.Path(path_type=Path, file_okay=False))
@click.option("--candidate-run", type=click.Path(path_type=Path, file_okay=False))
def configuration_diff(baseline, candidate, baseline_run, candidate_run):
    """Compare two native configuration observations and optional matched Player pixel runs."""
    try:
        result = compare(
            load_observation(read_json(baseline)),
            load_observation(read_json(candidate)),
            baseline_run,
            candidate_run,
        )
        click.echo(json.dumps(result, ensure_ascii=False, indent=2))
    except (ValueError, OSError) as error:
        raise click.ClickException(str(error)) from None
