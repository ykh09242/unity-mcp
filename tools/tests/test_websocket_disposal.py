"""Run production send/disposal methods with controlled ownership boundaries on .NET."""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "MCPForUnity/Editor/Services/Transport/Transports/WebSocketTransportClient.cs"


def test_queued_sends_retain_semaphore_until_disposal_settles(tmp_path):
    dotnet = shutil.which("dotnet")
    if not dotnet:
        pytest.skip("Existing .NET SDK required for the transport lifecycle boundary regression")
    sdks = subprocess.check_output([dotnet, "--list-sdks"], text=True).splitlines()
    version, directory = sdks[-1].split(" ", 1)
    sdk = Path(directory.strip("[]")) / version
    runtime_root = sdk.parent.parent / "shared/Microsoft.NETCore.App"
    runtime = max(runtime_root.iterdir(), key=lambda path: tuple(map(int, path.name.split("."))))
    reference_root = sdk.parent.parent / "packs/Microsoft.NETCore.App.Ref"
    reference_pack = max(
        reference_root.iterdir(), key=lambda path: tuple(map(int, path.name.split(".")))
    )
    references = reference_pack / "ref" / f"net{reference_pack.name.split('.')[0]}.0"
    source = SOURCE.read_text(encoding="utf-8")
    methods = []
    for name in (
        "Dispose",
        "DisposeSendResourcesAsync",
        "SendOwnedFrameAsync",
        "IsCurrentConnectionToken",
    ):
        match = re.search(
            rf"^        (?:public|private) (?:async )?[^\n]*\b{name}\([^\n]*\)\n"
            rf"        \{{.*?^        \}}$",
            source,
            re.MULTILINE | re.DOTALL,
        )
        if match:
            methods.append(match.group())
    fields = []
    for name in (
        "_ownershipLock",
        "_sendLock",
        "_disposed",
        "_activeSends",
        "_sendCompletion",
        "_disposeSettlement",
    ):
        match = re.search(rf"^        private [^\n]*\b{name}\b[^\n]*;", source, re.MULTILINE)
        if match:
            fields.append(match.group())
    fixture = (ROOT / "tools/tests/fixtures/transport_disposal/SendDisposalHarness.cs").read_text(
        encoding="utf-8"
    )
    harness = tmp_path / "Harness.cs"
    harness.write_text(
        fixture.replace("// PRODUCTION_MEMBERS", "\n".join(fields + methods)), encoding="utf-8"
    )
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
                f'"{harness}"',
            ]
        ),
        encoding="utf-8",
    )
    compile_result = subprocess.run(
        [dotnet, str(sdk / "Roslyn/bincore/csc.dll"), f"@{response}"],
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert compile_result.returncode == 0, compile_result.stdout + compile_result.stderr
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
    assert "PASS queued send disposal" in result.stdout
