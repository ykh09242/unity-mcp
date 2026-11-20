"""Exercise SDK ApiCompat and independent source/binary clients without Unity or NuGet."""

import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


def run(*args: str | Path, success: bool = True, exit_code: int | None = None) -> str:
    result = subprocess.run(args, capture_output=True, text=True, errors="replace", timeout=60)
    output = result.stdout + result.stderr
    if (exit_code is not None and result.returncode != exit_code) or (
        exit_code is None and (result.returncode == 0) != success
    ):
        raise RuntimeError(f"Unexpected exit {result.returncode} from {args}:\n{output}")
    return output


def installation(listing: str, name: str, version: str) -> Path:
    for line in listing.splitlines():
        if line.startswith(f"{name}{version} ["):
            return Path(line.split("[", 1)[1].removesuffix("]")) / version
    raise ValueError(f"Installed SDK/runtime not found: {name}{version}")


def verify(checker: Path, scratch: Path) -> None:
    version = run("dotnet", "--version").strip()
    sdk = installation(run("dotnet", "--list-sdks"), "", version)
    runtimes = run("dotnet", "--list-runtimes")
    runtime_version = next(
        line.split()[1]
        for line in runtimes.splitlines()
        if line.startswith("Microsoft.NETCore.App 11.")
    )
    runtime = installation(runtimes, "Microsoft.NETCore.App ", runtime_version)
    core = runtime / "System.Private.CoreLib.dll"

    def compile_source(path: Path, source: str, reference: Path | None = None, ok: bool = True):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.with_suffix(".cs").write_text(source, encoding="utf-8")
        refs = [core, reference] if reference else [core]
        response = [f'-r:"{item}"' for item in refs]
        response.extend(
            [
                "-target:exe" if reference else "-target:library",
                f'-out:"{path}"',
                f'"{path.with_suffix(".cs")}"',
            ]
        )
        rsp = path.with_suffix(".rsp")
        rsp.write_text("\n".join(response) + "\n", encoding="utf-8")
        return run("dotnet", sdk / "Roslyn/bincore/csc.dll", f"@{rsp}", success=ok)

    surface = scratch / "surface.txt"
    surface.write_text(
        "Library Extension Ping .ctor Value\nLibrary IProbe\nLibrary Catalog\n", encoding="utf-8"
    )
    original = (
        "public class Extension { public Extension() {} public int Value { get; set; } public int Ping(int count) => count; public void Unrelated() {} }"
        "public interface IProbe { void Read(); } public class Catalog {}"
    )
    client = (
        "class Client : IProbe { public void Read() {} } "
        "class Entry { static int Main() { try { return Exercise(); } "
        "catch (System.MemberAccessException) { return 1; } "
        "catch (System.TypeLoadException) { return 2; } "
        "catch (System.Exception) { return 3; } } "
        "[System.Runtime.CompilerServices.MethodImpl(System.Runtime.CompilerServices.MethodImplOptions.NoInlining)] "
        "static int Exercise() { new Client().Read(); var extension = new Extension { Value = 0 }; return extension.Ping(count: extension.Value); } }"
    )
    baseline = scratch / "baseline/Library.dll"
    compile_source(baseline, original)
    old_client = baseline.with_name("Client.dll")
    compile_source(old_client, client, baseline)
    runtime_config = checker.with_suffix(".runtimeconfig.json")
    cases = (
        ("compatible", "public void Unrelated()", "public void Added()", None, True, 0),
        ("widened", "int Ping(int count)", "int Ping(long count)", "Ping", True, 1),
        (
            "constructor",
            "public Extension()",
            "public Extension(int optional = 0)",
            "Extension",
            True,
            1,
        ),
        ("hidden", "public int Ping", "internal int Ping", "Ping", False, 1),
        ("removed", "public int Ping", "public int Echo", "Ping", False, 1),
        ("accessor", "get; set;", "get; private set;", "Value", False, 1),
        ("interface", "void Read();", "void Read(); void Added();", "Added", False, 2),
        ("renamed", "int count) => count", "int value) => value", "count", False, 0),
    )
    for name, before, after, diagnostic, source_ok, binary_exit in cases:
        candidate = scratch / name / "Library.dll"
        changed = original.replace(before, after)
        if name == "compatible":
            changed = changed.replace(
                "class Catalog {}", "class Catalog { public bool Added => true; }"
            )
        elif name == "widened":
            changed = changed.replace("=> count;", "=> (int)count;")
        compile_source(candidate, changed)
        output = run(
            "dotnet", checker, surface, baseline, candidate, exit_code=int(diagnostic is not None)
        )
        if diagnostic is not None and diagnostic not in output:
            raise AssertionError(f"{name}: missing {diagnostic} in {output}")
        compile_source(candidate.with_name("Rebuilt.dll"), client, candidate, ok=source_ok)
        existing = candidate.with_name("Client.dll")
        shutil.copyfile(old_client, existing)
        run("dotnet", "exec", "--runtimeconfig", runtime_config, existing, exit_code=binary_exit)
        print(
            f"{name}: comparison, source compilation and old binary invocation matched expectations"
        )
    original_definition = baseline.with_suffix(".asmdef")
    changed_definition = scratch / "compatible/Library.asmdef"
    old_guid = "0123456789abcdef0123456789abcdef"
    for definition in (original_definition, changed_definition):
        definition.with_suffix(".asmdef.meta").write_text(f"guid: {old_guid}\n", encoding="utf-8")
    original_definition.write_text('{"name":"Library","autoReferenced":true}', encoding="utf-8")
    for fields, diagnostic in (
        ({}, None),
        ({"autoReferenced": False}, "autoReferenced"),
        ({"defineConstraints": ["REQUIRED_SDK"]}, "defineConstraints"),
        ({"includePlatforms": ["Editor"]}, "includePlatforms"),
        ({"excludePlatforms": ["Editor"]}, "excludePlatforms"),
    ):
        changed_definition.write_text(json.dumps({"name": "Library", **fields}), encoding="utf-8")
        output = run(
            "dotnet",
            checker,
            surface,
            baseline,
            changed_definition.with_suffix(".dll"),
            original_definition,
            changed_definition,
            exit_code=2 if diagnostic else 0,
        )
        if diagnostic and diagnostic not in output:
            raise AssertionError(f"Missing {diagnostic} in {output}")
    print("Predefined-assembly references and platform/define constraints matched expectations")
    changed_definition.write_text('{"name":"Library"}', encoding="utf-8")
    changed_definition.with_suffix(".asmdef.meta").write_text(
        f"guid: {old_guid[::-1]}\n", encoding="utf-8"
    )
    output = run(
        "dotnet",
        checker,
        surface,
        baseline,
        changed_definition.with_suffix(".dll"),
        original_definition,
        changed_definition,
        exit_code=2,
    )
    if "asmdef GUID" not in output:
        raise AssertionError(output)
    print("Changing the assembly-definition GUID broke reference compatibility as expected")


if __name__ == "__main__":
    with tempfile.TemporaryDirectory(prefix="public-api-") as temporary:
        verify(Path(sys.argv[1]).resolve(), Path(temporary))
