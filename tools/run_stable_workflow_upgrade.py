"""Read stable-written scenario, verification profile, and report data across two Unity revisions."""

import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys
from uuid import uuid4
from zipfile import ZipFile

from player_e2e_artifacts import digest, read_json, write_json
from player_e2e_process import Invocation, execute
from unity_ci_packages import PreparationError, ROOT, prepare

FIXTURE = ROOT / "tools/native_api_shapes/VerifiedWorkflowUpgrade.cs"
FILES = (
    "ProjectSettings/MCPForUnity/PlayScenarios/stable-compatibility.json",
    "ProjectSettings/MCPVerificationProfiles.json",
    "Library/MCPForUnity/PlayScenarioRuns/0123456789abcdef0123456789abcdef.json",
)
READ_SERVER = """
import json
from pathlib import Path
import sys
sys.path.insert(0, sys.argv[1])
from cli.utils.play_scenario_reports import write_player_artifacts
from services.verification.profiles import profile_operations, profile_permissions, resolve_profile
profile_path, report_path, output = map(Path, sys.argv[2:])
report = json.loads(report_path.read_bytes())
profile = resolve_profile('stable-upgrade', json.loads(profile_path.read_bytes()))
write_player_artifacts(report, output / 'export')
print(json.dumps({'profile': profile.model_dump(mode='json'), 'operations': profile_operations(profile),
                  'permissions': sorted(profile_permissions(profile))}, sort_keys=True))
"""


def git_id(ref: str, kind: str) -> str:
    """Resolve an input ref into an immutable Git identifier."""
    return subprocess.check_output(
        ["git", "rev-parse", "--verify", "--end-of-options", f"{ref}^{{{kind}}}"],
        cwd=ROOT,
        encoding="ascii",
    ).strip()


def archive(commit: str, output: Path, path: str) -> None:
    """Export selected authored paths from Git without switching the checkout."""
    output.mkdir()
    archive_file = output / "source.zip"
    subprocess.run(
        ["git", "archive", "--format=zip", f"--output={archive_file}", commit, path],
        cwd=ROOT,
        check=True,
        timeout=120,
    )
    with ZipFile(archive_file) as source:
        source.extractall(output)


def server_pin(package: Path) -> str:
    """Take the server commit from the published package's update pin."""
    value = read_json(package / "package.json")["mcpServerSource"]
    prefix = "https://github.com/ykh09242/unity-mcp/archive/"
    suffix = ".zip#subdirectory=Server"
    if not value.startswith(prefix) or not value.endswith(suffix):
        raise ValueError("Unexpected stable server pin")
    pin = value.removeprefix(prefix).removesuffix(suffix)
    if len(pin) != 40 or git_id(pin, "commit") != pin:
        raise ValueError("Stable server pin did not resolve to the exact local commit")
    return pin


def observe(
    directory: Path,
    editor: Path,
    version: str,
    registry_cache: Path | None,
    revision: str,
    stable_artifacts: Path | None,
) -> tuple[Path, dict]:
    """Compile the product in an isolated Editor and exercise actual storage entry points."""
    directory.mkdir()
    source = directory / "package-source"
    archive(revision, source, "MCPForUnity")
    fixture = source / FIXTURE.name
    shutil.copyfile(FIXTURE, fixture)
    prepared = directory / "prepared"
    prepare(
        version,
        editor.parent / "Data",
        prepared,
        repo=source,
        registry_cache=registry_cache,
        fixture_sources={f"Assets/Editor/{FIXTURE.name}": fixture},
    )
    project = prepared / "project"
    session = uuid4().hex
    (project / ".upgrade-owner").write_text(session, encoding="ascii")
    if stable_artifacts is not None:
        for relative in FILES:
            destination = project / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(stable_artifacts / relative, destination)
    evidence = directory / "editor.json"
    log = directory / "editor.log"
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
        "VerifiedWorkflowUpgrade.Run",
        "--upgrade-mode",
        "produce" if stable_artifacts is None else "observe",
        "--upgrade-session",
        session,
        "--upgrade-evidence",
        str(evidence),
        "-logFile",
        str(log),
    ]
    write_json(
        directory / "source.json", {"package_commit": revision, "fixture_sha256": digest(fixture)}
    )
    process = execute(Invocation(command, directory, timeout=900))
    if process["actual_exit_code"] != 0 or process["forced_termination"]:
        raise ValueError(f"Editor launch failed. See {log}")
    observed = read_json(evidence)
    if observed["session"] != session or observed["unity_version"] != version:
        raise ValueError(f"Editor evidence has the wrong session or version. See {evidence}")
    manifest = read_json(project / "Packages/manifest.json")["dependencies"]
    actual = read_json(project / "Packages/packages-lock.json")["dependencies"]
    if any(actual.get(name, {}).get("version") != expected for name, expected in manifest.items()):
        raise ValueError(f"Unity resolved unexpected package versions. See {project}")
    return project, observed


