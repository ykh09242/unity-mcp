"""Run source-linked managed-launch recovery against controlled persistent identities."""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def installed_versions(root):
    if not root.is_dir():
        return []
    return [
        (tuple(map(int, path.name.split("."))), path)
        for path in root.iterdir()
        if path.is_dir() and re.fullmatch(r"\d+\.\d+\.\d+", path.name)
    ]


@pytest.fixture(scope="module")
def identity_harness(tmp_path_factory):
    tmp_path = tmp_path_factory.mktemp("identity-harness")
    dotnet = shutil.which("dotnet")
    if not dotnet:
        pytest.skip("Existing .NET SDK required for process identity regression")
    sdk_matches = [
        match
        for line in subprocess.check_output([dotnet, "--list-sdks"], text=True).splitlines()
        if (match := re.fullmatch(r"(\d+\.\d+\.\d+) \[(.+)\]", line))
    ]
    if not sdk_matches:
        pytest.skip("Existing stable .NET SDK required; no restore is run")
    sdk = max(
        (tuple(map(int, item[1].split("."))), Path(item[2]) / item[1]) for item in sdk_matches
    )[1]
    runtimes = installed_versions(sdk.parent.parent / "shared/Microsoft.NETCore.App")
    profiles = []
    for version, pack in installed_versions(sdk.parent.parent / "packs/Microsoft.NETCore.App.Ref"):
        compatible = [(value, path) for value, path in runtimes if value[0] == version[0]]
        references = pack / "ref" / f"net{version[0]}.0"
        if compatible and references.is_dir():
            profiles.append((version, references, max(compatible)[1]))
    compiler = sdk / "Roslyn/bincore/csc.dll"
    if not profiles or not compiler.is_file():
        pytest.skip("Existing Roslyn, stable reference pack and matching runtime required")
    _, references, runtime = max(profiles)
    source = (ROOT / "MCPForUnity/Editor/Services/ServerManagementService.cs").read_text("utf-8")
    start = source.index("        private bool TryPreparePreviousManagedLaunch(")
    end = source.index("        /// <summary>", start)
    fixture = (ROOT / "tools/tests/fixtures/managed_process_identity/IdentityHarness.cs").read_text(
        "utf-8"
    )
    harness = tmp_path / "Harness.cs"
    harness.write_text(fixture.replace("// PRODUCTION_RECOVERY", source[start:end]), "utf-8")
    sources = [
        harness,
        ROOT / "MCPForUnity/Editor/Services/Server/PidFileManager.cs",
        ROOT / "MCPForUnity/Editor/Services/Server/IPidFileManager.cs",
        ROOT / "MCPForUnity/Editor/Services/Server/IProcessDetector.cs",
        ROOT / "MCPForUnity/Editor/Constants/EditorPrefKeys.cs",
    ]
    output = tmp_path / "Harness.dll"
    response = tmp_path / "compile.rsp"
    response.write_text(
        "\n".join(
            [
                "/nologo",
                "/noconfig",
                "/nostdlib+",
                "/target:exe",
                "/langversion:latest",
                f'/out:"{output}"',
                *(f'/reference:"{path}"' for path in references.glob("*.dll")),
                *(f'"{path}"' for path in sources),
            ]
        ),
        "utf-8",
    )
    compiled = subprocess.run(
        [dotnet, str(compiler), f"@{response}"],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert compiled.returncode == 0, compiled.stdout + compiled.stderr
    output.with_suffix(".runtimeconfig.json").write_text(
        json.dumps(
            {
                "runtimeOptions": {
                    "framework": {"name": "Microsoft.NETCore.App", "version": runtime.name}
                }
            }
        ),
        "utf-8",
    )
    return [dotnet, str(output)]


@pytest.mark.parametrize("role", ["launcher", "server"])
@pytest.mark.parametrize("identity", ["reused", "current", "unknown", "legacy"])
def test_managed_recovery_fences_creation_identity(identity_harness, tmp_path, role, identity):
    result = subprocess.run(
        [*identity_harness, str(tmp_path), role, identity],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert f"PASS {role} {identity}" in result.stdout


def test_unusable_identity_directory_exits_with_diagnostic(identity_harness, tmp_path):
    blocked_root = tmp_path / "file-in-place-of-directory"
    blocked_root.touch()

    result = subprocess.run(
        [*identity_harness, str(blocked_root), "launcher", "current"],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )

    assert result.returncode == 1, result.stdout + result.stderr
    assert "at System.IO.Directory.CreateDirectory" in result.stderr
    assert "at MCPForUnity.Editor.Services.Server.PidFileManager.GetPidFilePath" in result.stderr
    assert "PASS" not in result.stdout
