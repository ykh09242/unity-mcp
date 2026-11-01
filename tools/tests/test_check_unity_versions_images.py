"""Hermetic local Docker runners: extract functions and replace every Docker call."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[2]
STABLE = "unityci/editor:ubuntu-6000.3.25f1-base-3@sha256:" + "a" * 64
PREVIEW = "unity-mcp-editor:6000.7.0b2"


def find_shell(shell):
    found = shutil.which(shell)
    if not found and shell == "bash" and os.name == "nt":
        candidate = Path("C:/Program Files/Git/bin/bash.exe")
        if candidate.exists():
            found = str(candidate)
    if not found:
        pytest.skip(f"{shell} is not available")
    return found


def scenario_program(tmp_path: Path, shell: str, scenario: str) -> str:
    version = "6000.7.0b2" if scenario in {"preview", "prepare_failure"} else "6000.3.25f1"
    tag = "base-3.2.2" if scenario == "override" else "base-3"
    image = PREVIEW if version.endswith("b2") else STABLE
    tools = tmp_path / "tools"
    tools.mkdir()
    helper_trace = tmp_path / "helper.json"
    docker_trace = tmp_path / "docker.txt"
    helper = (
        "import json, pathlib, sys\n"
        f"pathlib.Path({str(helper_trace)!r}).write_text(json.dumps(sys.argv[1:]))\n"
        "print('controlled preparation diagnostic', file=sys.stderr)\n"
        f"print({image!r})\n"
        f"sys.exit({1 if scenario == 'prepare_failure' else 0})\n"
    )
    (tools / "unity_ci.py").write_text(helper, encoding="utf-8")
    log = tmp_path / "unity.log"
    log.write_text("")
    if shell == "bash":
        text = (ROOT / "tools/check-unity-versions.sh").read_text(encoding="utf-8")
        function = text[text.index("run_docker() {") : text.index("# ---- main loop")]
        program = f"""
REPO_ROOT='{tmp_path.as_posix()}'
PROJECT_PATH='{tmp_path.as_posix()}'
PYTHON_BIN='{Path(sys.executable).as_posix()}'
DOCKER_IMAGE_TAG='{tag}'
FULL=0
C_FAIL= C_DIM= C_RST=
docker() {{
  local operation="$1" arg
  for arg in "$@"; do
    case "$arg" in unityci/*|unity-mcp-editor:*) printf '%s:%s\\n' "$operation" "$arg" >> '{docker_trace.as_posix()}' ;; esac
  done
  return 0
}}
{function}
run_docker '{version}' '{log.as_posix()}'
printf '%s' "$?" > '{(tmp_path / "returncode").as_posix()}'
"""
    else:
        text = (ROOT / "tools/check-unity-versions.ps1").read_text(encoding="utf-8")
        function = text[text.index("function Invoke-DockerUnity(") : text.index("$pass = 0;")]
        quote = lambda value: "'" + str(value).replace("'", "''") + "'"
        program = f"""
$ErrorActionPreference = 'Stop'
$RepoRoot = {quote(tmp_path)}
$ProjectPath = {quote(tmp_path)}
$PythonBin = {quote(sys.executable)}
$DockerImageTag = {quote(tag)}
$Full = $false
function docker {{
  foreach ($argument in $args) {{
    if ($argument -like 'unityci/*' -or $argument -like 'unity-mcp-editor:*') {{
      [System.IO.File]::AppendAllText({quote(docker_trace)}, "$($args[0]):$argument`n")
    }}
  }}
  $global:LASTEXITCODE = 0
}}
{function}
$result = Invoke-DockerUnity {quote(version)} {quote(log)}
[System.IO.File]::WriteAllText({quote(tmp_path / "returncode")}, [string]$result)
"""
    return program


@pytest.fixture(scope="module")
def shell(request, tmp_path_factory):
    """Reuse shell startup, keeping every scenario's files and result independent."""
    name = request.param
    executable = find_shell(name)
    root = tmp_path_factory.mktemp(f"docker-{name}")
    cases = {}
    programs = []
    # Run the failure before successes so it cannot silently abort the remaining cases.
    for scenario in ("prepare_failure", "stable", "preview", "override"):
        directory = root / scenario
        directory.mkdir()
        cases[scenario] = directory
        program = scenario_program(directory, name, scenario)
        # PowerShell scope and native exit state must not leak between cases.
        programs.append(program if name == "bash" else "& {\n" + program + "\n}")
    program = "\n".join(programs)
    if name == "bash":
        script = root / "runner.sh"
        script.write_text(program, encoding="utf-8")
        command = [executable, str(script)]
    else:
        script = root / "runner.ps1"
        script.write_text(program + "\nexit 0\n", encoding="utf-8")
        command = [
            executable,
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(script),
        ]
    env = {
        name: os.environ[name]
        for name in (
            "PATH",
            "SystemRoot",
            "TEMP",
            "TMP",
            "HOME",
            "USERPROFILE",
            "APPDATA",
            "LOCALAPPDATA",
            "COMSPEC",
            "PATHEXT",
            "SYSTEMDRIVE",
            "PROGRAMDATA",
            "ALLUSERSPROFILE",
        )
        if name in os.environ
    }
    # These controls must exist before PowerShell starts, including in stripped environments.
    env.update(POWERSHELL_TELEMETRY_OPTOUT="1", POWERSHELL_UPDATECHECK="Off")
    result = subprocess.run(command, cwd=root, env=env, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    return cases, result


@pytest.mark.parametrize("shell", ["bash", "powershell", "pwsh"], indirect=True, scope="module")
@pytest.mark.parametrize("scenario", ["stable", "preview", "override", "prepare_failure"])
def test_local_docker_runner_resolves_default_images(shell, scenario):
    cases, result = shell
    tmp_path = cases[scenario]
    helper_trace = tmp_path / "helper.json"
    docker_trace = tmp_path / "docker.txt"
    returncode = int((tmp_path / "returncode").read_text())
    version = "6000.7.0b2" if scenario in {"preview", "prepare_failure"} else "6000.3.25f1"
    tag = "base-3.2.2" if scenario == "override" else "base-3"
    image = PREVIEW if version.endswith("b2") else STABLE
    expected_image = f"unityci/editor:ubuntu-{version}-{tag}" if scenario == "override" else image
    calls = docker_trace.read_text().splitlines() if docker_trace.exists() else []
    if scenario == "prepare_failure":
        assert returncode == 1, result.stdout + result.stderr
        assert calls == []
    else:
        assert returncode == 0, result.stdout + result.stderr
        expected = [] if scenario == "preview" else [f"pull:{expected_image}"]
        assert calls == [*expected, f"run:{expected_image}"]
    if scenario == "override":
        assert not helper_trace.exists()
    else:
        assert json.loads(helper_trace.read_text()) == ["prepare", version]
