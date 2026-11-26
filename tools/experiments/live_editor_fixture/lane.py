"""Provision and address an owned, offline, disposable Unity Editor lane on Windows."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import time
import uuid
import wave
import zipfile
from pathlib import Path


SOURCE = Path(__file__).resolve().parent
ROOT = SOURCE.parents[2]
WORK = Path(os.environ.get("LIVE_AGENT_TRIAL_DIR", ROOT / ".work/live-agent-20261125")).resolve()
SEED = Path(
    os.environ.get(
        "LIVE_AGENT_SEED_PROJECT", ROOT / ".work/discovery-live-20261122/DiscoveryFixture"
    )
).resolve()
EDITOR = Path(
    os.environ.get(
        "LIVE_AGENT_UNITY_EDITOR", "C:/Program Files/Unity/Hub/Editor/6000.0.69f1/Editor"
    )
).resolve()
if EDITOR.name.lower() == "unity.exe":
    EDITOR = EDITOR.parent
CACHE = Path(
    os.environ.get(
        "LIVE_AGENT_UNITY_CACHE",
        Path(os.environ.get("LOCALAPPDATA", Path.home() / ".cache")) / "Unity/cache",
    )
).resolve()
RECORDED_REVISION = "3973b243680eede8639343694edcb075c060b3e5"
REVISION = os.environ.get("LIVE_AGENT_PRODUCT_REVISION", RECORDED_REVISION).lower()
if re.fullmatch(r"[0-9a-f]{40}", REVISION) is None:
    raise ValueError("LIVE_AGENT_PRODUCT_REVISION must be a full Git commit SHA")
UNITY_VERSION = "6000.0.69f1"
PRODUCT_PACKAGE = "com.ykh09242.unity-mcp"
SEED_PACKAGES = {"com.unity.editorcoroutines": "1.0.1", "com.unity.nuget.newtonsoft-json": "3.2.2"}


def dump(path, obj):
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def require_file(path, setting):
    if not path.is_file():
        raise FileNotFoundError(f"Missing {path}. Check {setting}.")
    return path


def real_child(path):
    if path.absolute() != path.resolve() or not path.resolve().is_relative_to(WORK):
        raise ValueError(f"Refusing redirected path outside the owned layout: {path}")
    if path.is_file() and path.stat().st_nlink > 1:
        raise ValueError(f"Refusing a hard-linked output: {path}")
    return path


def lane_marker(lane):
    real_child(lane)
    if lane.parent != WORK or lane.name not in {f"lane-{i}" for i in range(4)}:
        raise ValueError(f"Invalid lane path under {WORK}: {lane}")
    index = int(lane.name[-1])
    expected = real_child(lane / "project")
    marker = require_file(real_child(lane / "owned-lane.json"), "the lane ownership marker")
    receipt = json.loads(marker.read_bytes())
    if (
        type(receipt.get("lane")) is not int
        or receipt["lane"] != index
        or receipt.get("revision") not in (REVISION, REVISION[:9])
        or Path(receipt.get("project", "")).absolute() != expected
    ):
        raise ValueError(f"Lane ownership mismatch: {lane}")
    for name in ["control", "status"]:
        real_child(lane / name)


def check_work_root():
    if (
        WORK == ROOT
        or ROOT.is_relative_to(WORK)
        or WORK.is_relative_to(SEED)
        or SEED.is_relative_to(WORK)
    ):
        raise ValueError(
            "The trial directory must be dedicated and separate from the seed project."
        )
    marker = real_child(WORK / "fixture-run.json")
    if marker.is_file():
        if json.loads(marker.read_bytes()) != {"revision": REVISION, "directory": str(WORK)}:
            raise ValueError(f"Trial directory ownership mismatch: {WORK}")
        return
    if WORK.exists() and any(WORK.iterdir()):
        # Retain compatibility with the recorded run, which predates a run-level marker.
        if WORK != ROOT / ".work/live-agent-20261125":
            raise ValueError(f"Choose a new or empty LIVE_AGENT_TRIAL_DIR: {WORK}")
        lane_marker(WORK / "lane-0")
    WORK.mkdir(parents=True, exist_ok=True)
    with marker.open("x", encoding="utf-8") as stream:
        json.dump({"revision": REVISION, "directory": str(WORK)}, stream)


def seed_package(name):
    version = SEED_PACKAGES[name]
    folder = SEED / "Library/PackageCache"
    matches = []
    for metadata in sorted(folder.glob(f"{name}@*/package.json")):
        entry = json.loads(metadata.read_bytes())
        if entry.get("name") == name and entry.get("version") == version:
            matches.append(metadata.parent)
    if len(matches) != 1:
        raise ValueError(
            f"Expected one resolved {name}@{version} in {folder}; found {len(matches)}. Check LIVE_AGENT_SEED_PROJECT."
        )
    return matches[0]


def seed_manifest():
    settings = SEED / "ProjectSettings"
    version = require_file(settings / "ProjectVersion.txt", "LIVE_AGENT_SEED_PROJECT").read_text()
    if f"m_EditorVersion: {UNITY_VERSION}" not in version.splitlines():
        raise ValueError(
            f"Expected seed editor version {UNITY_VERSION} in {settings / 'ProjectVersion.txt'}"
        )
    require_file(settings / "ProjectSettings.asset", "LIVE_AGENT_SEED_PROJECT")
    manifest = require_file(SEED / "Packages/manifest.json", "LIVE_AGENT_SEED_PROJECT")
    data = json.loads(manifest.read_bytes())
    if not isinstance(data.get("dependencies"), dict):
        raise ValueError(f"Missing dependencies object in {manifest}")
    required = {
        f"com.unity.modules.{module}"
        for module in ("ai", "audio", "physics", "terrain", "terrainphysics")
    }
    if missing := required.difference(data["dependencies"]):
        raise ValueError(
            f"Missing seed built-in dependencies in {manifest}: {', '.join(sorted(missing))}"
        )
    return {
        "dependencies": {
            k: v
            for k, v in data["dependencies"].items()
            if k.startswith("com.unity.modules.") or k == PRODUCT_PACKAGE
        }
    }


def request(lane: Path, op: str, task="", language="", seed=0, timeout=120):
    lane = Path(lane).absolute()
    lane_marker(lane)
    control = lane / "control"
    rid = uuid.uuid4().hex
    packet = dict(id=rid, op=op, task=task, language=language, seed=seed)
    temp = control / (rid + ".tmp")
    dump(temp, packet)
    pending = real_child(control / "request.json")
    if pending.exists():
        raise RuntimeError("Lane already has a pending request")
    temp.rename(pending)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        failure = control / "startup-error.txt"
        if failure.exists():
            raise RuntimeError(failure.read_text())
        try:
            reply = json.loads((control / "response.json").read_bytes())
            if reply["id"] == rid:
                evidence = lane.parent / "registry-evidence.json"
                if task == "installed_package" and reply.get("ok") and evidence.is_file():
                    observed = reply["state"]["targetPackage"]
                    registered = json.loads(evidence.read_bytes())
                    entry = registered["packages"][observed["name"]]
                    if (
                        observed["source"] != "Registry"
                        or observed["registry"] != registered["installedRegistry"]
                        or any(
                            observed[k] != entry[k]
                            for k in ("name", "version", "displayName", "description")
                        )
                    ):
                        raise RuntimeError(
                            "Native installed record differs from the offline registry evidence"
                        )
                    reply["state"]["registryRecord"] = entry
                return reply
        except (FileNotFoundError, PermissionError):
            pass
        time.sleep(0.1)
    raise TimeoutError(f"{op} timed out for {lane.name} ({rid})")


def compile_dll(name):
    out = real_child(WORK / "bin")
    out.mkdir(parents=True, exist_ok=True)
    data = EDITOR / "Data"
    runtime = require_file(data / "NetCoreRuntime/dotnet.exe", "LIVE_AGENT_UNITY_EDITOR")
    compiler = require_file(data / "DotNetSdkRoslyn/csc.dll", "LIVE_AGENT_UNITY_EDITOR")
    unity = data / "Managed/UnityEngine"
    refs = [
        require_file(data / "NetStandard/ref/2.1.0/netstandard.dll", "LIVE_AGENT_UNITY_EDITOR"),
        *unity.glob("UnityEngine*.dll"),
        *unity.glob("UnityEditor*.dll"),
        require_file(
            seed_package("com.unity.nuget.newtonsoft-json") / "Runtime/Newtonsoft.Json.dll",
            "LIVE_AGENT_SEED_PROJECT",
        ),
    ]
    if name in {"World", "IsolatedToolPreferences"}:
        refs.append(require_file(out / "LaneHost.dll", "the prior shared step"))
    real_child(out / f"{name}.dll")
    response = real_child(out / f"{name}.rsp")
    lines = [
        "/nologo",
        "/target:library",
        "/langversion:latest",
        "/nostdlib+",
        "/optimize+",
        "/deterministic+",
        f'/out:"{out / (name + ".dll")}"',
    ]
    response.write_text(
        "\n".join([*lines, *(f'/reference:"{p}"' for p in refs), f'"{SOURCE / (name + ".cs")}"']),
        encoding="utf-8",
    )
    subprocess.run([runtime, compiler, "@" + str(response)], check=True)


def product_archive(shared_dir: Path) -> Path:
    archive = real_child(shared_dir / "mcp.zip")
    package = real_child(shared_dir / "MCPForUnity")
    if not archive.exists():
        if package.exists():
            raise ValueError(f"The existing product package has no Git archive: {package}")
        subprocess.run(
            ["git", "archive", "-o", str(archive), REVISION, "MCPForUnity"], cwd=ROOT, check=True
        )
    with zipfile.ZipFile(archive) as contents:
        if contents.comment != REVISION.encode("ascii"):
            raise ValueError(f"Product archive does not match commit {REVISION}: {archive}")
        if not package.exists():
            contents.extractall(shared_dir)
    require_file(package / "package.json", "the selected product archive")
    return package


def shared():
    """Construct offline dependencies only from the registered commit and local package caches."""
    require_file(EDITOR / "Unity.exe", "LIVE_AGENT_UNITY_EDITOR")
    data = seed_manifest()
    deps = data["dependencies"]
    for name in SEED_PACKAGES:
        deps[name] = "file:" + seed_package(name).as_posix()
    inp = CACHE / "packages/packages.unity.com/com.unity.inputsystem@1.14.2"
    cine = CACHE / "npm/packages.unity.com/com.unity.cinemachine/2.6.11/package.tgz"
    require_file(inp / "package.json", "LIVE_AGENT_UNITY_CACHE (Input System 1.14.2)")
    require_file(cine, "LIVE_AGENT_UNITY_CACHE (Cinemachine 2.6.11)")
    check_work_root()
    shared_dir = real_child(WORK / "dependencies")
    shared_dir.mkdir(parents=True, exist_ok=True)
    for name in ("manifest.json", "tone.wav", "tone.ogg"):
        real_child(shared_dir / name)
    package = product_archive(shared_dir)
    deps[PRODUCT_PACKAGE] = "file:" + package.as_posix()
    deps["com.unity.inputsystem"] = "file:" + inp.as_posix()
    deps["com.unity.cinemachine"] = "file:" + cine.as_posix()
    deps["com.unity.ugui"] = "2.0.0"
    dump(shared_dir / "manifest.json", data)
    with wave.Wave_write(str(shared_dir / "tone.wav")) as wav:
        wav.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
        wav.writeframes(b"\x20\x01\xe0\xfe" * 2000)
    if not (shared_dir / "tone.ogg").is_file():
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            raise FileNotFoundError("A local ffmpeg installation is required for the Ogg fixture")
        subprocess.run(
            [
                ffmpeg,
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-i",
                str(shared_dir / "tone.wav"),
                str(shared_dir / "tone.ogg"),
            ],
            check=True,
        )
    compile_dll("LaneHost")


def launch(index, *, registry=None, isolated_tools=None):
    if index not in range(4):
        raise ValueError("Lane index must be 0 through 3")
    if registry is not None and registry != "https://packages.unity.com":
        raise ValueError("Expected the official Unity registry for this replay")
    lane = real_child(WORK / f"lane-{index}")
    if lane.exists():
        lane_marker(lane)
    seed_manifest()
    require_file(EDITOR / "Unity.exe", "LIVE_AGENT_UNITY_EDITOR")
    for artifact in [
        "bin/LaneHost.dll",
        "dependencies/manifest.json",
        "dependencies/tone.wav",
        "dependencies/tone.ogg",
    ]:
        require_file(real_child(WORK / artifact), "the prior shared step")
    check_work_root()
    project = lane / "project"
    control = lane / "control"
    status = lane / "status"
    if (project / "Temp/UnityLockfile").exists():
        raise RuntimeError("This lane is already open")
    if not lane.exists():
        lane.mkdir()
        dump(
            lane / "owned-lane.json", {"lane": index, "revision": REVISION, "project": str(project)}
        )
    for target in [
        *(
            project / "ProjectSettings" / file.relative_to(SEED / "ProjectSettings")
            for file in (SEED / "ProjectSettings").rglob("*")
        ),
        project / "Assets/Editor/LaneHost.dll",
        project / "Packages/manifest.json",
        *(
            control / name
            for name in (
                "tone.wav",
                "tone.ogg",
                "startup-error.txt",
                "request.json",
                "response.json",
            )
        ),
        lane / "launch.json",
        lane / "editor.log",
    ]:
        real_child(target)
    for folder in [control, status, project / "Assets/Editor", project / "Packages"]:
        folder.mkdir(parents=True, exist_ok=True)
    shutil.copytree(SEED / "ProjectSettings", project / "ProjectSettings", dirs_exist_ok=True)
    settings = project / "ProjectSettings/ProjectSettings.asset"
    settings.write_text(
        settings.read_text().replace("activeInputHandler: 0", "activeInputHandler: 2"),
        encoding="utf-8",
    )
    shutil.copy2(WORK / "dependencies/manifest.json", project / "Packages/manifest.json")
    if registry:
        shutil.copy2(
            require_file(WORK / "dependencies/packages-lock.json", "the offline registry setup"),
            real_child(project / "Packages/packages-lock.json"),
        )
    shutil.copy2(WORK / "bin/LaneHost.dll", project / "Assets/Editor/LaneHost.dll")
    entry = "LaneHost.Start"
    if isolated_tools is not None:
        if not isinstance(isolated_tools, (list, tuple)) or any(
            not isinstance(name, str) or not re.fullmatch(r"[a-z][a-z0-9_]*", name)
            for name in isolated_tools
        ):
            raise ValueError("isolated_tools must be an explicit sequence of tool names")
        preferences = json.loads(
            require_file(
                real_child(WORK / "tool-preferences.json"), "frozen tool preferences"
            ).read_bytes()
        )
        bootstrap = require_file(
            real_child(WORK / "bin/IsolatedToolPreferences.dll"), "the preferences step"
        )
        shutil.copy2(bootstrap, real_child(project / "Assets/Editor/IsolatedToolPreferences.dll"))
        dump(
            real_child(control / "tool-preferences.json"),
            preferences | {"enableTools": sorted(set(isolated_tools))},
        )
        entry = "IsolatedToolPreferences.Start"
    for asset in ["tone.wav", "tone.ogg"]:
        shutil.copy2(WORK / "dependencies" / asset, control / asset)
    for file in ["startup-error.txt", "request.json", "response.json"]:
        (control / file).unlink(missing_ok=True)
    env = os.environ.copy()
    env.update(
        LIVE_LANE_PROJECT=str(project),
        UNITY_MCP_STATUS_DIR=str(status),
        UPM_NPM_REGISTRY="http://127.0.0.1:9",
        UPM_ENABLE_OFFLINE_MODE="true",
    )
    env.pop("UNITY_MCP_ALLOW_BATCH", None)
    if registry:
        # UPM can read its official local cache. Any network fallback must stay on loopback.
        env.update(
            UPM_NPM_REGISTRY=registry,
            UPM_CACHE_ROOT=str(real_child(WORK / "upm-cache")),
            HTTP_PROXY="http://127.0.0.1:9",
            HTTPS_PROXY="http://127.0.0.1:9",
            ALL_PROXY="http://127.0.0.1:9",
            NO_PROXY="localhost,127.0.0.1",
        )
    command = [
        EDITOR / "Unity.exe",
        "-batchmode",
        "-nographics",
        "-disable-assembly-updater",
        "-noUpmDefaultRegistry",
        "-job-worker-count",
        "2",
        "-projectPath",
        project,
        "-executeMethod",
        entry,
        "-logFile",
        lane / "editor.log",
    ]
    proc = subprocess.Popen(
        command,
        env=env,
        creationflags=subprocess.CREATE_NO_WINDOW,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
    )
    dump(
        lane / "launch.json",
        {
            "pid": proc.pid,
            "utc": time.time(),
            "project": str(project),
            "host_sha256": hashlib.sha256((WORK / "bin/LaneHost.dll").read_bytes()).hexdigest(),
        },
    )
    print(f"{lane.name} launched PID {proc.pid}", flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "command",
        choices=["shared", "preferences", "launch", "world", "ping", "prepare", "snapshot", "stop"],
    )
    parser.add_argument("lanes", nargs="*", type=int)
    parser.add_argument("--task", default="")
    parser.add_argument("--language", default="")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--isolated-tools", nargs="*", metavar="TOOL")
    args = parser.parse_args()
    if args.command == "shared":
        shared()
        return
    if args.command == "preferences":
        check_work_root()
        require_file(real_child(WORK / "tool-preferences.json"), "tool_preferences.py capture")
        compile_dll("IsolatedToolPreferences")
        return
    if args.command == "world":
        for index in args.lanes:
            lane_marker(WORK / f"lane-{index}")
        check_work_root()
        compile_dll("World")
    for index in args.lanes:
        lane = WORK / f"lane-{index}"
        if args.command == "world":
            shutil.copy2(WORK / "bin/World.dll", real_child(lane / "control/World.dll"))
        elif args.command == "launch":
            launch(index, isolated_tools=args.isolated_tools)
        else:
            print(
                json.dumps(
                    request(lane, args.command, args.task, args.language, args.seed),
                    ensure_ascii=True,
                )
            )


if __name__ == "__main__":
    main()
