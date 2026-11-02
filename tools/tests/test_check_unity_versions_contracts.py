"""Execute contributor checker functions with harmless Editor and Docker substitutes."""

import json
import os
from pathlib import Path, PurePosixPath
import shlex
import shutil
import subprocess
import sys

import pytest

from tools.tests.test_check_unity_versions_images import find_shell


ROOT = Path(__file__).resolve().parents[2]
PASS_XML = (
    '<test-run result="Passed" total="1" passed="1" failed="0" inconclusive="0" '
    'skipped="0"><test-case fullname="Fixture.Test" result="Passed" /></test-run>'
)


def ps_quote(value: str | Path) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def run_checker(
    tmp_path: Path,
    shell: str,
    runner: str,
    result: str,
    full: bool = True,
    external: bool = False,
) -> tuple[int, list[str], list[str], Path, Path]:
    executable = find_shell(shell)
    repo = tmp_path / "checkout with spaces 한글"
    project = (tmp_path if external else repo / "TestProjects") / "Unity MCP 한글"
    (project / "Packages").mkdir(parents=True)
    (project / "Packages/manifest.json").write_text(
        json.dumps(
            {
                "dependencies": {
                    "package": "file:../LocalPackage" if external else "file:../../../MCPForUnity"
                }
            }
        ),
        encoding="utf-8",
    )
    (repo / "MCPForUnity").mkdir(parents=True)
    if external:
        (project / "LocalPackage").mkdir()
    (repo / "tools").mkdir()
    shutil.copyfile(
        ROOT / "tools/check_unity_test_results.py", repo / "tools/check_unity_test_results.py"
    )
    log = tmp_path / "logs with spaces 한글" / "6000.3.25f1.log"
    log.parent.mkdir()
    log.touch()
    xml = Path(str(log) + ".xml")
    if result == "stale":
        xml.write_text(PASS_XML, encoding="utf-8")
    trace = tmp_path / "argv.json"
    probe = tmp_path / "argument probe.py"
    contents = {
        "passing": PASS_XML,
        "empty": "",
        "zero": '<test-run result="Passed" total="0" passed="0" failed="0" inconclusive="0" skipped="0" />',
        "failed": PASS_XML.replace("Passed", "Failed").replace(
            'passed="1" failed="0"', 'passed="0" failed="1"'
        ),
        "runner_failure": PASS_XML,
    }
    probe.write_text(
        "import json, pathlib, sys\n"
        f"pathlib.Path({str(trace)!r}).write_text(json.dumps(sys.argv[1:]), encoding='utf-8')\n"
        "if '-testResults' in sys.argv:\n"
        "    destination = pathlib.Path(sys.argv[sys.argv.index('-testResults') + 1])\n"
        f"    content = {contents.get(result)!r}\n"
        "    if content is not None:\n"
        "        destination.write_text(content, encoding='utf-8')\n"
        # A passing XML cannot override an Editor process failure.
        f"sys.exit({1 if result == 'runner_failure' else 0})\n",
        encoding="utf-8",
    )
    docker_trace = tmp_path / "docker-args.json"
    docker_probe = tmp_path / "docker probe.py"
    docker_probe.write_text(
        "import json, pathlib, sys\n"
        f"pathlib.Path({str(docker_trace)!r}).write_text(json.dumps(sys.argv[1:]), encoding='utf-8')\n",
        encoding="utf-8",
    )
    if shell == "bash":
        source = (ROOT / "tools/check-unity-versions.sh").read_text(encoding="utf-8")
        functions = source[source.index("run_local() {") : source.index("# ---- main loop")]
        editor = tmp_path / "hub" / "6000.3.25f1" / "Editor" / "Unity"
        editor.parent.mkdir(parents=True)
        editor.write_text(
            f'#!/bin/bash\n{shlex.quote(Path(sys.executable).as_posix())} {shlex.quote(probe.as_posix())} "$@"\n',
            encoding="utf-8",
        )
        editor.chmod(0o755)
        # printf uses one NUL-delimited record per argument, preserving script newlines.
        program = f"""
REPO_ROOT={shlex.quote(repo.as_posix())}
PROJECT_PATH={shlex.quote(project.as_posix())}
HUB_ROOT={shlex.quote((tmp_path / "hub").as_posix())}
UNITY_RELPATH=Editor/Unity
PYTHON_BIN={shlex.quote(Path(sys.executable).as_posix())}
DOCKER_IMAGE_TAG=base-custom
FULL={int(full)}
C_FAIL= C_SKIP= C_DIM= C_RST=
docker() {{
  if [[ "$1" == run ]]; then
    printf '%s\\0' "$@" > {shlex.quote(docker_trace.as_posix())}
    if [[ '{str(result in contents).lower()}' == true ]]; then
      printf '%s' {shlex.quote(contents.get(result, ""))} > {shlex.quote(xml.as_posix())}
    fi
    return {1 if result == "runner_failure" else 0}
  fi
  return 0
}}
{functions}
run_{runner} 6000.3.25f1 {shlex.quote(log.as_posix())}
printf '%s' "$?" > {shlex.quote((tmp_path / "rc").as_posix())}
"""
        command = [executable]
        suffix = ".sh"
    else:
        source = (ROOT / "tools/check-unity-versions.ps1").read_text(encoding="utf-8")
        functions = source[source.index("function Invoke-LocalUnity(") : source.index("$pass = 0;")]
        program = f"""
$ErrorActionPreference = 'Stop'
$RepoRoot = {ps_quote(repo)}
$ProjectPath = {ps_quote(project)}
$PythonBin = {ps_quote(sys.executable)}
$DockerImageTag = 'base-custom'
$Full = ${str(full).lower()}
function Get-UnityBin {{ return {ps_quote(sys.executable)} }}
function Start-Process {{
  param($FilePath, $ArgumentList, [switch]$NoNewWindow, [switch]$PassThru, [switch]$Wait)
  $probeArgs = @('"' + {ps_quote(probe)} + '"') + @($ArgumentList)
  Microsoft.PowerShell.Management\\Start-Process -FilePath $FilePath -ArgumentList $probeArgs -NoNewWindow -PassThru -Wait
}}
function docker {{
  if ($args[0] -eq 'run') {{
    & $PythonBin {ps_quote(docker_probe)} @args
    if (${str(result in contents).lower()}) {{ [IO.File]::WriteAllText({ps_quote(xml)}, {ps_quote(contents.get(result, ""))}) }}
  }}
  $global:LASTEXITCODE = if ($args[0] -eq 'run') {{ {1 if result == "runner_failure" else 0} }} else {{ 0 }}
}}
{functions}
$rc = Invoke-{runner.title()}Unity '6000.3.25f1' {ps_quote(log)}
[IO.File]::WriteAllText({ps_quote(tmp_path / "rc")}, [string]$rc)
"""
        command = [
            executable,
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
        ]
        suffix = ".ps1"
    script = tmp_path / ("runner" + suffix)
    script.write_text(program, encoding="utf-8-sig" if shell == "powershell" else "utf-8")
    env = {name: value for name, value in os.environ.items() if name.upper() != "UNITY_LICENSE"}
    env.update(POWERSHELL_TELEMETRY_OPTOUT="1", POWERSHELL_UPDATECHECK="Off", MSYS_NO_PATHCONV="1")
    completed = subprocess.run(
        [*command, str(script)], cwd=tmp_path, env=env, capture_output=True, text=True, timeout=30
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    rc = int((tmp_path / "rc").read_text(encoding="utf-8"))
    arguments = json.loads(trace.read_text(encoding="utf-8")) if trace.exists() else []
    docker_args = []
    if docker_trace.exists():
        data = docker_trace.read_text(encoding="utf-8")
        docker_args = data.rstrip("\0").split("\0") if shell == "bash" else json.loads(data)
    return rc, arguments, docker_args, project, log


@pytest.mark.parametrize("shell", ["powershell", "pwsh"])
def test_windows_local_runner_preserves_argument_boundaries(tmp_path: Path, shell: str) -> None:
    rc, arguments, _, project, log = run_checker(tmp_path, shell, "local", "missing", full=False)
    assert rc == 0
    assert arguments[arguments.index("-projectPath") + 1] == str(project)
    assert arguments[arguments.index("-logFile") + 1] == str(log)


@pytest.mark.parametrize("shell", ["bash", "powershell", "pwsh"])
@pytest.mark.parametrize("external", [False, True])
def test_docker_mounts_keep_the_project_local_package_resolvable(
    tmp_path: Path, shell: str, external: bool
) -> None:
    rc, _, arguments, project, _ = run_checker(
        tmp_path, shell, "docker", "missing", full=False, external=external
    )
    assert rc == 0
    mounts = [arguments[i + 1].rsplit(":", 1) for i, arg in enumerate(arguments) if arg == "-v"]
    script = arguments[arguments.index("-c") + 1]
    words = shlex.split(script)
    selected = words[words.index("-projectPath") + 1]
    container_project = PurePosixPath(arguments[-1] if selected == "$1" else selected)
    if external:
        assert container_project == PurePosixPath("/project")
    dependencies = json.loads((project / "Packages/manifest.json").read_text())["dependencies"]
    for value in dependencies.values():
        dependency = PurePosixPath(
            os.path.normpath(str(container_project / "Packages" / value[5:])).replace("\\", "/")
        )
        matches = []
        for host, target in mounts:
            if shell == "bash" and os.name == "nt" and host.startswith("/"):
                host = host[1] + ":" + host[2:]
            if dependency.is_relative_to(target):
                matches.append((Path(host), PurePosixPath(target)))
        assert matches, f"Local package {dependency} is outside Docker mounts {mounts}"
        host, target = max(matches, key=lambda mount: len(mount[1].parts))
        assert (host / str(dependency.relative_to(target))).resolve() == (
            project / "Packages" / value[5:]
        ).resolve()


@pytest.mark.parametrize("shell", ["bash", "powershell", "pwsh"])
@pytest.mark.parametrize("runner", ["local", "docker"])
@pytest.mark.parametrize(
    "result", ["missing", "empty", "zero", "failed", "stale", "passing", "runner_failure"]
)
def test_full_runner_requires_fresh_passing_nunit_results(
    tmp_path: Path, shell: str, runner: str, result: str
) -> None:
    rc, arguments, docker_args, _, _ = run_checker(tmp_path, shell, runner, result)
    assert rc == (0 if result == "passing" else 1), f"{runner} accepted {result} test evidence"
    if runner == "local":
        assert "-testResults" in arguments
    else:
        assert "-testResults" in docker_args[docker_args.index("-c") + 1]


@pytest.mark.parametrize("shell", ["bash", "powershell", "pwsh"])
@pytest.mark.parametrize("runner", ["local", "docker"])
@pytest.mark.parametrize("full", [False, True])
def test_runner_exit_is_owned_by_tests_only_in_full_mode(
    tmp_path: Path, shell: str, runner: str, full: bool
) -> None:
    rc, arguments, docker_args, _, _ = run_checker(
        tmp_path, shell, runner, "passing" if full else "missing", full=full
    )
    assert rc == 0
    if runner == "docker":
        arguments = shlex.split(docker_args[docker_args.index("-c") + 1])
    assert ("-runTests" in arguments) == full
    assert ("-quit" in arguments) != full, "-quit can exit before test execution completes"
