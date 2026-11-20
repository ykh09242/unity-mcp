"""Require an owned Editor death and independent disk evidence for each prefab persistence cut."""

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
import subprocess
from tempfile import TemporaryFile
from uuid import uuid4
from zipfile import ZipFile

from player_e2e_artifacts import digest, linked, read_json, write_json
from player_e2e_process import Invocation, execute


@dataclass(frozen=True)
class Cut:
    crash: str
    reopen: str
    boundary: str
    phase: str
    entry_count: int


CUTS = (
    Cut("CrashAfterPrefabSave", "ReopenAfterPrefabSave", "prefab_saved", "creating", 0),
    Cut("CrashAfterCreationJournal", "ReopenAfterCreationJournal", "created", "created", 0),
    Cut(
        "CrashAfterRegistrationSave",
        "ReopenAfterRegistrationSave",
        "registration_saved",
        "registering",
        1,
    ),
)

RECOVERY_RUNNER_INPUTS = (
    "tools/run_native_package_shapes.py",
    "tools/prefab_crash_recovery.py",
    "tools/check_unity_test_results.py",
    "tools/player_e2e_artifacts.py",
    "tools/player_e2e_process.py",
    "tools/unity_ci_packages.py",
    "tools/unity-ci-packages.json",
    "tools/unity-registry-pins.json",
)
OBSERVER = "tools/native_api_shapes/PrefabPersistenceObserver.cs"
STAGED_OBSERVER = "package/Editor/Tools/Addressables/PrefabPersistenceObserver.cs"


def checkout_identity(repository: Path, paths: tuple[str, ...]) -> str:
    commit = subprocess.check_output(
        ["git", "rev-parse", "--verify", "HEAD^{commit}"], cwd=repository, text=True
    ).strip()
    dirty = subprocess.check_output(
        ["git", "status", "--porcelain=v1", "-z", "--untracked-files=all", "--", *paths],
        cwd=repository,
    )
    if not re.fullmatch("[0-9a-f]{40}", commit) or dirty:
        raise ValueError(
            "Native recovery requires clean, committed package, fixture and runner inputs"
        )
    return commit


@dataclass(frozen=True)
class RecoverySource:
    repository: Path
    archive_root: Path
    paths: tuple[str, ...]
    commit: str
    staged_hashes: dict[str, str]

    def verify(self, prepared: Path) -> None:
        if checkout_identity(self.repository, self.paths) != self.commit:
            raise ValueError("Native recovery checkout changed after admission")
        for name, expected in self.staged_hashes.items():
            path = prepared / name
            if not path.is_file() or digest(path) != expected:
                raise ValueError(f"Prepared native recovery source changed: {name}")
        for root in (prepared / "package", prepared / "project/Assets"):
            for path in root.rglob("*"):
                if (
                    not path.is_file()
                    or path.relative_to(prepared).as_posix() in self.staged_hashes
                ):
                    continue
                compiler_input = path.suffix.lower() in {
                    ".cs",
                    ".asmdef",
                    ".asmref",
                    ".rsp",
                    ".dll",
                }
                if compiler_input or (root.name == "package" and path.suffix != ".meta"):
                    raise ValueError(f"Unexpected native recovery input: {path.name}")


def admit_source(
    repository: Path, directory: Path, fixtures: dict[str, Path], expected_sha: str | None = None
) -> RecoverySource:
    """Prepare the package and fixtures exclusively from the commit admitted before launch."""
    staged = {
        path.relative_to(repository).as_posix(): "project/" + target
        for target, path in fixtures.items()
    }
    staged[OBSERVER] = STAGED_OBSERVER
    paths = ("MCPForUnity", *staged, *RECOVERY_RUNNER_INPUTS)
    commit = checkout_identity(repository, paths)
    if expected_sha is not None and expected_sha != commit:
        raise ValueError("Native recovery checkout differs from the requested source revision")
    directory.mkdir(parents=True, exist_ok=False)
    hashes = {}
    with TemporaryFile() as buffer:
        subprocess.run(
            ["git", "archive", "--format=zip", commit, *paths],
            cwd=repository,
            stdout=buffer,
            check=True,
            timeout=120,
        )
        buffer.seek(0)
        with ZipFile(buffer) as archive:
            for member in archive.infolist():
                if member.is_dir():
                    continue
                name = member.filename
                if (
                    name not in staged
                    and name not in RECOVERY_RUNNER_INPUTS
                    and not name.startswith("MCPForUnity/")
                ):
                    raise ValueError("Unexpected file in native recovery source archive")
                path = Path(archive.extract(member, directory))
                target = (
                    "package/" + name.removeprefix("MCPForUnity/")
                    if name.startswith("MCPForUnity/")
                    else staged.get(name)
                )
                if target:
                    hashes[target] = digest(path)
    return RecoverySource(repository, directory, paths, commit, hashes)


def terminate_at_cut(command: list[str], directory: Path, cut: Cut, *, timeout: int = 900) -> dict:
    """Stop only the launched child after its own atomic marker is visible."""
    run_id = uuid4().hex
    marker_path = directory / "boundary.json"
    reached = False

    def matches(pid: int) -> bool:
        nonlocal reached
        if not marker_path.exists():
            return False
        marker = read_json(marker_path, 8192)
        if (marker.get("run_id"), marker.get("boundary"), marker.get("process_id")) != (
            run_id,
            cut.boundary,
            pid,
        ):
            raise ValueError("Persistence boundary did not identify this run, step and owned PID")
        reached = True
        return True

    launch = execute(
        Invocation(
            command,
            directory,
            timeout=timeout,
            environment={"MCP_PREFAB_FIXTURE_RUN": run_id},
            stop_at_boundary=matches,
        )
    )
    if (
        not reached
        or not launch["process_ended"]
        or not launch["forced_termination"]
        or launch["actual_exit_code"] == 0
        or (directory / "results.xml").exists()
    ):
        raise ValueError(f"Expected a process death at {cut.boundary}, see {directory}")
    return launch