def read_server(directory: Path, server_commit: str, project: Path) -> dict:
    """Resolve the real saved profile and export the real old report on this server revision."""
    source = directory / "server-source"
    archive(server_commit, source, "Server/src")
    completed = subprocess.run(
        [
            sys.executable,
            "-I",
            "-c",
            READ_SERVER,
            str(source / "Server/src"),
            str(project / FILES[1]),
            str(project / FILES[2]),
            str(directory),
        ],
        capture_output=True,
        check=True,
        timeout=120,
    )
    server = json.loads(completed.stdout)
    write_json(directory / "server.json", server)
    return server


def main() -> int:
    """Export, check, and compare legacy input bytes and observable interpretations."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stable", default="ykh09242-v2.5.0")
    parser.add_argument("--candidate", default="HEAD")
    parser.add_argument("--editor", type=Path, required=True)
    parser.add_argument("--unity-version", required=True)
    parser.add_argument("--registry-cache", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    options = parser.parse_args()
    output = options.output.resolve()
    try:
        output.mkdir(parents=True)
        stable = git_id(options.stable, "commit")
        candidate = git_id(options.candidate, "commit")
        before, after = output / "before", output / "after"
        args = (options.editor.resolve(strict=True), options.unity_version, options.registry_cache)
        before_project, old = observe(before, *args, stable, None)
        stable_server = server_pin(before / "package-source/MCPForUnity")
        old_hashes = {name: digest(before_project / name) for name in FILES}
        old_server = read_server(before, stable_server, before_project)
        after_project, new = observe(after, *args, candidate, before_project)
        new_server = read_server(after, candidate, after_project)
        new_hashes = {name: digest(after_project / name) for name in FILES}
        fields = ("scenario", "scenario_names", "profile", "report", "report_ids")
        changed = {
            field: {"before": old[field], "after": new[field]}
            for field in fields
            if old[field] != new[field]
        }
        # Revisions may add empty report fields. The old fields and values must survive.
        if "report" in changed and all(new["report"].get(k) == v for k, v in old["report"].items()):
            del changed["report"]
        evidence = {
            "stable": {"commit": stable, "tree": git_id(stable, "tree"), "server": stable_server},
            "candidate": {
                "commit": candidate,
                "tree": git_id(candidate, "tree"),
                "server": candidate,
            },
            "unity_version": options.unity_version,
            "fixture_sha256": digest(FIXTURE),
            "before": old_hashes,
            "after": new_hashes,
            "originals_preserved": all(
                digest(before_project / k) == v for k, v in old_hashes.items()
            ),
            "changed_editor_interpretations": changed,
            "server_profile_match": old_server == new_server,
            "server_junit_match": digest(before / "export/junit.xml")
            == digest(after / "export/junit.xml"),
        }
        write_json(output / "comparison.json", evidence)
        if (
            changed
            or old_hashes != new_hashes
            or not all(
                evidence[k]
                for k in ("originals_preserved", "server_profile_match", "server_junit_match")
            )
        ):
            raise ValueError("Stable workflow compatibility changed. See comparison.json.")
    except subprocess.CalledProcessError as error:
        print(
            f"Upgrade compatibility subprocess exited with code {error.returncode}.",
            file=sys.stderr,
        )
        if error.stderr:
            print(error.stderr.decode(errors="replace"), file=sys.stderr)
        return 1
    except (OSError, ValueError, PreparationError, subprocess.SubprocessError) as error:
        print(f"Upgrade compatibility failed: {error}", file=sys.stderr)
        return 1
    print(output / "comparison.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
