"""Run selected Editor/package API shapes in fresh, isolated Unity EditMode projects."""

import argparse
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import shutil
import sys
import xml.etree.ElementTree as ET

from check_unity_test_results import check_results
from player_e2e_process import Invocation, execute
from player_e2e_artifacts import read_json as read_artifact, write_json
from prefab_crash_recovery import (
    CUTS,
    OBSERVER,
    STAGED_OBSERVER,
    admit_source,
    disk_observation,
    publish_case,
    terminate_at_cut,
    verify_reopened,
)
from unity_ci_packages import PROFILES as PACKAGE_CONFIG
from unity_ci_packages import PackageSpec, PreparationError, ROOT, prepare, read_json


SHAPES = ROOT / "tools/native_api_shapes"
ASSETS = ROOT / "TestProjects/UnityMCPTests/Assets"
TESTS = ASSETS / "Tests/EditMode"
NAMESPACE = "MCPForUnityTests.Editor.Tools"
VERSIONS = {"early-lts": "2022.3.62f3", "package": "6000.0.69f1", "integrated": "6000.6.4f1"}
CAMERA_AND_ATLAS_SUITES = ("early-lts", "package", "integrated")
CONFIG = read_json(PACKAGE_CONFIG)
NATIVE_PACKAGES = CONFIG["nativePackageSets"]


@dataclass(frozen=True)
class Profile:
    suites: tuple[str, ...]
    test_class: str
    phases: tuple[tuple[str, ...], ...]
    packages: dict[str, PackageSpec]
    fixtures: tuple[Path, ...]
    graphics: bool = False


def atlas(version: str) -> Profile:
    return Profile(
        CAMERA_AND_ATLAS_SUITES,
        "SpriteAtlasReopenTests",
        ((f"Author{version}",), (f"Reopen{version}",)),
        NATIVE_PACKAGES["sprite-atlas"],
        (SHAPES / "SpriteAtlasReopenTests.cs",),
        graphics=True,
    )


def auditor(suites: tuple[str, ...], method: str, packages: dict[str, PackageSpec]) -> Profile:
    return Profile(
        suites,
        "ProjectAuditorNativeIntegrationTests",
        ((method,),),
        packages,
        (
            TESTS / "Tools/ProjectAuditorNativeIntegrationTests.cs",
            ASSETS / "Scripts/ProjectAuditorAuditFixture.cs",
        ),
    )


def cinemachine(method: str, packages: dict[str, PackageSpec]) -> Profile:
    return Profile(
        CAMERA_AND_ATLAS_SUITES,
        "CinemachineShapeTests",
        ((method,),),
        packages,
        (SHAPES / "CinemachineShapeTests.cs", TESTS / "Helpers/PrefabTestSceneFixture.cs"),
    )


PROFILES = {
    "timeline-rename": Profile(
        ("package",),
        "TimelineAuthoringTests",
        (("HierarchyRename_RetainsDirectorBindingButLosesRelativeClipTargetDuringPlayback",),),
        NATIVE_PACKAGES["timeline-rename"],
        (TESTS / "Tools/TimelineAuthoringTests.cs", TESTS / "Helpers/PrefabTestSceneFixture.cs"),
    ),
    "managed-reference-migration": Profile(
        ("package",),
        "ManagedReferenceMigrationTests",
        (("ManagedTypeMigration_DistinguishesNullAndMissingAndRecoversOwnedPayloadAfterReopen",),),
        {},
        (
            TESTS / "Tools/ManagedReferenceMigrationTests.cs",
            ASSETS / "Scripts/TestAsmdef/SerializedInspectionAsset.cs",
        ),
    ),
    "cinemachine-2": cinemachine("LegacyProvider", NATIVE_PACKAGES["cinemachine-2"]),
    "cinemachine-3": cinemachine("ModernProvider", NATIVE_PACKAGES["cinemachine-3"]),
    "cinemachine-absent": cinemachine("AbsentProvider", {}),
    "atlas-v1": atlas("V1"),
    "atlas-v2": atlas("V2"),
    "addressables-legacy": Profile(
        ("package",),
        "AddressablesAuthoringTests",
        (
            (
                "LegacyProviderReadsSettingsAndRefusesUnavailableLayout",
                "MissingEntryWriterPreservesNativeSettingsAndUndo",
                "GuardedAddressAndGroupMovePreserveGuidLabelsAssetsAndUndo",
                "PagedEntriesGroupsProfilesPreserveNativeIdentityAndLabels",
                "PrefabRegistrationRecoversDroppedCreationResponseWithoutDuplicatingNativeWrites",
                "CompetingPrefabClientsPreserveOneCopyAndInterveningMetadataEdit",
                "PrefabRegistrationPreservesInterveningAddressEditAndReportsOutstandingStep",
                "PrefabCancellationAndDestinationCollisionPreserveExistingAssets",
                "CancellationDuringNativeImportObservesRunningAndRetainsSettledCreation",
                "ReconciliationVerifiesSavedRegistrationWhenCancellationArrivedTooLate",
            ),
            ("PrefabCheckpointSurvivesDomainReload",),
            ("ReplacementEditorRefusesPriorPrefabCheckpoint",),
            *((method,) for cut in CUTS for method in (cut.crash, cut.reopen)),
        ),
        NATIVE_PACKAGES["addressables-legacy"],
        (
            TESTS / "Tools/AddressablesAuthoringTests.cs",
            TESTS / "Tools/AddressablesReferenceFixture.cs",
            TESTS / "Services/StdioBridgeReconnectTests.Peer.cs",
            TESTS / "Helpers/PrefabTestSceneFixture.cs",
            SHAPES / "PrefabPersistenceTests.cs",
        ),
    ),
    "auditor-package": auditor(
        ("package",),
        "InstalledOfficialPackageProducesActualFixtureDiagnostic",
        NATIVE_PACKAGES["auditor-package"],
    ),
    "auditor-absent": auditor(("early-lts", "package"), "EditorWithoutAuditorRejectsStart", {}),
    "auditor-integrated": auditor(
        ("integrated",),
        "IntegratedEditorProducesActualFixtureDiagnostic",
        NATIVE_PACKAGES["auditor-integrated"],
    ),
    "auditor-integrated-no-rules": auditor(
        ("integrated",), "IntegratedEditorRequiresRulesBeforeStarting", {}
    ),
}


