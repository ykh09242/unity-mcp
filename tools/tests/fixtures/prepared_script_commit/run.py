"""Exercise the actual prepared-script commit method without a Unity Editor."""

import argparse
import hashlib
import subprocess
from pathlib import Path
from xml.sax.saxutils import escape

ROOT = Path(__file__).resolve().parents[4]
SOURCE = "MCPForUnity/Editor/Tools/ManageScript.cs"
BATCH = "MCPForUnity/Editor/Tools/BatchExecute.cs"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    if not output.is_relative_to(ROOT / ".tmp"):
        parser.error("--output must be inside repository .tmp")
    source = (
        subprocess.check_output(["git", "show", f"{args.baseline}:{SOURCE}"], cwd=ROOT).decode()
        if args.baseline
        else (ROOT / SOURCE).read_text(encoding="utf-8")
    )
    print(f"SOURCE {SOURCE} SHA256 {hashlib.sha256(source.encode()).hexdigest()}", flush=True)
    helpers = source[
        source.index("        private sealed class RawScriptSnapshot") : source.index(
            "        private static ErrorResponse ValidateOrderedFinal"
        )
    ]
    method = source[
        source.index("        private static object ApplyPreparedScript(") : source.index(
            "        private static bool TryIndexFromLineCol("
        )
    ]
    batch = (
        subprocess.check_output(["git", "show", f"{args.baseline}:{BATCH}"], cwd=ROOT).decode()
        if args.baseline
        else (ROOT / BATCH).read_text(encoding="utf-8")
    )
    print(f"SOURCE {BATCH} SHA256 {hashlib.sha256(batch.encode()).hexdigest()}", flush=True)
    normalize = batch[
        batch.index("        internal static JObject NormalizeParameterKeys(") : batch.index(
            "        private static string ToCamelCase("
        )
    ]
    output.mkdir(parents=True, exist_ok=True)
    boundary = (Path(__file__).parent / "Program.cs").read_text(encoding="utf-8")
    (output / "Program.cs").write_text(
        boundary.replace("// PRODUCTION_COMMIT", helpers + method).replace(
            "// PRODUCTION_BATCH", normalize
        ),
        encoding="utf-8",
    )
    json_dll = escape((ROOT / ".compile-refs/Newtonsoft.Json.dll").resolve(strict=True).as_posix())
    response = escape((ROOT / "MCPForUnity/Editor/Helpers/Response.cs").as_posix())
    string_case = escape((ROOT / "MCPForUnity/Editor/Helpers/StringCaseUtility.cs").as_posix())
    (output / "Harness.csproj").write_text(
        f"""<Project Sdk="Microsoft.NET.Sdk">
<PropertyGroup><OutputType>Exe</OutputType><TargetFramework>net9.0</TargetFramework><ImplicitUsings>disable</ImplicitUsings></PropertyGroup>
<ItemGroup><Compile Include="{response}"/><Compile Include="{string_case}"/><Reference Include="Newtonsoft.Json"><HintPath>{json_dll}</HintPath></Reference></ItemGroup>
</Project>
""",
        encoding="utf-8",
    )
    return subprocess.call(
        ["dotnet", "run", "--project", str(output / "Harness.csproj"), "-c", "Release"],
        cwd=ROOT,
    )


if __name__ == "__main__":
    raise SystemExit(main())
