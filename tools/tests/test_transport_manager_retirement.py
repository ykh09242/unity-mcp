"""Compile the complete manager and locator against controlled external services."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def test_manager_and_locator_retire_previous_owners(tmp_path):
    dotnet = shutil.which("dotnet")
    if not dotnet:
        pytest.skip("Existing .NET SDK required for the manager ownership regression")
    version, directory = (
        subprocess.check_output([dotnet, "--list-sdks"], text=True).splitlines()[-1].split(" ", 1)
    )
    sdk = Path(directory.strip("[]")) / version
    runtime = max(
        (sdk.parent.parent / "shared/Microsoft.NETCore.App").iterdir(),
        key=lambda path: tuple(map(int, path.name.split("."))),
    )
    pack = max(
        (sdk.parent.parent / "packs/Microsoft.NETCore.App.Ref").iterdir(),
        key=lambda path: tuple(map(int, path.name.split("."))),
    )
    references = pack / "ref" / f"net{pack.name.split('.')[0]}.0"
    source_paths = [
        ROOT / "MCPForUnity/Editor/Services/Transport/TransportManager.cs",
        ROOT / "MCPForUnity/Editor/Services/Transport/TransportState.cs",
        ROOT / "MCPForUnity/Editor/Services/Transport/IMcpTransportClient.cs",
        ROOT / "MCPForUnity/Editor/Services/MCPServiceLocator.cs",
        ROOT / "tools/tests/fixtures/transport_disposal/ManagerRetirementHarness.cs",
    ]
    output = tmp_path / "Harness.dll"
    response = tmp_path / "compile.rsp"
    response.write_text(
        "\n".join(
            [
                "/nologo",
                "/nostdlib+",
                "/target:exe",
                "/langversion:latest",
                f'/out:"{output}"',
                *(f'/reference:"{path}"' for path in references.glob("*.dll")),
                *(f'"{path}"' for path in source_paths),
            ]
        ),
        encoding="utf-8",
    )
    compiled = subprocess.run(
        [dotnet, str(sdk / "Roslyn/bincore/csc.dll"), "/noconfig", f"@{response}"],
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert compiled.returncode == 0, compiled.stdout + compiled.stderr
    output.with_suffix(".runtimeconfig.json").write_text(
        json.dumps(
            {
                "runtimeOptions": {
                    "tfm": f"net{runtime.name.split('.')[0]}.0",
                    "framework": {"name": "Microsoft.NETCore.App", "version": runtime.name},
                }
            }
        ),
        encoding="utf-8",
    )
    result = subprocess.run(
        [dotnet, str(output)], check=False, capture_output=True, text=True, timeout=20
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "PASS manager and locator retirement" in result.stdout
