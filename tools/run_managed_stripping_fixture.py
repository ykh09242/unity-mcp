"""Build, run, and verify the isolated Windows IL2CPP reflection fixture."""

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from uuid import UUID, uuid4

TOOLS = Path(__file__).resolve().parent
FIXTURE = TOOLS / "fixtures" / "managed_stripping"
PROJECT_SOURCE_PATHS = (
    "Assets/Editor/FixtureBuild.cs",
    "Assets/Runtime/ManagedStripping.Runtime.asmdef",
    "Assets/Runtime/ReflectionProbe.cs",
    "Assets/Runtime/ReflectionTarget.cs",
)
ASSEMBLY = "ManagedStripping.Runtime.dll"
TYPE = "ManagedStrippingFixture.ReflectionTarget"
METHOD = "Read"
RETURN_VALUE = "reflection-target-invoked"
MARKER = "MCP_MANAGED_STRIPPING "
PLAYER = "StrippingProbe.exe"
NATIVE_FILES = (
    PLAYER,
    "GameAssembly.dll",
    "StrippingProbe_Data/il2cpp_data/Metadata/global-metadata.dat",
)
EVIDENCE_FILES = (
    "editor.log",
    "build-report.json",
    "before-link.dll",
    "after-link.dll",
    "launch.json",
    "player.log",
)
MAX_RECORD = 1024 * 1024
MAX_LOG = 32 * MAX_RECORD
CASES = ("broken", "repaired")


class EvidenceError(ValueError):
    """An execution prerequisite or evidence check failed."""


def unity_pin() -> tuple[str, str]:
    pin = read_json(TOOLS / "unity-windows-il2cpp.json")
    return pin["unity_version"], pin["release_revision"]


def require(condition: bool, message: str) -> None:
    if not condition:
        raise EvidenceError(message)


def bounded_bytes(path: Path, limit: int) -> bytes:
    require(not path.is_symlink() and path.is_file(), f"Missing or linked file: {path}")
    with path.open("rb") as stream:
        data = stream.read(limit + 1)
    require(len(data) <= limit, f"File exceeds its evidence limit: {path}")
    return data


def read_json(path: Path) -> dict:
    return json.loads(bounded_bytes(path, MAX_RECORD))


def write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def fixture_inputs() -> tuple[dict[str, bytes], bytes, str]:
    common = {
        path: bounded_bytes(FIXTURE / Path(path).name, MAX_RECORD) for path in PROJECT_SOURCE_PATHS
    }
    common["Packages/manifest.json"] = (
        b'{"dependencies":{"com.unity.modules.jsonserialize":"1.0.0"}}\n'
    )
    version, revision = unity_pin()
    common["ProjectSettings/ProjectVersion.txt"] = (
        f"m_EditorVersion: {version}\nm_EditorVersionWithRevision: {version} ({revision})\n"
    ).encode()
    link_xml = bounded_bytes(FIXTURE / "link.xml", MAX_RECORD)
    # Git's text checkout may change line endings on Windows.
    common = {name: data.replace(b"\r\n", b"\n") for name, data in common.items()}
    inventory = {name: sha(data) for name, data in common.items()}
    return (
        common,
        link_xml.replace(b"\r\n", b"\n"),
        sha(json.dumps(inventory, sort_keys=True).encode()),
    )


def read_log_observation(path: Path) -> dict:
    lines = bounded_bytes(path, MAX_LOG).decode("utf-8-sig", errors="replace").splitlines()
    markers = [line.removeprefix(MARKER) for line in lines if line.startswith(MARKER)]
    require(len(markers) == 1, f"Expected one native probe marker in {path}")
    return json.loads(markers[0])


