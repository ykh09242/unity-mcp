"""Run the native cancellation regressions against exact production ownership members."""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("required", [False, True])
@pytest.mark.parametrize("missing", ["dotnet", "nunit"])
def test_harness_prerequisite_policy(monkeypatch, tmp_path, required, missing):
    # Given a clean runner missing exactly one managed harness prerequisite.
    monkeypatch.setattr(shutil, "which", lambda _: None if missing == "dotnet" else "dotnet")
    monkeypatch.setitem(globals(), "ROOT", tmp_path)
    monkeypatch.delenv("UNITY_MCP_DEADLINE_NUNIT_REFERENCE", raising=False)
    if missing != "nunit":
        reference = tmp_path / ".compile-refs/nunit.framework.dll"
        reference.parent.mkdir()
        reference.write_bytes(b"fixture-only; must not reach compilation")
    if required:
        monkeypatch.setenv("UNITY_MCP_REQUIRE_DEADLINE_HARNESS", "1")
    else:
        monkeypatch.delenv("UNITY_MCP_REQUIRE_DEADLINE_HARNESS", raising=False)

    # When the designated harness is invoked, required CI must fail rather than skip.
    outcome = pytest.fail.Exception if required else pytest.skip.Exception
    with pytest.raises((pytest.fail.Exception, pytest.skip.Exception), match="NUnit|SDK") as raised:
        test_websocket_deadline_callback_ownership(tmp_path)
    assert isinstance(raised.value, outcome)


def test_websocket_deadline_callback_ownership(tmp_path):
    dotnet = shutil.which("dotnet")
    nunit = Path(
        os.environ.get(
            "UNITY_MCP_DEADLINE_NUNIT_REFERENCE", ROOT / ".compile-refs/nunit.framework.dll"
        )
    )
    if not dotnet or not nunit.is_file():
        message = "Existing .NET SDK and .compile-refs NUnit required"
        if os.environ.get("UNITY_MCP_REQUIRE_DEADLINE_HARNESS") == "1":
            pytest.fail(message)
        pytest.skip(message)
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
    source = (
        ROOT / "MCPForUnity/Editor/Services/Transport/Transports/WebSocketTransportClient.cs"
    ).read_text(encoding="utf-8")
    members = []
    for start, end in (
        (
            "        private sealed class CommandAdmission",
            "        private sealed class ConnectionLoops",
        ),
        (
            "        internal static double CommandDeadlineRemainingMilliseconds",
            "        private async Task HandleExecuteAsync",
        ),
        (
            "        private sealed class QueuedCommand",
            "        private async Task ExecuteQueuedCommandAsync",
        ),
    ):
        offset = source.index(start)
        members.append(source[offset : source.index(end, offset)])
    stdio_source = (
        ROOT / "MCPForUnity/Editor/Services/Transport/Transports/StdioBridgeHost.cs"
    ).read_text(encoding="utf-8")
    stdio_start = stdio_source.index("        private sealed class CommandCancellation")
    stdio_members = stdio_source[
        stdio_start : stdio_source.index(
            "        private static void ExecuteQueuedCommand", stdio_start
        )
    ]
    template = (
        ROOT / "tools/tests/fixtures/transport_disposal/WebSocketDeadlineHarness.cs"
    ).read_text(encoding="utf-8")
    harness = tmp_path / "Harness.cs"
    harness.write_text(
        template.replace("// PRODUCTION_MEMBERS", "\n".join(members)).replace(
            "// STDIO_PRODUCTION_MEMBERS", stdio_members
        ),
        encoding="utf-8",
    )
    source_paths = [
        harness,
        ROOT / "MCPForUnity/Editor/Services/Transport/ConnectionCommandWork.cs",
        ROOT
        / "TestProjects/UnityMCPTests/Assets/Tests/EditMode/Services/CooperativeCancellationTests.cs",
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
                f'/reference:"{nunit}"',
                *(f'"{path}"' for path in source_paths),
            ]
        ),
        encoding="utf-8",
    )
    compiled = subprocess.run(
        [dotnet, str(sdk / "Roslyn/bincore/csc.dll"), "/noconfig", f"@{response}"],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert compiled.returncode == 0, compiled.stdout + compiled.stderr
    shutil.copy2(nunit, tmp_path / nunit.name)
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
    result = subprocess.run([dotnet, str(output)], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    assert (
        "PASS CancellationSettlement_RejectsLateCallbacksAndJoinsConcurrentCallbacks(False)"
        in result.stdout
    )
    assert (
        "PASS CancellationSettlement_RejectsLateCallbacksAndJoinsConcurrentCallbacks(True)"
        in result.stdout
    )
    assert "PASS WebSocket deadline ownership" in result.stdout
