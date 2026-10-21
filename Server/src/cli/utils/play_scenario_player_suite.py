"""One verified frozen build, ordered children and a fresh owned Player process per child."""

from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path
import time
from typing import Annotated, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator, model_validator
from models.play_scenarios import (
    JobId,
    PlayScenario,
    PlayScenarioCommand,
    ScenarioName,
    normalize_native_definition,
)
from cli.utils.play_scenario_player import (
    PlayerBundle,
    PlayerLaunchError,
    PlayerRunOptions,
    _read_json,
    run_player,
)
from cli.utils.play_scenario_player_payload import (
    PlayerPayload,
    PlayerPayloadError,
    PAYLOAD_MANIFEST_LIMIT,
    verify_player_payload,
)
from cli.utils.play_scenario_player_platform import Backend, BuildTarget, EXECUTABLES
from cli.utils.play_scenario_reports import write_player_artifacts, write_suite_artifacts


def canonical(value: JsonValue) -> str:
    """Match the native canonical manifest identity bytes."""
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


class FrozenChild(BaseModel):
    """Keep exact authored JSON separate from its validated execution model."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    scenario_name: ScenarioName
    definition_hash: Annotated[str, Field(strict=True, pattern=r"^[0-9a-f]{64}$")]
    definition_json: Annotated[str, Field(strict=True, max_length=65536)]
    definition: dict[str, JsonValue]


class PlayerSuiteBundle(PlayerPayload):
    """Version three freezes every child and the exact union in a single verified payload."""

    schema_version: Annotated[int, Field(strict=True, ge=3, le=3)]
    suite_name: ScenarioName
    suite_hash: Annotated[str, Field(strict=True, pattern=r"^[0-9a-f]{64}$")]
    failure_policy: Literal["stop", "continue"]
    isolation: Literal["fresh-process"]
    children: Annotated[list[FrozenChild], Field(min_length=1, max_length=16)]
    scene_paths: Annotated[
        list[Annotated[str, Field(strict=True, max_length=4096)]],
        Field(min_length=1, max_length=64),
    ]
    executable: Annotated[str, Field(strict=True, max_length=128)]
    build_target: BuildTarget = "windows64"
    scripting_backend: Backend = "mono"
    unity_version: Annotated[str, Field(strict=True, min_length=1, max_length=128)]
    package_version: Annotated[str, Field(strict=True, min_length=1, max_length=128)]

    @model_validator(mode="after")
    def frozen_identity(self) -> "PlayerSuiteBundle":
        """Admit only unchanged, distinct concrete children and their exact sorted scene union."""
        if self._identity_hash() != self.suite_hash:
            raise PlayerLaunchError("Player suite frozen identity mismatch")
        names = [child.scenario_name for child in self.children]
        if len(set(names)) != len(names) or self.executable != EXECUTABLES[self.build_target]:
            raise PlayerLaunchError("Player suite child names or executable are invalid")
        scenes: set[str] = set()
        for child in self.children:
            parsed = PlayerBundle.model_validate(self.child_manifest(child.scenario_name))
            scenes.update(parsed.scene_paths)
            if any(
                key in child.definition for key in ("parameters", "parameter_bindings", "datasets")
            ):
                raise PlayerLaunchError(
                    "Frozen Player children must be materialized before execution"
                )
        if self.scene_paths != sorted(
            scenes, key=lambda path: path.encode("utf-16-be", errors="surrogatepass")
        ):
            raise PlayerLaunchError("Player suite scene inventory does not match the exact union")
        return self

    def _identity_hash(self) -> str:
        """Hash exact frozen JSON strings without reserializing their numeric or Unicode contents."""
        fields = [self.suite_name, self.failure_policy, self.isolation, str(len(self.children))]
        for child in self.children:
            fields.extend((child.scenario_name, child.definition_hash, child.definition_json))
        identity = bytearray(b"MCPForUnity.PlayerSuite/3\n")
        for value in fields:
            encoded = value.encode("utf-8")
            identity.extend(str(len(encoded)).encode("ascii") + b":" + encoded)
        return sha256(identity).hexdigest()

    def child_manifest(self, name: str) -> dict[str, JsonValue]:
        """Project a selected frozen child onto the existing version-two process contract."""
        child = next((item for item in self.children if item.scenario_name == name), None)
        if child is None:
            raise PlayerLaunchError("Player suite child is not frozen in this build")
        definition = PlayScenario.model_validate(normalize_native_definition(child.definition))
        steps = definition.setup_steps + definition.steps + definition.cleanup_steps
        common = self.model_dump(
            mode="json",
            include={
                "build_id",
                "build_source_revision",
                "payload_inventory",
                "payload_inventory_json",
                "payload_hash",
                "executable",
                "unity_version",
                "package_version",
            },
        )
        for field in ("build_target", "scripting_backend"):
            if field in self.model_fields_set:
                common[field] = getattr(self, field)
        return {
            **common,
            **child.model_dump(mode="json"),
            "schema_version": 2,
            "scene_paths": sorted({step.scene for step in steps if step.scene is not None}),
        }


def select_child(
    raw: dict[str, JsonValue], name: str, manifest_hash: str | None
) -> tuple[dict[str, JsonValue], dict[str, JsonValue]]:
    """Refuse manifest drift before handing one selected child to ordinary process admission."""
    if manifest_hash is None or sha256(canonical(raw).encode("utf-8")).hexdigest() != manifest_hash:
        raise PlayerLaunchError("Player suite manifest changed after suite admission")
    bundle = PlayerSuiteBundle.model_validate(raw)
    selected = bundle.child_manifest(name)
    index = next(
        index for index, child in enumerate(bundle.children) if child.scenario_name == name
    )
    return selected, {
        "name": bundle.suite_name,
        "suite_hash": bundle.suite_hash,
        "child_index": index,
        "isolation": "fresh-process",
    }


class PlayerSuiteOptions(BaseModel):
    """Bounded sequential launch policy; shared-process state reuse is intentionally unsupported."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    build_directory: Path
    output_directory: Path
    isolation: Literal["fresh-process"] = "fresh-process"
    failure_policy: Literal["stop", "continue"] | None = None
    timeout_seconds: Annotated[int, Field(strict=True, ge=1, le=86400)] = 300
    max_runtime_seconds: Annotated[int, Field(strict=True, ge=1, le=86400)] = 3600
    cleanup_wait_seconds: Annotated[int, Field(strict=True, ge=1, le=3600)] = 1860
    graphical: Annotated[bool, Field(strict=True)] = False
    source_revision: Annotated[str, Field(strict=True, max_length=128)] | None = None
    expected_build_revision: Annotated[str, Field(strict=True, max_length=128)] | None = None
    expected_build_id: JobId | None = None

    @field_validator("source_revision", "expected_build_revision")
    @classmethod
    def revision(cls, value: str | None) -> str | None:
        """Keep caller reproduction labels distinct from native payload evidence."""
        return PlayScenarioCommand.check_revision(value)


