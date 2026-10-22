"""Prepare a pinned, disposable Unity Editor QA project and launch only that project.

This runner never discovers/reuses a running Editor, modifies global EditorPrefs,
reads Unity license files, or terminates a process it did not launch. Preparation
records source identity and launch records retain explicit arguments and PID.
"""

import argparse
import hashlib
import importlib.util
import io
import json
import os
import re
from pathlib import Path
import subprocess
import shutil
import tarfile
import time
import sys
import tempfile

try:
    from .check_unity_test_results import check_results
    from .native_test_inputs import (
        apply_refresh,
        capture_inputs,
        checked_path,
        digest,
        load_selection,
        operation_lock,
        refresh_plan,
        refresh_tree,
        tree_files,
        verify_inputs,
    )
except ImportError:  # Direct script invocation.
    from check_unity_test_results import check_results
    from native_test_inputs import (
        apply_refresh,
        capture_inputs,
        checked_path,
        digest,
        load_selection,
        operation_lock,
        refresh_plan,
        refresh_tree,
        tree_files,
        verify_inputs,
    )
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_EDITOR = Path("C:/Program Files/Unity/Hub/Editor/6000.0.69f1/Editor/Unity.exe")


def owned_path(value):
    path = checked_path(value)
    if not path.is_relative_to(ROOT / "reports") or path == ROOT / "reports":
        raise ValueError("QA output must be a task-owned directory beneath repository reports")
    return path


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def snapshot_source(args, output):
    output.mkdir(parents=True, exist_ok=True)
    snapshot = output / "snapshot"
    if snapshot.exists():
        raise ValueError("Snapshot already exists; choose a fresh evidence directory")
    ref = subprocess.check_output(["git", "rev-parse", args.ref], cwd=ROOT, text=True).strip()
    paths = [
        "MCPForUnity",
        "TestProjects/UnityMCPTests/Assets/Tests",
        "TestProjects/UnityMCPTests/Assets/Scripts",
        "TestProjects/UnityMCPTests/ProjectSettings",
        "tools/unity_ci_packages.py",
        "tools/unity-ci-packages.json",
    ]
    snapshot.mkdir()
    source = {"ref": ref, "snapshotPaths": paths, "workingTree": args.working_tree}
    if args.working_tree:

        def excluded(directory, names):
            result = {
                name
                for name in names
                if name in {"Library", "Temp", "Logs", "obj", ".git", "__pycache__"}
                or name.startswith(".env")
                or Path(name).suffix.lower() in {".key", ".pem", ".pfx", ".p12"}
            }
            for name in set(names) - result:
                if (Path(directory) / name).is_symlink():
                    raise ValueError("Linked source is prohibited")
            return result

        for relative in paths:
            src, dst = checked_path(ROOT / relative), snapshot / relative
            if src.is_dir():
                shutil.copytree(src, dst, ignore=excluded)
            else:
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(src, dst)
        source["fileSha256"] = {
            path.relative_to(snapshot).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in snapshot.rglob("*")
            if path.is_file()
        }
    else:
        content = subprocess.check_output(["git", "archive", ref, *paths], cwd=ROOT)
        with tarfile.open(fileobj=io.BytesIO(content)) as archive:
            for member in archive.getmembers():
                relative = Path(member.name)
                if (
                    relative.is_absolute()
                    or ".." in relative.parts
                    or not (member.isfile() or member.isdir())
                ):
                    raise ValueError("Unsafe source snapshot member")
                if any(part.startswith(".env") for part in relative.parts):
                    raise ValueError("Forbidden source path in snapshot")
            archive.extractall(snapshot, filter="data")
        source["archiveSha256"] = hashlib.sha256(content).hexdigest()
    write_json(output / "source.json", source)


def prepare(args):
    output = owned_path(args.output)
    snapshot_source(args, output)
    resolve(args)