def owned_file(project: Path, relative: str) -> Path:
    """Accept only a normal file in the prepared project's fixture-owned asset tree."""
    path = project / relative
    if not path.resolve().is_relative_to(project / "Assets") or not path.is_file():
        raise ValueError("Expected a regular fixture file under Assets")
    if linked(path) or any(
        linked(parent)
        for parent in path.parents
        if parent != project and parent.is_relative_to(project)
    ):
        raise ValueError("Refusing linked fixture asset")
    if path.stat().st_size > 1024 * 1024:
        raise ValueError("Fixture asset exceeds 1 MiB")
    return path


def meta_guid(project: Path, relative: str) -> str:
    matches = re.findall(
        rb"(?m)^guid: ([0-9a-f]{32})\r?$", owned_file(project, relative + ".meta").read_bytes()
    )
    if len(matches) != 1:
        raise ValueError("Missing or ambiguous disk asset GUID")
    return matches[0].decode("ascii")


def disk_observation(project: Path, marker: dict, cut: Cut, *, intervene: bool) -> dict:
    """Inspect the legacy provider's actual serialized bytes with the Editor closed."""
    project = project.resolve(strict=True)
    root, group = marker["root"], marker["group"]
    if (
        not re.fullmatch(r"Assets/__McpAddressables_[0-9a-f]{32}", root)
        or marker["prefab"] != root + "/Result.prefab"
        or marker["settings"] != root + "/Config/Settings.asset"
        or group != root + "/Config/AssetGroups/OwnedA.asset"
        or meta_guid(project, root) != marker["root_guid"]
    ):
        raise ValueError("Fixture root, group or prefab ownership changed")
    guid = meta_guid(project, marker["prefab"])
    prefab = owned_file(project, marker["prefab"])
    if guid == meta_guid(project, root + "/Template.prefab") or list(
        prefab.parent.glob("Result*.prefab")
    ) != [prefab]:
        raise ValueError("Expected exactly one new prefab with its own disk GUID")
    group_path = owned_file(project, group)
    content = group_path.read_bytes()
    entry_count = len(re.findall(rb"(?m)^\s*- m_GUID: " + guid.encode() + rb"\r?$", content))
    if entry_count != cut.entry_count:
        raise ValueError(f"Wrong saved group entry count at {cut.boundary}: {entry_count}")
    # The external edit targets an existing serialized entry while the original Editor is dead.
    original = marker["prefab"] if entry_count else "owned/first"
    edited = "manual/" + marker["run_id"]
    expected_guid = guid if entry_count else marker["user_entry_guid"]
    if not re.fullmatch("[0-9a-f]{32}", expected_guid) or not re.fullmatch(
        "[0-9a-f]{32}", marker["run_id"]
    ):
        raise ValueError("Invalid fixture entry identity")
    entry = re.compile(
        rb"(?m)^(\s*- m_GUID: "
        + expected_guid.encode()
        + rb"\r?\n[ \t]+m_Address: )"
        + re.escape((original if intervene else edited).encode())
        + rb"(\r?)$"
    )
    if len(entry.findall(content)) != 1:
        raise ValueError("Expected exactly one owned entry with the independently observed address")
    if intervene:
        content = entry.sub(lambda match: match[1] + edited.encode() + match[2], content)
        group_path.write_bytes(content)
    return {
        "guid": guid,
        "entry_count": entry_count,
        "user_address": edited,
        "group_sha256": hashlib.sha256(content).hexdigest(),
        "prefab_sha256": hashlib.sha256(prefab.read_bytes()).hexdigest(),
    }


def verify_reopened(project: Path, directory: Path, cut: Cut, launch: dict) -> dict:
    crash_directory = directory.parent / cut.crash
    marker = read_json(crash_directory / "boundary.json", 8192)
    original = read_json(crash_directory / "disk.json", 8192)
    current = disk_observation(project, marker, cut, intervene=False)
    observed = read_json(directory / "reopened.json", 8192)
    if (
        original != current
        or any(
            observed.get(field) != current[field]
            for field in ("guid", "entry_count", "user_address")
        )
        or observed.get("run_id") != marker["run_id"]
        or observed.get("process_id") != launch["process_id"]
        or not observed.get("session")
        or observed["session"] == marker["session"]
        or observed.get("recorded_phase") != cut.phase
        or observed.get("state") != ("conflict" if cut.phase == "created" else "unknown")
    ):
        raise ValueError(f"Reopened native and disk evidence differ at {cut.boundary}")
    return observed


def publish_case(profile_directory: Path, source: RecoverySource) -> None:
    source.verify(profile_directory / "prepared")
    cuts = {}
    for cut in CUTS:
        owner = read_json(profile_directory / cut.crash / "launch.json")
        observer = read_json(profile_directory / cut.reopen / "reopened.json", 8192)
        cuts[cut.boundary] = {
            "terminated_pid": owner["process_id"],
            "exit_code": owner["actual_exit_code"],
            "forced_termination": owner["forced_termination"],
            "disk": read_json(profile_directory / cut.crash / "disk.json", 8192),
            **observer,
        }
    write_json(
        profile_directory / "process-death.json",
        {
            "schema": 1,
            "native": True,
            "source_sha": source.commit,
            "staged_inputs_sha256": hashlib.sha256(
                json.dumps(sorted(source.staged_hashes.items())).encode()
            ).hexdigest(),
            "cuts": cuts,
        },
    )