def inspect_metadata(pwsh: str, path: Path) -> dict:
    # Inspect metadata tables without loading or running archived managed code.
    process = subprocess.run(
        [
            pwsh,
            "-NoProfile",
            "-NonInteractive",
            "-File",
            str(FIXTURE / "ReadMetadata.ps1"),
            str(path),
        ],
        timeout=60,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    require(process.returncode == 0, f"Cannot inspect metadata in {path}: {process.stderr.strip()}")
    return json.loads(process.stdout)


def check_observation(
    record: dict, nonce: str, version: str, editor: bool, repaired: bool, guid: UUID
) -> None:
    backend = "Editor" if editor else "IL2CPP"
    invoked = editor or repaired
    require(
        record["nonce"] == nonce
        and record["unityVersion"] == version
        and record["platform"] == ("WindowsEditor" if editor else "WindowsPlayer")
        and record["backend"] == backend
        and record["typeName"] == TYPE
        and record["methodName"] == METHOD
        and record["state"] == ("invoked" if invoked else "missing_type")
        and record["value"] == (RETURN_VALUE if invoked else ""),
        f"{backend} reflection probe has an unexpected session, environment or result.",
    )
    if not editor:
        require(UUID(record["buildGuid"]) == guid, "Player probe belongs to another build.")


def check_build(report: dict, nonce: str, version: str) -> tuple[UUID, dict]:
    settings = {
        field: report[field]
        for field in (
            "unityVersion",
            "activeTarget",
            "target",
            "backend",
            "strippingLevel",
            "engineStripping",
            "scriptingDefines",
            "scene",
            "options",
        )
    }
    require(
        settings
        == {
            "unityVersion": version,
            "activeTarget": "StandaloneWindows64",
            "target": "StandaloneWindows64",
            "backend": "IL2CPP",
            "strippingLevel": "High",
            "engineStripping": report["engineStripping"],
            "scriptingDefines": "",
            "scene": "Assets/StrippingProbe.unity",
            "options": "DetailedBuildReport",
        }
        and type(settings["engineStripping"]) is bool,
        "Build report does not match Windows IL2CPP fixture settings.",
    )
    guid = UUID(report["buildGuid"])
    steps = report["nativeSteps"]
    require(
        report["nonce"] == nonce
        and guid.int != 0
        and report["result"] == "Succeeded"
        and type(report["totalErrors"]) is int
        and report["totalErrors"] == 0
        and isinstance(steps, list)
        and steps
        and all(isinstance(step, str) for step in steps),
        "Build report lacks successful native build evidence.",
    )
    return guid, settings


def check_launch(launch: dict, nonce: str, repaired: bool) -> None:
    hashes = launch["nativeFileHashes"]
    require(
        launch["nonce"] == nonce
        and type(launch["editorExit"]) is int
        and launch["editorExit"] == 0
        and type(launch["playerExit"]) is int
        and launch["playerExit"] == (0 if repaired else 42)
        and set(hashes) == set(NATIVE_FILES)
        and all(re.fullmatch(r"[a-f0-9]{64}", digest) for digest in hashes.values()),
        "Process exits or launched Player identifiers do not match the fixture.",
    )


def check_metadata(before: dict, after: dict, repaired: bool) -> None:
    require(
        before == {"probe": True, "target": True, "method": True},
        "UnityLinker input lacks the compiled probe, target or method.",
    )
    require(
        after == {"probe": True, "target": repaired, "method": repaired},
        "UnityLinker output does not show the expected stripped or retained type and method.",
    )


def case_files(repaired: bool) -> tuple[str, ...]:
    return (*EVIDENCE_FILES, "link.xml") if repaired else EVIDENCE_FILES


def case_hashes(case_root: Path, repaired: bool) -> dict[str, str]:
    require(not case_root.is_symlink(), f"Linked case directory: {case_root}")
    return {
        name: sha(bounded_bytes(case_root / name, MAX_LOG if name.endswith(".log") else MAX_RECORD))
        for name in case_files(repaired)
    }


def verify(root: Path, pwsh: str, source_revision: str | None) -> tuple[UUID, UUID]:
    manifest = read_json(root / "manifest.json")
    _, link_xml, common_hash = fixture_inputs()
    revision = manifest["sourceRevision"]
    require(
        manifest["version"] == 1
        and manifest["fixtureInputsSha256"] == common_hash
        and re.fullmatch(r"[a-f0-9]{40}", revision)
        and source_revision in (None, revision)
        and set(manifest["cases"]) == set(CASES),
        "Manifest does not match the fixture, requested source revision or required cases.",
    )
    version, _ = unity_pin()
    settings = []
    guids = []
    nonces = []
    for case in CASES:
        repaired = case == "repaired"
        case_root = root / case
        record = manifest["cases"][case]
        nonce = record["nonce"]
        require(re.fullmatch(r"[a-f0-9]{32}", nonce) is not None, f"Invalid {case} nonce.")
        require(record["files"] == case_hashes(case_root, repaired), f"Changed {case} evidence.")
        preserve = case_root / "link.xml"
        if repaired:
            require(
                bounded_bytes(preserve, MAX_RECORD) == link_xml, "Unexpected preservation rule."
            )
        else:
            require(
                not preserve.exists() and not preserve.is_symlink(),
                "Unexpected broken-case preservation.",
            )
        guid, build_settings = check_build(
            read_json(case_root / "build-report.json"), nonce, version
        )
        check_launch(read_json(case_root / "launch.json"), nonce, repaired)
        check_observation(
            read_log_observation(case_root / "editor.log"), nonce, version, True, repaired, guid
        )
        check_observation(
            read_log_observation(case_root / "player.log"), nonce, version, False, repaired, guid
        )
        check_metadata(
            inspect_metadata(pwsh, case_root / "before-link.dll"),
            inspect_metadata(pwsh, case_root / "after-link.dll"),
            repaired,
        )
        settings.append(build_settings)
        guids.append(guid)
        nonces.append(nonce)
    require(settings[0] == settings[1], "Build settings differ across the two cases.")
    require(
        guids[0] != guids[1] and nonces[0] != nonces[1], "Cases repeat the same build or execution."
    )
    return guids[0], guids[1]


def outcome(command: list[str], cwd: Path, timeout: int, description: str) -> int:
    try:
        return subprocess.run(command, cwd=cwd, timeout=timeout, check=False).returncode
    except subprocess.TimeoutExpired as error:
        raise EvidenceError(
            f"{description} exceeded {timeout} seconds. See the collected logs."
        ) from error


def run_case(
    editor: Path,
    project: Path,
    player_dir: Path,
    case_root: Path,
    nonce: str,
    version: str,
    timeout: int,
) -> None:
    probe_args = ["--strip-nonce", nonce, "--strip-type", TYPE, "--strip-method", METHOD]
    common = ["-batchmode", "-nographics"]
    editor_exit = outcome(
        [
            str(editor),
            *common,
            "-quit",
            "-projectPath",
            str(project),
            "-buildTarget",
            "Win64",
            "-executeMethod",
            "ManagedStrippingFixture.FixtureBuild.Run",
            "-logFile",
            str(case_root / "editor.log"),
            "--strip-build-output",
            str(player_dir),
            "--strip-build-report",
            str(case_root / "build-report.json"),
            "--strip-version",
            version,
            *probe_args,
        ],
        project,
        timeout,
        "Unity Editor build",
    )
    require(editor_exit == 0, f"Unity Editor exited {editor_exit}. See {case_root / 'editor.log'}.")
    check_build(read_json(case_root / "build-report.json"), nonce, version)
    bee = project / "Library" / "Bee"
    before = bee / "PlayerScriptAssemblies" / ASSEMBLY
    after = bee / "artifacts" / "WinPlayerBuildProgram" / "ManagedStripped" / ASSEMBLY
    for source, name in ((before, "before-link.dll"), (after, "after-link.dll")):
        require(
            source.is_file(),
            f"Pinned Unity linker evidence unavailable: {source.relative_to(project)}",
        )
        (case_root / name).write_bytes(bounded_bytes(source, MAX_RECORD))

    native_hashes = {}
    for name in NATIVE_FILES:
        with (player_dir / name).open("rb") as stream:
            native_hashes[name] = hashlib.file_digest(stream, "sha256").hexdigest()
    player_exit = outcome(
        [str(player_dir / PLAYER), *common, "-logFile", str(case_root / "player.log"), *probe_args],
        player_dir,
        120,
        "Native Player",
    )
    write_json(
        case_root / "launch.json",
        {
            "nonce": nonce,
            "editorExit": editor_exit,
            "playerExit": player_exit,
            "nativeFileHashes": native_hashes,
        },
    )


def run(editor: Path, root: Path, source_revision: str, timeout: int) -> None:
    require(sys.platform == "win32", "Native collection requires Windows x64.")
    require(
        editor.is_file() and editor.name.lower() == "unity.exe", "Supply the pinned Unity.exe path."
    )
    require(timeout > 0, "Editor timeout must be positive.")
    require(
        re.fullmatch(r"[a-f0-9]{40}", source_revision) is not None, "Supply a full Git revision."
    )
    root.mkdir(parents=True)
    common, link_xml, common_hash = fixture_inputs()
    version, _ = unity_pin()
    records = {}
    for case in CASES:
        case_root = root / case
        case_root.mkdir()
        nonce = uuid4().hex
        with tempfile.TemporaryDirectory(prefix="mcp-strip-") as temp:
            project = Path(temp) / "project"
            player_dir = Path(temp) / "player"
            player_dir.mkdir()
            for name, data in common.items():
                path = project / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
            if case == "repaired":
                (project / "Assets" / "link.xml").write_bytes(link_xml)
                (case_root / "link.xml").write_bytes(link_xml)
            run_case(editor, project, player_dir, case_root, nonce, version, timeout)
        records[case] = {"nonce": nonce, "files": case_hashes(case_root, case == "repaired")}
    write_json(
        root / "manifest.json",
        {
            "version": 1,
            "sourceRevision": source_revision,
            "fixtureInputsSha256": common_hash,
            "cases": records,
        },
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_subparsers(dest="mode", required=True)
    collect = modes.add_parser("run", help="Build and launch two fresh Players on Windows")
    collect.add_argument("--editor", type=Path, required=True)
    collect.add_argument("--output", type=Path, required=True)
    collect.add_argument("--editor-timeout", type=int, default=2400)
    collect.add_argument("--source-revision")
    validate = modes.add_parser(
        "verify", help="Check an existing evidence directory without launching Unity"
    )
    validate.add_argument("output", type=Path)
    validate.add_argument("--source-revision")
    args = parser.parse_args()
    try:
        pwsh = shutil.which("pwsh")
        require(
            pwsh is not None,
            "PowerShell 7 (pwsh) is required for read-only DLL metadata inspection.",
        )
        root = args.output.resolve()
        revision = args.source_revision
        if args.mode == "run":
            if revision is None:
                revision = subprocess.check_output(
                    ["git", "rev-parse", "HEAD"], cwd=TOOLS, text=True, timeout=30
                ).strip()
            run(args.editor.resolve(), root, revision, args.editor_timeout)
        broken, repaired = verify(root, pwsh, revision)
    except (
        AttributeError,
        KeyError,
        OSError,
        TypeError,
        ValueError,
        subprocess.SubprocessError,
    ) as error:
        parser.exit(1, f"Managed stripping evidence unavailable or inconclusive: {error}\n")
    print(f"Editor invoked Read in both cases. Player {broken} lacked the reflected type.")
    print(
        f"UnityLinker removed the type in the broken build. Player {repaired} invoked Read with link.xml."
    )


if __name__ == "__main__":
    main()