class PlayerSuiteBuildRequest(BaseModel):
    """Dedicated native build action, separate from Editor suite execution requests."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    action: Literal["player_suite_build"] = "player_suite_build"
    name: ScenarioName
    output_directory: Annotated[str, Field(strict=True, min_length=1, max_length=4096)]
    build_target: BuildTarget = "windows64"
    scripting_backend: Backend = "mono"
    isolation: Literal["fresh-process"] = "fresh-process"
    failure_policy: Literal["stop", "continue"] | None = None
    source_revision: Annotated[str, Field(strict=True, max_length=128)] | None = None

    @field_validator("source_revision")
    @classmethod
    def revision(cls, value: str | None) -> str | None:
        """Validate the caller build label before native build dispatch."""
        return PlayScenarioCommand.check_revision(value)


@dataclass(frozen=True, slots=True)
class PlayerSuiteOutcome:
    """Launcher aggregate references actual child reports and explicit unstarted skips."""

    report: dict[str, JsonValue]
    directory: Path
    exit_code: int


def run_player_suite(options: PlayerSuiteOptions) -> PlayerSuiteOutcome:
    """Launch each frozen child once, wait for ownership cleanup and export child plus suite JUnit."""
    root = options.build_directory.resolve(strict=True)
    raw = _read_json(root / "scenario-bundle.json", PAYLOAD_MANIFEST_LIMIT)
    bundle = PlayerSuiteBundle.model_validate(raw)
    if options.expected_build_id is not None and options.expected_build_id != bundle.build_id:
        raise PlayerLaunchError("Player suite build ID does not match the expected ID")
    if (
        options.expected_build_revision is not None
        and options.expected_build_revision != bundle.build_source_revision
    ):
        raise PlayerLaunchError("Player suite build revision does not match the expected label")
    if options.output_directory.resolve().is_relative_to(root):
        raise PlayerLaunchError("Player suite output must be outside the immutable build root")
    deadline = time.monotonic() + options.max_runtime_seconds
    verify_player_payload(root, bundle, deadline)
    manifest_hash = sha256(canonical(raw).encode("utf-8")).hexdigest()
    suite_id = uuid4().hex
    directory = options.output_directory.resolve() / suite_id
    directory.mkdir(parents=True, exist_ok=False)
    entries: list[dict[str, JsonValue]] = [
        {"name": child.scenario_name, "definition_hash": child.definition_hash, "status": "pending"}
        for child in bundle.children
    ]
    report: dict[str, JsonValue] = {
        "suite_id": suite_id,
        "suite": {"name": bundle.suite_name},
        "status": "running",
        "started_unix_ms": int(time.time() * 1000),
        "scenarios": entries,
        "source_revision": options.source_revision,
        "repeat_count": 1,
        "timeout_seconds": options.timeout_seconds,
        "metadata": {
            "suite_hash": bundle.suite_hash,
            "build_id": bundle.build_id,
            "payload_hash": bundle.payload_hash,
            "isolation": "fresh-process",
            "failure_policy": options.failure_policy or bundle.failure_policy,
        },
    }
    stop_reason = None
    cancelled = False
    timed_out = False
    interrupted = False
    policy = options.failure_policy or bundle.failure_policy
    for entry in entries:
        if time.monotonic() >= deadline:
            stop_reason = stop_reason or "Player suite whole-run deadline expired"
            timed_out = True
        if stop_reason is not None:
            entry.update(status="skipped", skip_reason=stop_reason)
            continue
        entry["job_id"] = uuid4().hex
        try:
            outcome = run_player(
                PlayerRunOptions(
                    build_directory=root,
                    output_directory=directory,
                    timeout_seconds=options.timeout_seconds,
                    cleanup_wait_seconds=options.cleanup_wait_seconds,
                    source_revision=options.source_revision,
                    expected_build_id=bundle.build_id,
                    expected_build_revision=bundle.build_source_revision,
                    require_verified_payload=True,
                    graphical=options.graphical,
                    deadline_monotonic=deadline,
                    player_job_id=entry["job_id"],
                    suite_child_name=entry["name"],
                    suite_manifest_hash=manifest_hash,
                )
            )
            entry.update(
                status=outcome.report["status"]
                if outcome.exit_code == 0 or outcome.report["status"] in {"cancelled", "timed_out"}
                else "failed",
                report=outcome.report,
                report_directory=outcome.directory.name,
            )
            infrastructure_error = outcome.client_error or outcome.report.get("report_error")
            write_player_artifacts(outcome.report, outcome.directory, infrastructure_error)
            if infrastructure_error:
                entry["client_error"] = infrastructure_error
                report["client_error"] = infrastructure_error[:2048]
                stop_reason = "Player suite stopped after an infrastructure failure"
            if outcome.exit_code == 130 or outcome.report["status"] == "cancelled":
                cancelled = True
                interrupted = outcome.exit_code == 130
                stop_reason = "Player suite cancelled; remaining children were not launched"
            elif outcome.exit_code != 0 and policy == "stop":
                stop_reason = "Player suite stopped after an unsuccessful child"
        except KeyboardInterrupt:
            if "report" not in entry:
                entry.update(
                    status="cancelled",
                    client_error="Interrupted without an admitted native child report",
                )
                report["client_error"] = (
                    "Player suite interrupted without an admitted native child report"
                )
            cancelled = interrupted = True
            stop_reason = "Player suite interrupted; remaining children were not launched"
        except (PlayerLaunchError, PlayerPayloadError, OSError, ValueError) as error:
            entry.update(status="failed", client_error=str(error)[:2048])
            report["client_error"] = (
                "Player suite child admission, process evidence or artifact export failed"
            )
            stop_reason = "Player suite stopped after an infrastructure failure"
    report["status"] = (
        "cancelled"
        if cancelled
        else "timed_out"
        if timed_out
        else "failed"
        if any(entry["status"] != "succeeded" for entry in entries)
        else "succeeded"
    )
    report["finished_unix_ms"] = int(time.time() * 1000)
    write_suite_artifacts(report, directory)
    return PlayerSuiteOutcome(
        report, directory, 130 if interrupted else 0 if report["status"] == "succeeded" else 1
    )
