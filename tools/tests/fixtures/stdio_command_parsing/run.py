"""Exercise actual stdio parsing/admission methods without launching Unity."""

import argparse
import hashlib
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
HOST = "MCPForUnity/Editor/Services/Transport/Transports/StdioBridgeHost.cs"


def section(source, start, end):
    first = source.index(start)
    return source[first : source.index(end, first)]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--require-reuse", action="store_true")
    args = parser.parse_args()
    output = args.output.resolve()
    if not output.is_relative_to(ROOT / ".tmp"):
        parser.error("--output must be inside repository .tmp")
    output.mkdir(parents=True, exist_ok=True)

    def read(path):
        value = (
            subprocess.check_output(["git", "show", f"{args.baseline}:{path}"], cwd=ROOT)
            if args.baseline
            else (ROOT / path).read_bytes()
        )
        print(f"SOURCE {path} SHA256 {hashlib.sha256(value).hexdigest()}", flush=True)
        return value.decode("utf-8-sig")

    source = read(HOST)
    prepared = "class StdioCommandInput" in source
    parts = [
        section(source, "class QueuedCommand", "    [InitializeOnLoad]"),
        "internal static partial class StdioBridgeHost {",
        section(
            source,
            "internal static int ResolveCommandTimeoutMs",
            "        private static void IoInfo",
        ),
        section(
            source, "private enum ProgressPrelude", "        private static void LogReceivedCommand"
        ),
        section(
            source,
            "private static void ReleaseInput",
            "        private static ulong ReadUInt64BigEndian",
        ),
        section(
            source,
            "private static bool UsesNativeArbitration",
            "        private static object InvokeOnMainThreadWithTimeout",
        ),
        section(
            source, "private static bool IsValidJson", "        public static void WriteHeartbeat"
        ),
        "}",
    ]
    (output / "Host.cs").write_text(
        "using System; using System.Collections.Generic; using System.Diagnostics; "
        "using System.Threading; using System.Threading.Tasks; using Newtonsoft.Json; "
        "using Newtonsoft.Json.Linq; using MCPForUnity.Editor.Helpers; "
        "using MCPForUnity.Editor.Models; using UnityEditor; "
        "namespace MCPForUnity.Editor.Services.Transport.Transports {\n" + "\n".join(parts) + "\n}",
        encoding="utf-8",
    )
    for relative in (
        "MCPForUnity/Editor/Models/Command.cs",
        "MCPForUnity/Editor/Services/Transport/TransportJson.cs",
        "MCPForUnity/Editor/Services/Transport/TransportCommandResponse.cs",
        "MCPForUnity/Editor/Services/Transport/StdioFrameBudget.cs",
        "MCPForUnity/Editor/Helpers/ToolParams.cs",
        "MCPForUnity/Editor/Helpers/ParamCoercion.cs",
        "MCPForUnity/Editor/Helpers/StringCaseUtility.cs",
        "MCPForUnity/Runtime/Serialization/JsonScalarConversion.cs",
    ):
        (output / Path(relative).name).write_text(read(relative), encoding="utf-8")
    for name in ("Program.cs", "Boundary.cs"):
        (output / name).write_text(
            (Path(__file__).parent / name).read_text(encoding="utf-8"), encoding="utf-8"
        )
    dll = (ROOT / ".compile-refs/Newtonsoft.Json.dll").resolve(strict=True).as_posix()
    (output / "Harness.csproj").write_text(
        '<Project Sdk="Microsoft.NET.Sdk"><PropertyGroup>'
        f"<DefineConstants>{'PREPARED' if prepared else ''}</DefineConstants>"
        "<OutputType>Exe</OutputType><TargetFramework>net9.0</TargetFramework>"
        "<Nullable>disable</Nullable><ImplicitUsings>disable</ImplicitUsings>"
        "<NoWarn>CS0649</NoWarn>"
        '</PropertyGroup><ItemGroup><Reference Include="Newtonsoft.Json">'
        f"<HintPath>{dll}</HintPath></Reference></ItemGroup></Project>",
        encoding="utf-8",
    )
    command = ["dotnet", "run", "--project", str(output / "Harness.csproj"), "-c", "Release"]
    if args.require_reuse:
        command += ["--", "--require-reuse"]
    return subprocess.call(command, cwd=ROOT)


if __name__ == "__main__":
    raise SystemExit(main())
