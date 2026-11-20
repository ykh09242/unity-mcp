"""Build and capture both maintained prefab controls on an approved Windows Unity builder."""

import argparse
from hashlib import sha256
import json
import os
from pathlib import Path
import subprocess
import sys
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "Server/src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from player_e2e_artifacts import write_json
from player_e2e_process import Invocation, execute
from unity_ci_packages import prepare
from run_prefab_move import FIXTURE, METHOD, repair
from services.verification.input_chunks import source_revision
from services.verification.git_draft import _Git
from cli.utils.play_scenario_analysis_io import read_bytes
from services.verification.pair_transfer import capture_build, pack_pair


def git(project, *args):
    return subprocess.check_output(
        [
            "git",
            "-c",
            "user.name=Unity fixture builder",
            "-c",
            "user.email=unity-fixture@users.noreply.github.com",
            *args,
        ],
        cwd=project,
        text=True,
        timeout=120,
    ).strip()


def invoke_editor(editor, project, output, session, phase, *, build=False):
    output.mkdir(parents=True, exist_ok=False)
    args = [
        str(editor),
        "-batchmode",
        "-nographics",
        "-quit",
        "-job-worker-count",
        "2",
        "-projectPath",
        str(project),
        "-buildTarget",
        "Win64",
        "-logFile",
        "-",
        "-executeMethod",
        METHOD,
        "--shop-session",
        session,
        "--shop-phase",
        phase,
        "--mcp-scenario-output",
        str(output / "build"),
        "--shop-review-capture",
    ]
    args.extend(
        ["--shop-reuse-prepared", "--mcp-pin-build-source"] if build else ["--shop-prepare-only"]
    )
    process = execute(Invocation(args, output / "editor-process", timeout=1800 if build else 600))
    if process["actual_exit_code"] or process["forced_termination"]:
        raise ValueError(
            "Unity fixture phase failed: " + str(output / "editor-process/process.log")
        )
    report = json.loads((output / "editor.json").read_bytes())
    if (
        report["session"] != session
        or report["phase"] != phase
        or Path(report["project"]).resolve() != project
    ):
        raise ValueError("unexpected_fixture_editor_identity")
    return report


def frozen_definition(project, revision):
    relative = "ProjectSettings/MCPForUnity/PlayScenarios/verified-shop.json"
    file = project / relative
    live = read_bytes(file, 1024**2)
    committed = _Git(project).read("cat-file", "blob", revision + ":" + relative, limit=1024**2)
    if live != committed or json.loads(live)["name"] != "verified-shop":
        raise ValueError("fixture_scenario_definition_is_not_frozen")
    info = file.stat()
    return sha256(live).hexdigest(), info.st_mtime_ns, info.st_ino


def registration(project, registry, phase, commit, observed):
    folder = registry / phase
    bundle = json.loads((folder / "build/scenario-bundle.json").read_bytes())
    inputs = observed["inputs"]
    if source_revision(inputs) != bundle["build_source_revision"] or observed[
        "source_revision"
    ] != source_revision(inputs):
        raise ValueError("fixture_build_input_revision_changed")
    direct = {path: inputs[path] for path in observed["direct_paths"]}
    added = {path: digest for path, digest in inputs.items() if path not in direct}
    write_json(
        folder / "closure.json",
        {
            "schema_version": 1,
            "kind": "verification_dependency_closure",
            "candidate_commit": commit,
            "candidate_source_revision": source_revision(direct),
            "expanded_source_revision": source_revision(inputs),
            "input_hashes": added,
            "root": observed["scene"],
            "coverage": "unity_assetdatabase_recursive_project_assets",
            "graph_commit_binding": "builder_observed",
            "unresolved": [],
            "unity_dependencies": observed["unity_dependencies"],
            "external_dependencies": [
                p for p in observed["unity_dependencies"] if not p.startswith("Assets/")
            ],
        },
    )
    write_json(
        folder / "candidate.json",
        {
            "schema_version": 1,
            "project": str(project),
            "scenario": bundle["scenario_name"],
            "source_revision": observed["source_revision"],
            "inputs": inputs,
            "assets": {observed["asset_path"]: observed["asset_guid"]},
            "observations": {
                key: observed[key]
                for key in (
                    "key",
                    "asset_guid",
                    "serialized_guid",
                    "loader_guid",
                    "dependency_retained",
                )
            },
            "build_id": bundle["build_id"],
            "payload_hash": bundle["payload_hash"],
        },
    )