def fixture_sources(profile: Profile) -> dict[str, Path]:
    result = {"Assets/Tests/ShapeTests.asmdef": SHAPES / "ShapeTests.asmdef"}
    for source in profile.fixtures:
        relative = "Assets/Scripts" if source.parent == ASSETS / "Scripts" else "Assets/Tests"
        result[f"{relative}/{source.name}"] = source
    return result


def verify_lock(project: Path, dependencies: dict[str, str]) -> None:
    packages = project / "Packages"
    if json.loads((packages / "manifest.json").read_bytes())["dependencies"] != dependencies:
        raise PreparationError("Unity changed the profile's package manifest")
    locked = json.loads((packages / "packages-lock.json").read_bytes())["dependencies"]
    unexpected = [
        name
        for name in locked
        if name not in dependencies and not name.startswith("com.unity.modules.")
    ]
    if unexpected:
        raise PreparationError(f"Unexpected resolved packages: {unexpected}")
    for name, version in dependencies.items():
        entry = locked.get(name, {})
        source = "local" if version.startswith("file:") else "builtin"
        if entry.get("version") != version or entry.get("source") != source:
            raise PreparationError(f"Unexpected locked source/version for {name}: {entry}")


def phase_name(methods: tuple[str, ...]) -> str:
    """Directory name shared by native execution and artifact checks."""
    return methods[0] if len(methods) == 1 else "EditMode"


def phase_evidence(methods: tuple[str, ...]) -> tuple[str, ...]:
    common = ("launch.json", "process.log")
    if any(methods == (cut.crash,) for cut in CUTS):
        return (*common, "boundary.json", "disk.json")
    if any(methods == (cut.reopen,) for cut in CUTS):
        return (*common, "results.xml", "reopened.json")
    return (*common, "results.xml")


def run_phase(
    editor: Path,
    version: str,
    project: Path,
    directory: Path,
    profile: Profile,
    methods: tuple[str, ...],
) -> None:
    directory.mkdir()
    required = [f"{NAMESPACE}.{profile.test_class}.{method}" for method in methods]
    xml, log = directory / "results.xml", directory / "editor.log"
    graphics_args = ["-force-d3d11"] if profile.graphics and os.name == "nt" else []
    if not profile.graphics:
        graphics_args = ["-nographics"]
    command = [
        str(editor),
        "-batchmode",
        "-runTests",
        "-projectPath",
        str(project),
        "-testResults",
        str(xml),
        "-testPlatform",
        "EditMode",
        "-testFilter",
        ";".join(required),
        *graphics_args,
        "-job-worker-count",
        "2",
        "-logFile",
        str(log),
    ]
    print(f"Running {directory.name}: {', '.join(required)}", flush=True)
    for cut in CUTS:
        if methods == (cut.crash,):
            terminate_at_cut(command, directory, cut)
            verify_editor_version(log, version)
            marker = read_artifact(directory / "boundary.json", 8192)
            write_json(
                directory / "disk.json", disk_observation(project, marker, cut, intervene=True)
            )
            return
        if methods == (cut.reopen,):
            marker = read_artifact(directory.parent / cut.crash / "boundary.json", 8192)
            launch = execute(
                Invocation(
                    command,
                    directory,
                    timeout=900,
                    environment={"MCP_PREFAB_FIXTURE_RUN": marker["run_id"]},
                )
            )
            verify_phase_result(directory, version, required, launch)
            verify_reopened(project, directory, cut, launch)
            return
    launch = execute(Invocation(command, directory, timeout=15 * 60))
    verify_phase_result(directory, version, required, launch)


