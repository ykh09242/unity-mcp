"""Run actual publisher and dispatcher method with a synchronous observation boundary."""

import argparse
import hashlib
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
PUBLISHER = "MCPForUnity/Editor/Services/EditorStatePublisher.cs"
DISPATCHER = "MCPForUnity/Editor/Services/Transport/TransportCommandDispatcher.cs"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    if not output.is_relative_to(ROOT / ".tmp"):
        parser.error("--output must be inside repository .tmp")
    output.mkdir(parents=True, exist_ok=True)
    for path, target in ((PUBLISHER, "Publisher.cs"), (DISPATCHER, "Dispatcher.cs")):
        source = (
            subprocess.check_output(["git", "show", f"{args.baseline}:{path}"], cwd=ROOT).decode()
            if args.baseline
            else (ROOT / path).read_text(encoding="utf-8")
        )
        print(
            f"SOURCE {path} {args.baseline or 'working-tree'} SHA256 {hashlib.sha256(source.encode()).hexdigest()}",
            flush=True,
        )
        if path == DISPATCHER:
            start = source.index("internal static Task<T> RunOnMainThreadAsync<T>")
            end = source.index("private static void RequestMainThreadPump()", start)
            method = source[start:end].strip()
            source = (
                """using System;
using System.Collections.Generic;
using System.Threading;
using System.Threading.Tasks;
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
        (output / target).write_text(source, encoding="utf-8")
    for name in ("Program.cs", "Boundary.cs"):
        (output / name).write_text((Path(__file__).parent / name).read_text(), encoding="utf-8")
    dll = (ROOT / ".compile-refs/Newtonsoft.Json.dll").as_posix()
    (output / "Harness.csproj").write_text(
        f"""<Project Sdk="Microsoft.NET.Sdk">
<PropertyGroup><OutputType>Exe</OutputType><TargetFramework>net9.0</TargetFramework><Nullable>disable</Nullable><ImplicitUsings>disable</ImplicitUsings><EnableDefaultCompileItems>false</EnableDefaultCompileItems></PropertyGroup>
<ItemGroup><Compile Include="Publisher.cs"/><Compile Include="Dispatcher.cs"/><Compile Include="Program.cs"/><Compile Include="Boundary.cs"/><Reference Include="Newtonsoft.Json"><HintPath>{dll}</HintPath></Reference></ItemGroup>
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
