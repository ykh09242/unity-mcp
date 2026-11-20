"""Observe the optional Addressables assembly boundary on the configured native package shapes."""

import argparse
from pathlib import Path
import re
import shutil
import subprocess
import sys
from uuid import uuid4

from player_e2e_artifacts import digest, read_json, write_json
from player_e2e_process import Invocation, execute
from run_stable_workflow_upgrade import archive, git_id
from unity_ci_packages import PROFILES, PreparationError, ROOT, prepare

FIXTURE = ROOT / "tools/native_api_shapes/AddressablesBoundaryProbe.cs"
LEAF = "Editor/Tools/Addressables/AddressablesState.cs"
COMPILER = re.compile(
    r"^\[\d+/\d+[^\]]*\] Csc [^\n/]+(?:/[^\n/]+)*/([^/\s]+\.dll)(?:\s|$)", re.MULTILINE
)
TUNDRA = re.compile(
    r"Tundra build success \(([0-9.]+) seconds\), (\d+) items updated, (\d+) evaluated"
)


def phase(
    directory: Path, project: Path, editor: Path, version: str, session: str, provider: str | None
) -> dict:
    """Observe the compiled assembly and its real inspect response after one Editor import."""
    directory.mkdir()
    log, evidence = directory / "editor.log", directory / "boundary.json"
    command = [
        str(editor),
        "-batchmode",
        "-nographics",
        "-quit",
        "-projectPath",
        str(project),
        "-job-worker-count",
        "2",
        "-executeMethod",
        "AddressablesBoundaryProbe.Observe",
        "--boundary-session",
        session,
        "--boundary-output",
        str(evidence),
        "-logFile",
        str(log),
    ]
    launch = execute(Invocation(command, directory, timeout=900))
    if launch["actual_exit_code"] != 0 or launch["forced_termination"]:
        raise ValueError(f"Addressables boundary Editor failed. See {log}")
    observed = read_json(evidence)
    expected = "addressables_settings_not_found" if provider else "addressables_unavailable"
    if (
        observed["session"] != session
        or observed["unity_version"] != version
        or observed["provider_version"] != provider
        or observed["inspection_result"] != expected
        or any("Addressables" in name for name in observed["adapter_references"])
    ):
        raise ValueError(f"Optional provider boundary differed. See {evidence}")
    logs = log.read_text(encoding="utf-8", errors="replace")
    return {
        **observed,
        "launch_wall_ms": launch["finished_unix_ms"] - launch["started_unix_ms"],
        "completed_csc_nodes": sorted(set(COMPILER.findall(logs))),
        "bee_builds": [
            {
                "elapsed_seconds": float(seconds),
                "items_updated": int(updated),
                "items_evaluated": int(evaluated),
            }
            for seconds, updated, evaluated in TUNDRA.findall(logs)
        ],
    }


def run(options: argparse.Namespace, output: Path) -> None:
    """Measure unchanged and provider-adapter leaf import against a known source archive."""
    output.mkdir(parents=True)
    editor = options.editor.resolve(strict=True)
    cache = options.registry_cache
    revision = git_id(options.revision, "commit")
    source = output / "source"
    archive(revision, source, "MCPForUnity")
    fixture = source / FIXTURE.name
    shutil.copyfile(FIXTURE, fixture)
    legacy = read_json(PROFILES)["nativePackageSets"]["addressables-legacy"]
    providers = {"absent": {}, "legacy": legacy}
    runs = {}
    for name in options.shape:
        specs = providers[name]
        version = specs.get("com.unity.addressables", {}).get("version")
        shape = output / name
        prepared = shape / "prepared"
        prepare(
            options.unity_version,
            editor.parent / "Data",
            prepared,
            repo=source,
            registry_cache=cache,
            extra_packages=specs,
            fixture_sources={f"Assets/Editor/{fixture.name}": fixture},
        )
        project = prepared / "project"
        session = uuid4().hex
        (project / ".boundary-owner").write_text(session, encoding="ascii")
        args = (project, editor, options.unity_version, session, version)
        initial = phase(shape / "cold-project", *args)
        unchanged = phase(shape / "warm-unchanged", *args)
        adapter = prepared / "package" / LEAF
        old = digest(adapter)
        with adapter.open("ab") as stream:
            stream.write(b"\n// Disposable compile-boundary fixture comment.\n")
        changed = phase(shape / "warm-adapter-edit", *args)
        runs[name] = {
            "leaf": LEAF,
            "old_leaf_sha256": old,
            "new_leaf_sha256": digest(adapter),
            "cold_project": initial,
            "warm_unchanged": unchanged,
            "warm_adapter_edit": changed,
        }
        write_json(shape / "measurement.json", runs[name])
    write_json(
        output / "measurement.json",
        {
            "commit": revision,
            "tree": git_id(revision, "tree"),
            "fixture_sha256": digest(fixture),
            "shapes": runs,
        },
    )
    print(output / "measurement.json")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--revision", default="HEAD")
    parser.add_argument("--editor", type=Path, required=True)
    parser.add_argument("--unity-version", required=True)
    parser.add_argument("--registry-cache", type=Path)
    parser.add_argument("--shape", action="append", choices=("absent", "legacy"))
    parser.add_argument("--output", type=Path, required=True)
    options = parser.parse_args()
    options.shape = tuple(dict.fromkeys(options.shape or ("absent", "legacy")))
    try:
        run(options, options.output.resolve())
    except (OSError, ValueError, PreparationError, subprocess.SubprocessError) as error:
        print(f"Addressables boundary measurement unavailable: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