def verify_phase_result(directory: Path, version: str, required: list[str], launch: dict) -> None:
    """Require a clean run consisting exclusively of the selected native methods."""
    xml, log = directory / "results.xml", directory / "editor.log"
    outcome = (
        "success"
        if launch["actual_exit_code"] == 0 and not launch["forced_termination"]
        else f"exit {launch['actual_exit_code']}, forced={launch['forced_termination']}"
    )
    if check_results(xml, outcome, required):
        raise PreparationError(f"Native phase failed: {directory}")
    root = ET.parse(xml).getroot()
    cases = [case.get("fullname") for case in root.iter("test-case")]
    if (
        root.get("result") != "Passed"
        or int(root.attrib["total"]) != len(required)
        or int(root.attrib["passed"]) != len(required)
        or int(root.attrib["skipped"]) != 0
        or len(cases) != len(required)
        or set(cases) != set(required)
    ):
        raise PreparationError(f"Unexpected native test selection: {cases}")
    verify_editor_version(log, version)


def verify_editor_version(log: Path, version: str) -> None:
    with log.open(encoding="utf-8", errors="replace") as stream:
        if not re.search(
            rf"(?m)^Built from '[^'\r\n]+' branch; Version is '{re.escape(version)} \(",
            stream.read(32768),
        ):
            raise PreparationError(f"Editor startup log did not identify build {version}: {log}")


def run(
    suite: str, editor: Path, version: str, output: Path, profile_name: str | None = None
) -> None:
    if version != VERSIONS[suite]:
        raise PreparationError(f"{suite} suite requires Unity {VERSIONS[suite]}")
    selected = {
        name: profile
        for name, profile in PROFILES.items()
        if suite in profile.suites and (profile_name is None or profile_name == name)
    }
    if not selected:
        raise PreparationError(f"Profile {profile_name} is outside suite {suite}")
    editor, output = editor.resolve(strict=True), output.resolve()
    data = editor.parent / "Data"
    if not editor.is_file() or not data.is_dir():
        raise PreparationError(f"Expected Unity executable beside Editor Data: {editor}")
    module = data / "Managed/UnityEngine/UnityEditor.ProjectAuditorModule.dll"
    if module.is_file() != (suite == "integrated"):
        raise PreparationError(f"Unexpected Project Auditor Editor module for {suite}: {module}")
    if any(
        output.is_relative_to(path) for path in (editor.parent, ROOT / "MCPForUnity", ASSETS.parent)
    ):
        raise PreparationError("Output must be outside the Editor and tested source trees")
    output.mkdir(parents=True, exist_ok=False)
    overrides = CONFIG["nativePackageOverrides"].get(suite, {})
    for name, profile in selected.items():
        print(f"Preparing {name}", flush=True)
        directory = output / name
        fixtures = fixture_sources(profile)
        recovery_source = None
        source_root = ROOT
        profiles_path = PACKAGE_CONFIG
        if name == "addressables-legacy":
            recovery_source = admit_source(
                ROOT, directory / "source", fixtures, os.environ.get("GITHUB_SHA")
            )
            source_root = recovery_source.archive_root
            fixtures = {
                target: source_root / path.relative_to(ROOT) for target, path in fixtures.items()
            }
            profiles_path = source_root / PACKAGE_CONFIG.relative_to(ROOT)
            if read_json(profiles_path) != CONFIG:
                raise PreparationError(
                    "Package settings changed since the runner loaded its profiles"
                )
        prepared = prepare(
            version,
            data,
            directory / "prepared",
            extra_packages=profile.packages | overrides.get(name, {}),
            fixture_sources=fixtures,
            repo=source_root,
            profiles_path=profiles_path,
        )
        project = ROOT / prepared.project_path
        if recovery_source is not None:
            # Only this disposable package copy implements the partial persistence observer.
            shutil.copyfile(source_root / OBSERVER, project.parent / STAGED_OBSERVER)
            recovery_source.verify(project.parent)
        dependencies = json.loads((project / "Packages/manifest.json").read_bytes())["dependencies"]
        for methods in profile.phases:
            phase = phase_name(methods)
            run_phase(editor, version, project, directory / phase, profile, methods)
            verify_lock(project, dependencies)
        if recovery_source is not None:
            publish_case(directory, recovery_source)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", choices=VERSIONS, required=True)
    parser.add_argument("--profile", choices=PROFILES)
    parser.add_argument("--editor", type=Path, required=True)
    parser.add_argument("--unity-version", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        run(args.suite, args.editor, args.unity_version, args.output, args.profile)
    except (OSError, PreparationError, ValueError) as exc:
        print(f"Native package shapes failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
