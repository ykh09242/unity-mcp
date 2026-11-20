"""Exercise the SDK process boundary and its failure paths without crashing Windows."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import run_dotnet


@pytest.mark.parametrize("bad", ["missing", "file"])
def test_invalid_inherited_temp_prevents_process_start(tmp_path, monkeypatch, capsys, bad):
    invalid = tmp_path / bad
    if bad == "file":
        invalid.touch()
    monkeypatch.setenv("TMP" if os.name == "nt" else "TMPDIR", str(invalid))
    monkeypatch.setattr(subprocess, "run", lambda *_a, **_k: pytest.fail("SDK started"))
    assert run_dotnet.main(["restore", "Example.csproj"]) == 1
    assert str(invalid) in capsys.readouterr().err


@pytest.mark.parametrize("option", ["--artifacts-path", "--output", "-o", "--results-directory"])
def test_invalid_artifact_directory_prevents_process_start(tmp_path, monkeypatch, capsys, option):
    parent = tmp_path / "scratch"
    parent.mkdir()
    collision = tmp_path / "file"
    collision.touch()
    monkeypatch.setattr(subprocess, "run", lambda *_a, **_k: pytest.fail("SDK started"))
    assert run_dotnet.main(["--temp-parent", str(parent), "test", option, str(collision)]) == 1
    assert str(collision) in capsys.readouterr().err
    assert list(parent.iterdir()) == []


@pytest.mark.parametrize("command", run_dotnet.COMMANDS)
def test_build_exits_and_arguments_are_preserved(tmp_path, monkeypatch, command):
    original = dict(os.environ)
    artifacts = tmp_path / "build outputs"
    app_path = tmp_path / "application-only argument"
    arguments = [f"--artifacts-path={artifacts}", "--", "--artifacts-path", str(app_path)]
    observed = []

    def execute(argv, *, env, check):
        stage = Path(env["TMP"])
        assert stage.parent == tmp_path and stage.is_dir()
        assert env["TMP"] == env["TEMP"] == env["TMPDIR"]
        assert env["MSBUILDDISABLENODEREUSE"] == "1" and env["UseSharedCompilation"] == "false"
        assert env["MSBUILDUSESERVER"] == "0"
        assert artifacts.is_dir() and not app_path.exists()
        assert argv[:2] == ["dotnet", command] and argv[-len(arguments) :] == arguments
        switches = argv[2 : -len(arguments)]
        assert ("--disable-build-servers" in switches) == (command != "msbuild")
        assert ("-m:1" in switches) == ("-nr:false" in switches) == (command != "run")
        assert check is False
        observed.append(stage)
        return subprocess.CompletedProcess(argv, 7)

    monkeypatch.setattr(subprocess, "run", execute)
    for _ in range(2):
        assert run_dotnet.main(["--temp-parent", str(tmp_path), command, *arguments]) == 7
    assert observed[0] != observed[1] and all(not stage.exists() for stage in observed)
    assert dict(os.environ) == original


@pytest.mark.skipif(not shutil.which("pwsh"), reason="PowerShell fixture runner is unavailable")
def test_standalone_fixture_forwards_program_arguments_or_refuses_bad_temp(tmp_path):
    launcher = Path(run_dotnet.__file__).with_name("Invoke-DotnetFixture.ps1")
    script = """
function dotnet {
    $args | ConvertTo-Json -Compress
    $global:LASTEXITCODE = 7
}
if ($env:FIXTURE_BAD_TEMP) {
    $env:TMP = $env:FIXTURE_BAD_TEMP
    $env:TEMP = $env:FIXTURE_BAD_TEMP
    $env:TMPDIR = $env:FIXTURE_BAD_TEMP
}
$fixtureArgs = @('--project', 'a path/Test.csproj')
if ($env:FIXTURE_OUTPUT) { $fixtureArgs += "--property:OutputPath=$env:FIXTURE_OUTPUT" }
$fixtureArgs += @('--', '-x', 'a b')
& $env:FIXTURE_LAUNCHER @fixtureArgs
exit $LASTEXITCODE
"""
    environment = {**os.environ, "FIXTURE_LAUNCHER": str(launcher)}
    shell = [shutil.which("pwsh"), "-NoProfile", "-NonInteractive", "-Command", script]
    positive = subprocess.run(shell, capture_output=True, env=environment, timeout=30)
    assert positive.returncode == 7, positive.stderr
    assert json.loads(positive.stdout) == [
        "run",
        "--disable-build-servers",
        "--project",
        "a path/Test.csproj",
        "--",
        "-x",
        "a b",
    ]
    collision = tmp_path / "file"
    collision.touch()
    environment["FIXTURE_BAD_TEMP"] = str(collision)
    rejected = subprocess.run(shell, capture_output=True, env=environment, timeout=30)
    assert rejected.returncode == 1 and b"Cannot prepare .NET fixture" in rejected.stderr
    assert not rejected.stdout
    del environment["FIXTURE_BAD_TEMP"]
    environment["FIXTURE_OUTPUT"] = str(collision)
    rejected = subprocess.run(shell, capture_output=True, env=environment, timeout=30)
    assert rejected.returncode == 1 and b"Cannot prepare .NET fixture" in rejected.stderr
    assert not rejected.stdout
