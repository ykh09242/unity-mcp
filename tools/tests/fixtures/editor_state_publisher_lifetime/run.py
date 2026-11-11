"""Run actual publisher, dispatcher, and transport drain with controlled boundaries."""

import argparse
import hashlib
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
PUBLISHER = "MCPForUnity/Editor/Services/EditorStatePublisher.cs"
FRAME = "MCPForUnity/Editor/Services/EditorStateFrame.cs"
DISPATCHER = "MCPForUnity/Editor/Services/Transport/TransportCommandDispatcher.cs"
TRANSPORT = "MCPForUnity/Editor/Services/Transport/Transports/WebSocketTransportClient.cs"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline")
    parser.add_argument("--json-assembly", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    if not output.is_relative_to(ROOT / ".tmp"):
        parser.error("--output must be inside repository .tmp")
    output.mkdir(parents=True, exist_ok=True)
    legacy_publisher = False
    generation_registration = False
    for path, target in (
        (PUBLISHER, "Publisher.cs"),
        (DISPATCHER, "Dispatcher.cs"),
        (TRANSPORT, "Transport.cs"),
    ):
        source = (
            subprocess.check_output(["git", "show", f"{args.baseline}:{path}"], cwd=ROOT).decode()
            if args.baseline
            else (ROOT / path).read_text(encoding="utf-8")
        )
        print(
            f"SOURCE {path} {args.baseline or 'working-tree'} SHA256 {hashlib.sha256(source.encode()).hexdigest()}",
            flush=True,
        )
        if path == PUBLISHER:
            legacy_publisher = "Func<JObject, CancellationToken, Task>" in source
        if path == DISPATCHER:
            start = source.index("internal static Task<T> RunOnMainThreadAsync<T>")
            end = source.index("private static void RequestMainThreadPump()", start)
            method = source[start:end].strip()
            source = (
                """using System;
using System.Collections.Generic;
using System.Threading;
using System.Threading.Tasks;
using MCPForUnity.Editor.Helpers;
using UnityEditor;
namespace MCPForUnity.Editor.Services.Transport;
internal static class TransportCommandDispatcher
{
    private static readonly object PendingLock = new();
    private static readonly LinkedList<Action> MainThreadCallbacks = new();
    private static SynchronizationContext _mainThreadContext;
    private static int _mainThreadId = Thread.CurrentThread.ManagedThreadId;
    internal static void SetContext(SynchronizationContext context) => _mainThreadContext = context;
    internal static int PendingCount { get { lock(PendingLock) return MainThreadCallbacks.Count; } }
    internal static void Pump() {
        while(true) {
            Action action;
            lock(PendingLock) {
                if(MainThreadCallbacks.Count == 0) return;
                action = MainThreadCallbacks.First.Value;
                MainThreadCallbacks.RemoveFirst();
            }
            action();
        }
    }
"""
                + method
                + "\n}\n"
            )
        if path == TRANSPORT:
            generation_registration = "private bool IsCurrentRegistration" in source
            sections = []
            for first, following in (
                ("private sealed class ConnectionLoops", "// Callers hold _ownershipLock"),
                ("private Task CancelOwnedAsync", "public Task<bool> VerifyAsync"),
                ("private bool IsCurrentConnectionToken", "// Caller holds _ownershipLock."),
                (
                    "private bool IsCurrentRegistration"
                    if generation_registration
                    else "private async Task CompleteRegistrationAsync",
                    "private void PersistSessionIdIfCurrent",
                ),
                (
                    "private Task StopCapturedConnectionLoopsAsync",
                    "private void StartBackgroundLoops",
                ),
            ):
                start = source.index(first)
                sections.append(source[start : source.index(following, start)].strip())
            publisher_field = re.search(r"private \w+ _statePublisher;", source).group()
            source = (
                """using System;
using System.Collections.Generic;
using System.Threading;
using System.Threading.Tasks;
using MCPForUnity.Editor.Services;
using MCPForUnity.Editor.Services.Transport;
using Newtonsoft.Json.Linq;
internal partial class TransportLifetimeBoundary
{
"""
                + publisher_field
                + "\n"
                + "\n".join(sections)
                + "\n}\n"
            )
        (output / target).write_text(source, encoding="utf-8")
    (output / "Frame.cs").write_text((ROOT / FRAME).read_text(encoding="utf-8"), encoding="utf-8")
    for name in ("Program.cs", "Boundary.cs"):
        (output / name).write_text((Path(__file__).parent / name).read_text(), encoding="utf-8")
    dll = (
        (args.json_assembly or ROOT / ".compile-refs/Newtonsoft.Json.dll")
        .resolve(strict=True)
        .as_posix()
    )
    defines = ["LEGACY_EDITOR_STATE"] if legacy_publisher else []
    if generation_registration:
        defines.append("GENERATION_REGISTRATION")
    (output / "Harness.csproj").write_text(
        f"""<Project Sdk="Microsoft.NET.Sdk">
<PropertyGroup><DefineConstants>{";".join(defines)}</DefineConstants><OutputType>Exe</OutputType><TargetFramework>net11.0</TargetFramework><Nullable>disable</Nullable><ImplicitUsings>disable</ImplicitUsings><EnableDefaultCompileItems>false</EnableDefaultCompileItems></PropertyGroup>
<ItemGroup><Compile Include="Frame.cs"/><Compile Include="Publisher.cs"/><Compile Include="Dispatcher.cs"/><Compile Include="Transport.cs"/><Compile Include="Program.cs"/><Compile Include="Boundary.cs"/><Reference Include="Newtonsoft.Json"><HintPath>{dll}</HintPath></Reference></ItemGroup>
</Project>
""",
        encoding="utf-8",
    )
    return subprocess.call(
        [
            "dotnet",
            "run",
            "--project",
            str(output / "Harness.csproj"),
            "--configuration",
            "Release",
        ],
        cwd=ROOT,
    )


if __name__ == "__main__":
    raise SystemExit(main())