def resolve(args):
    output = owned_path(args.output)
    snapshot = output / "snapshot"
    module_path = snapshot / "tools/unity_ci_packages.py"
    spec = importlib.util.spec_from_file_location("editor_qa_packages", module_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    result = module.prepare(
        args.version,
        args.editor.parent / "Data",
        snapshot / "isolated",
        repo=snapshot,
        profiles_path=snapshot / "tools/unity-ci-packages.json",
        registry_cache=Path(args.registry_cache).resolve() if args.registry_cache else None,
    )
    from dataclasses import asdict

    prep = {
        **asdict(result),
        "projectAbsolute": str(snapshot / result.project_path),
        "unityVersion": args.version,
    }
    prep["inputs"] = capture_inputs(output, prep, args.editor)
    write_json(output / "preparation.json", prep)
    print(json.dumps({"status": "PREPARED", "project": str(snapshot / result.project_path)}))


def _launch(args):
    output = owned_path(args.output)
    prep = json.loads((output / "preparation.json").read_text(encoding="utf-8"))
    project = Path(prep["projectAbsolute"]).resolve()
    if not project.is_relative_to(output) or project == output:
        raise ValueError("Project is not owned by this QA output")
    if prep.get("refreshPending"):
        raise ValueError("Interrupted source refresh; preparation is not launchable")
    verify_inputs(output, prep, args.editor, prep["inputs"])
    selection = load_selection(
        args.groups_manifest, args.group or ["transport-readiness"], sys.platform
    )
    if args.filter is not None and args.filter != selection["filter"]:
        raise ValueError("Filter must equal the complete selected method inventory")
    if (project / "Temp/UnityLockfile").exists():
        raise ValueError("Owned project is already open")
    if not args.editor.is_file():
        raise ValueError("Explicit Editor executable is missing")
    if not args.isolation_verified:
        raise ValueError("Launch requires confirmed package lifecycle isolation")
    if not re.fullmatch(r"[A-Za-z0-9_-]+", args.label):
        raise ValueError("Evidence label must be a plain filename component")
    result_path = checked_path(output / f"{args.label}-results.xml")
    log_path = checked_path(output / f"{args.label}-editor.log")
    record_path = checked_path(output / f"{args.label}-launch.json")
    if any(path.exists() for path in (record_path, result_path, log_path)):
        raise ValueError("Launch evidence already exists; use a fresh label")
    command = [
        str(args.editor),
        "-batchmode",
        *([] if selection["requiresGraphics"] else ["-nographics"]),
        "-forgetProjectPath",
        "-projectPath",
        str(project),
        "-runTests",
        "-testPlatform",
        "EditMode",
        "-testFilter",
        selection["filter"],
        "-testResults",
        str(result_path),
        "-logFile",
        str(log_path),
    ]
    env = os.environ.copy()
    env.pop("UNITY_MCP_ALLOW_BATCH", None)
    env["UNITY_MCP_OWNED_TRANSPORT_TESTS"] = "1"
    env["UNITY_MCP_STATUS_DIR"] = str(output / "stdio-status")
    if args.stdio_command_timeout_ms is not None:
        if args.stdio_command_timeout_ms <= 0:
            raise ValueError("Owned stdio command timeout must be positive")
        env["UNITY_MCP_STDIO_COMMAND_TIMEOUT_MS"] = str(args.stdio_command_timeout_ms)
    creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    startupinfo = None
    if os.name == "nt":
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = 0
    project_settings = (
        tree_files(project / "ProjectSettings") if (project / "ProjectSettings").is_dir() else {}
    )
    cache_reused = (project / "Library").is_dir()
    started = time.time()
    process = subprocess.Popen(
        command,
        env=env,
        cwd=project,
        creationflags=creationflags,
        startupinfo=startupinfo,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    record = {
        "command": command,
        "pid": process.pid,
        "startedUtcEpoch": started,
        "project": str(project),
        "autostartSuppressed": True,
        "timeoutSeconds": args.timeout,
        "isolationVerified": True,
        "ownedTransportTests": True,
        "stdioCommandTimeoutMs": args.stdio_command_timeout_ms,
        "selection": selection,
        "inputs": prep["inputs"],
        "cacheReused": cache_reused,
        "projectSettingsBefore": project_settings,
    }
    failure = None
    try:
        write_json(record_path, record)
        print(json.dumps({"status": "STARTED", "pid": process.pid}), flush=True)
        record["exitCode"] = process.wait(timeout=args.timeout)
    except subprocess.TimeoutExpired:
        record["status"] = "BLOCKED"
        record["reason"] = "Owned Editor exceeded bounded launch timeout"
    except BaseException as error:
        failure = error
        record["status"] = "BLOCKED"
        record["reason"] = (
            "Owned QA runner interrupted"
            if isinstance(error, KeyboardInterrupt)
            else "Owned QA runner failed"
        )
        record["exceptionType"] = type(error).__name__
    finally:
        # Only the held Popen handle is used. Never discover a process by name/PID.
        if process.poll() is None:
            process.terminate()
            record["ownedProcessCleanup"] = "terminate"
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                record["ownedProcessCleanup"] = "kill-after-terminate-timeout"
                process.wait(timeout=10)
        record["elapsedSeconds"] = round(time.time() - started, 3)
        # Persist interruption/timeout evidence before parsing a potentially partial XML.
        write_json(record_path, record)
    if result_path.is_file():
        try:
            root = ET.parse(result_path).getroot()
            record["nunit"] = root.attrib
            record["testCases"] = [
                {"name": case.get("fullname"), "result": case.get("result")}
                for case in root.iter("test-case")
            ]
            if "status" not in record:
                valid = (
                    check_results(
                        result_path,
                        "success" if record.get("exitCode") == 0 else "failure",
                        selection["required"],
                    )
                    == 0
                )
                record["status"] = (
                    "PASS" if valid and int(root.get("skipped", "0")) == 0 else "FAIL"
                )
        except (ET.ParseError, ValueError) as error:
            record.setdefault("status", "BLOCKED")
            record.setdefault("reason", "Editor produced invalid NUnit XML")
            record["xmlExceptionType"] = type(error).__name__
    else:
        record["status"] = "BLOCKED"
        record.setdefault(
            "reason", "Editor did not produce NUnit result XML; inspect owned launch log"
        )
    try:
        verify_inputs(output, prep, args.editor, prep["inputs"])
        record["projectSettingsAfter"] = (
            tree_files(project / "ProjectSettings")
            if (project / "ProjectSettings").is_dir()
            else {}
        )
        if digest(args.groups_manifest) != selection["manifestSha256"]:
            raise ValueError("Native selection manifest changed during launch")
    except (OSError, ValueError) as error:
        record["status"] = "BLOCKED"
        record["reason"] = str(error)
    write_json(record_path, record)
    print(json.dumps(record))
    if failure is not None:
        raise failure
    return 0 if record["status"] == "PASS" else 2 if record["status"] == "FAIL" else 3


def launch(args):
    output = owned_path(args.output)
    with operation_lock(output):
        return _launch(args)


def refresh(args):
    output = owned_path(args.output)
    with operation_lock(output):
        prep = json.loads((output / "preparation.json").read_text(encoding="utf-8"))
        project = checked_path(prep["projectAbsolute"])
        if prep.get("refreshPending") or (project / "Temp/UnityLockfile").exists():
            raise ValueError("Project is open or refresh is incomplete")
        verify_inputs(output, prep, args.editor, prep["inputs"])
        if args.version != prep["unityVersion"]:
            raise ValueError("Editor version change requires new preparation")
        with tempfile.TemporaryDirectory(prefix=".refresh-", dir=output) as temporary:
            stage = owned_path(temporary)
            snapshot_source(args, stage)
            old, new = output / "snapshot", stage / "snapshot"
            for rel in ("tools/unity_ci_packages.py", "tools/unity-ci-packages.json"):
                if (old / rel).read_bytes() != (new / rel).read_bytes():
                    raise ValueError("Package resolver changes require new preparation")
            before = json.loads((old / "MCPForUnity/package.json").read_text(encoding="utf-8"))
            after = json.loads((new / "MCPForUnity/package.json").read_text(encoding="utf-8"))
            if before.get("dependencies") != after.get("dependencies"):
                raise ValueError("Package dependency changes require new preparation")
            if tree_files(old / "TestProjects/UnityMCPTests/ProjectSettings") != tree_files(
                new / "TestProjects/UnityMCPTests/ProjectSettings"
            ):
                raise ValueError("Project settings changes require new preparation")
            package = project.parent / "package"
            installed = json.loads((package / "package.json").read_text(encoding="utf-8"))
            before["dependencies"] = installed["dependencies"]
            if before != installed:
                raise ValueError("Prepared package metadata changed")
            after["dependencies"] = installed["dependencies"]
            mappings = [
                ("MCPForUnity", package, ("package.json",)),
                ("TestProjects/UnityMCPTests/Assets/Tests", project / "Assets/Tests", ()),
                ("TestProjects/UnityMCPTests/Assets/Scripts", project / "Assets/Scripts", ()),
            ]
            plans = []
            for rel, target, exclude in mappings:
                plans.extend(refresh_plan(old / rel, new / rel, target, exclude=exclude))
                plans.extend(refresh_plan(old / rel, new / rel, old / rel))
            # Validate every conflict first; a failed/interrupted publication cannot be launched.
            prep["refreshPending"] = True
            write_json(output / "preparation.json", prep)
            apply_refresh(plans)
            if installed != after:
                write_json(package / "package.json", after)
            write_json(
                output / "source.json",
                json.loads((stage / "source.json").read_text(encoding="utf-8")),
            )
            prep["inputs"] = capture_inputs(output, prep, args.editor)
            prep.pop("refreshPending")
            write_json(output / "preparation.json", prep)
            print(
                json.dumps(
                    {
                        "status": "REFRESHED",
                        "filesChanged": len(plans),
                        "cacheReused": (project / "Library").is_dir(),
                    }
                )
            )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["prepare", "resolve", "refresh", "launch"])
    parser.add_argument("--output", required=True)
    parser.add_argument("--ref", default="HEAD")
    parser.add_argument("--version", default="6000.0.69f1")
    parser.add_argument("--editor", type=Path, default=DEFAULT_EDITOR)
    parser.add_argument("--registry-cache")
    parser.add_argument("--working-tree", action="store_true")
    parser.add_argument("--label", default="editmode")
    parser.add_argument(
        "--groups-manifest", type=Path, default=ROOT / "tools/unity-native-groups.json"
    )
    parser.add_argument("--group", action="append")
    parser.add_argument("--filter", help="Optional assertion of the exact selected method filter")
    parser.add_argument("--timeout", type=int, default=240)
    parser.add_argument("--stdio-command-timeout-ms", type=int)
    parser.add_argument("--isolation-verified", action="store_true")
    args = parser.parse_args()
    if args.command == "prepare":
        prepare(args)
    elif args.command == "resolve":
        resolve(args)
    elif args.command == "refresh":
        refresh(args)
    else:
        return launch(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
