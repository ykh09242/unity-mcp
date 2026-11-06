"""Exercise production tool-settings observer boundaries without launching Unity."""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def stable_directories(root):
    """Ignore preview and unrelated directories when selecting installed frameworks."""
    if not root.is_dir():
        return []
    return [
        (tuple(map(int, path.name.split("."))), path)
        for path in root.iterdir()
        if path.is_dir() and re.fullmatch(r"\d+\.\d+\.\d+", path.name)
    ]


@pytest.mark.parametrize("case", ["transitions", "dispose", "pending"])
def test_status_observers_cannot_interrupt_owned_work(tmp_path, case):
    dotnet = shutil.which("dotnet")
    if not dotnet:
        pytest.skip("Existing .NET SDK required for the tool-settings observer regression")
    sdks = subprocess.check_output([dotnet, "--list-sdks"], text=True).splitlines()
    installed = []
    for line in sdks:
        match = re.fullmatch(r"(\d+\.\d+\.\d+) \[(.+)\]", line)
        if match:
            installed.append((tuple(map(int, match[1].split("."))), Path(match[2]) / match[1]))
    if not installed:
        pytest.skip("An existing stable .NET SDK is required; no SDK is installed by this test")
    _, sdk = max(installed)
    compiler = sdk / "Roslyn/bincore/csc.dll"
    json_library = sdk / "Newtonsoft.Json.dll"
    if not compiler.is_file() or not json_library.is_file():
        pytest.skip("The existing SDK must include Roslyn and Newtonsoft.Json; no restore is run")
    runtimes = stable_directories(sdk.parent.parent / "shared/Microsoft.NETCore.App")
    profiles = []
    for version, pack in stable_directories(sdk.parent.parent / "packs/Microsoft.NETCore.App.Ref"):
        references = pack / "ref" / f"net{version[0]}.0"
        compatible = [
            (runtime_version, path)
            for runtime_version, path in runtimes
            if runtime_version[0] == version[0]
        ]
        if version[0] >= 6 and references.is_dir() and compatible:
            profiles.append((version, references, max(compatible)[1]))
    if not profiles:
        pytest.skip("An existing stable .NET 6+ reference pack and matching runtime are required")
    _, references, runtime = max(profiles)
    shutil.copy2(json_library, tmp_path / json_library.name)
    sources = [
        ROOT / "MCPForUnity/Editor/Services/ToolSettingsState.cs",
        ROOT / "MCPForUnity/Editor/Services/ToolSettingsRegistration.cs",
        ROOT / "tools/tests/fixtures/tool_settings_observers/ObserverHarness.cs",
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
                f'/reference:"{json_library}"',
                *(f'"{path}"' for path in sources),
            ]
        ),
        encoding="utf-8",
    )
    compiled = subprocess.run(
        [dotnet, str(compiler), f"@{response}"],
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
        [dotnet, str(output), case], check=False, capture_output=True, text=True, timeout=20
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert f"PASS {case}" in result.stdout
