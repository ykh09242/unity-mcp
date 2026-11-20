"""Exercise the SDK process boundary and its failure paths without crashing Windows."""

from contextlib import ExitStack
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
from zipfile import ZipFile

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import run_dotnet
from player_e2e_process import stop_owned_tree


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
        assert stage == tmp_path and stage.is_dir()
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
    assert observed == [tmp_path, tmp_path]
    assert list(tmp_path.iterdir()) == [artifacts]
    assert dict(os.environ) == original


@pytest.mark.skipif(not shutil.which("dotnet"), reason="The .NET SDK is unavailable")
@pytest.mark.parametrize("scratch_override", [False, True], ids=["default-scratch", "override"])
def test_msbuild_uses_the_same_nuget_lock_directory_as_other_restores(
    tmp_path, monkeypatch, scratch_override
):
    for key in ("TMP", "TEMP", "TMPDIR"):
        monkeypatch.setenv(key, str(tmp_path))
    if scratch_override:
        monkeypatch.setenv("NUGET_SCRATCH", str(tmp_path / "explicit scratch"))
    else:
        monkeypatch.delenv("NUGET_SCRATCH", raising=False)
    command = ["dotnet", "nuget", "locals", "temp", "--list"]
    expected = subprocess.check_output(command, text=True, timeout=30).strip().splitlines()[-1]
    project = tmp_path / "cache.proj"
    project.write_text(
        '<Project><Target Name="Observe"><Exec Command="dotnet nuget locals temp --list" ConsoleToMSBuild="true">'
        '<Output TaskParameter="ConsoleOutput" ItemName="Scratch" /></Exec>'
        '<WriteLinesToFile File="$(Receipt)" Lines="@(Scratch)" Overwrite="true" />'
        "</Target></Project>",
        encoding="utf-8",
    )
    for index in range(2):
        receipt = tmp_path / f"receipt-{index}.txt"
        assert (
            run_dotnet.main(
                ["msbuild", str(project), "-t:Observe", f"-p:Receipt={receipt}", "-nologo"]
            )
            == 0
        )
        assert receipt.read_text(encoding="utf-8-sig").strip().splitlines()[-1] == expected


def _wait_for_restore_log(process, path, marker):
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        output = path.read_bytes()
        if marker in output:
            return
        if process.poll() is not None:
            break
        time.sleep(0.05)
    pytest.fail(f"Missing {marker!r} in {path}: {path.read_bytes()[-4000:]!r}")


@pytest.mark.skipif(not shutil.which("dotnet"), reason="The .NET SDK is unavailable")
@pytest.mark.parametrize("scratch_override", [False, True], ids=["default-scratch", "override"])
def test_overlapping_restores_survive_cancellation_and_retry(tmp_path, scratch_override):
    package = "restoreprobe.1.0.0.nupkg"
    cache = tmp_path / "packages"
    installed = cache / "restoreprobe" / "1.0.0"
    feed = tmp_path / "feed"
    feed.mkdir()
    with ZipFile(feed / package, "w") as archive:
        archive.writestr(
            "restoreprobe.nuspec",
            "<package><metadata><id>restoreprobe</id><version>1.0.0</version>"
            "<authors>fixture</authors><description>Local restore probe</description>"
            "</metadata></package>",
        )
        archive.writestr("content/receipt.txt", b"complete package\n")
    config = tmp_path / "NuGet.Config"
    config.write_text(
        '<configuration><packageSources><clear/><add key="probe" value="feed"/>'
        "</packageSources></configuration>",
        encoding="utf-8",
    )
    environment = {
        **os.environ,
        "NUGET_PACKAGES": str(cache),
        "DOTNET_CLI_UI_LANGUAGE": "en-US",
        "DOTNET_CLI_TELEMETRY_OPTOUT": "1",
        "DOTNET_NOLOGO": "1",
    }
    for key in ("TMP", "TEMP", "TMPDIR", "DOTNET_CLI_HOME", "HOME"):
        environment[key] = str(tmp_path)
    for key in ("NUGET_HTTP_CACHE_PATH", "NUGET_PLUGINS_CACHE_PATH", "XDG_DATA_HOME"):
        environment[key] = str(tmp_path / key)
    if scratch_override:
        environment["NUGET_SCRATCH"] = str(tmp_path / "explicit scratch")
    else:
        environment.pop("NUGET_SCRATCH", None)

    def restore(directory):
        project = tmp_path / directory / "Consumer.csproj"
        project.parent.mkdir(exist_ok=True)
        project.write_text(
            '<Project Sdk="Microsoft.NET.Sdk"><PropertyGroup><TargetFramework>net8.0</TargetFramework>'
            "<DisableImplicitFrameworkReferences>true</DisableImplicitFrameworkReferences>"
            "<NuGetAudit>false</NuGetAudit></PropertyGroup><ItemGroup>"
            '<PackageReference Include="restoreprobe" Version="1.0.0" /></ItemGroup></Project>',
            encoding="utf-8",
        )
        return ["restore", str(project), "--configfile", str(config), "-v:detailed"]

    with ExitStack() as children:

        def start(name, arguments):
            log = tmp_path / f"{name}.log"
            with log.open("wb") as stream:
                process = subprocess.Popen(
                    arguments,
                    cwd=tmp_path,
                    env=environment,
                    stdout=stream,
                    stderr=subprocess.STDOUT,
                    start_new_session=os.name != "nt",
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
            children.callback(stop_owned_tree, process)
            return process, log

        # An ordinary SDK invocation holds NuGet's real installation lock. Both wrapped
        # restores must reach that same lock with an empty package cache.
        fixture = Path(__file__).parent / "fixtures" / "nuget_restore" / "HoldPackageLock.proj"
        lock, lock_log = start(
            "lock",
            [
                "dotnet",
                "msbuild",
                str(fixture.resolve()),
                "-nologo",
                "-nr:false",
                "-m:1",
                f"-p:PackagePath={installed / package}",
                f"-p:GateDirectory={tmp_path}",
            ],
        )
        _wait_for_restore_log(lock, lock_log, b"Holding package installation lock")
        children.callback((tmp_path / "release").touch)

        launcher = [sys.executable, run_dotnet.__file__]
        first_command = [*launcher, *restore("first")]
        first, first_log = start("first", first_command)
        second, second_log = start("second", [*launcher, *restore("second")])
        waiting = b"Acquiring lock for the installation of restoreprobe 1.0.0"
        for process, log in ((first, first_log), (second, second_log)):
            _wait_for_restore_log(process, log, waiting)
        assert first.poll() is None and second.poll() is None
        assert not installed.exists()
        stop_owned_tree(first)
        assert first.returncode != 0 and not (tmp_path / "first/obj/project.assets.json").exists()
        (tmp_path / "release").touch()
        assert lock.wait(timeout=30) == 0, lock_log.read_bytes()
        assert second.wait(timeout=30) == 0, second_log.read_bytes()
        retry, retry_log = start("retry", first_command)
        assert retry.wait(timeout=30) == 0, retry_log.read_bytes()

    for directory in ("first", "second"):
        assets = json.loads((tmp_path / directory / "obj/project.assets.json").read_bytes())
        assert "restoreprobe/1.0.0" in assets["libraries"]
        assert [Path(folder) for folder in assets["packageFolders"]] == [cache]
    assert (installed / package).read_bytes() == (feed / package).read_bytes()
    assert (installed / "content/receipt.txt").read_bytes() == b"complete package\n"
    assert (installed / ".nupkg.metadata").is_file()


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