def build_pair(editor, directory):
    directory.mkdir(parents=True, exist_ok=False)
    prepared = directory / "prepared"
    base = Path("Assets/Scripts/ScenarioPlayerFixture")
    paths = [
        base / name
        for name in (
            "ShopWorkflowProbe.cs",
            "MCPForUnity.ScenarioPlayerFixture.asmdef",
            "Editor/ShopWorkflowFixtureBuild.cs",
            "Editor/MCPForUnity.ScenarioPlayerFixture.Editor.asmdef",
        )
    ]
    paths += [Path(str(path) + ".meta") for path in paths]
    paths += [Path(str(base) + ".meta"), base / "Editor.meta"]
    prepare(
        "6000.0.69f1",
        editor.parent / "Data",
        prepared,
        fixture_sources={p.as_posix(): FIXTURE / p for p in paths},
    )
    project = prepared / "project"
    manifest = project / "Packages/manifest.json"
    document = json.loads(manifest.read_bytes())
    package_name = json.loads((ROOT / "MCPForUnity/package.json").read_bytes())["name"]
    document["dependencies"][package_name] = (
        "file:" + Path(os.path.relpath(ROOT / "MCPForUnity", manifest.parent)).as_posix()
    )
    manifest.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8", newline="\n")
    session = uuid4().hex
    (project / ".shop-workflow-owner").write_text(session, encoding="ascii")
    git(project, "init", "--initial-branch=fixture-build", "--template=", "--quiet")
    git(project, "config", "--local", "core.autocrlf", "false")
    registry = directory / "registrations"
    os.environ["UNITY_MCP_VERIFICATION_ROOT"] = str(registry)
    transfer = directory / "transfer"
    expected_guid = None
    definition_hash = None
    for phase in ("original", "moved", "repaired"):
        if phase == "repaired":
            repair(project)
        initial = invoke_editor(editor, project, directory / ("prepare-" + phase), session, phase)
        git(project, "add", "--", "Assets", "Packages", "ProjectSettings")
        staged = frozen_definition(project, "")
        if definition_hash is not None and definition_hash != staged[0]:
            raise ValueError("fixture_scenario_changed_between_builds")
        definition_hash = staged[0]
        git(project, "commit", "--quiet", "-m", "Prepared prefab fixture " + phase)
        commit = git(project, "rev-parse", "HEAD")
        if phase == "original":
            expected_guid = initial["asset_guid"]
            continue
        name = "baseline" if phase == "moved" else "candidate"
        before = frozen_definition(project, commit)
        observed = invoke_editor(editor, project, registry / phase, session, phase, build=True)
        if frozen_definition(project, commit) != before:
            raise ValueError("fixture_build_rewrote_committed_scenario")
        if (
            observed["asset_guid"] != expected_guid
            or observed["serialized_guid"] != expected_guid
            or observed["loader_guid"] != ("" if phase == "moved" else expected_guid)
            or observed["dependency_retained"] is not True
            or observed["inputs"] != initial["inputs"]
        ):
            raise ValueError("fixture_editor_guid_or_loader_observation_mismatch")
        registration(project, registry, phase, commit, observed)
        capture_build(project, ROOT, phase, registry / phase / "closure.json", transfer / name)
        git(project, "branch", name, commit)
    git(
        project,
        "bundle",
        "create",
        str(transfer / "project.bundle"),
        "refs/heads/baseline",
        "refs/heads/candidate",
    )
    return pack_pair(project, transfer, Path(__file__).with_name("prefab-pair-expectations.json"))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--editor", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    options = parser.parse_args()
    print(json.dumps(build_pair(options.editor.resolve(strict=True), options.output.resolve())))
