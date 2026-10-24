"""Optional engine modules must be absent from both metadata and compiler symbols."""

import itertools
import json
import os
import shutil
import subprocess

import pytest

from .compile_check_helpers import CompileHarness, ROOT, harness

MODULES = {
    "physics": ("PhysicsModule", "MCP_FOR_UNITY_PHYSICS"),
    "physics2d": ("Physics2DModule", "MCP_FOR_UNITY_PHYSICS2D"),
    "screencapture": ("ScreenCaptureModule", "MCP_FOR_UNITY_SCREEN_CAPTURE"),
}


@pytest.mark.parametrize("version", ["2021.3.45f2", "6000.0.84f1", "7000.0.0a1"])
@pytest.mark.parametrize(
    "disabled", [list(values) for n in range(4) for values in itertools.combinations(MODULES, n)]
)
def test_module_matrix_removes_metadata_and_symbols(
    harness: CompileHarness, monkeypatch: pytest.MonkeyPatch, disabled: list[str], version: str
) -> None:
    monkeypatch.setenv("DISABLED_UNITY_MODULES", " ".join(disabled))
    entries = ["DATA/Managed/Core.dll"]
    for module, (assembly, _) in MODULES.items():
        relative = f"Managed/UnityEngine/UnityEngine.{assembly}.dll"
        entries.append("DATA/" + relative)
        path = harness.data / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        if module not in disabled:
            path.touch()
    for profile in ("", "2021.3", "7000.0"):
        for name in ("Runtime", "Editor"):
            (harness.repo / "tools/compile-refs" / profile / f"{name}.txt").write_text(
                "\n".join(entries) + "\n", encoding="utf-8"
            )
    result = harness.run(version)
    assert result.returncode == 0, result.stdout + result.stderr
    for name in ("Runtime", "Editor"):
        response = (harness.output / "linux" / f"MCPForUnity.{name}.rsp").read_text(
            encoding="utf-8"
        )
        for module, (assembly, symbol) in MODULES.items():
            assert (f"UnityEngine.{assembly}.dll" in response) == (module not in disabled)
            assert (f"-define:{symbol}\n" in response) == (module not in disabled)

    if version.startswith("7000."):
        response = (harness.output / "linux" / "UnityEngine.UI.rsp").read_text(encoding="utf-8")
        assert ("-define:PACKAGE_PHYSICS\n" in response) == ("physics" not in disabled)
        assert ("-define:PACKAGE_PHYSICS2D\n" in response) == ("physics2d" not in disabled)


def test_module_matrix_rejects_unknown_module_before_compiling(
    harness: CompileHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DISABLED_UNITY_MODULES", "physic")
    result = harness.run("6000.0.84f1")
    assert result.returncode == 2
    assert "unknown optional Unity module" in result.stderr
    assert not harness.calls.exists()


def test_optional_module_defines_match_runtime_and_editor_package_versions() -> None:
    for name in ("Runtime", "Editor"):
        definition = json.loads(
            (ROOT / f"MCPForUnity/{name}/MCPForUnity.{name}.asmdef").read_text(encoding="utf-8")
        )
        defines = {entry["name"]: entry for entry in definition["versionDefines"]}
        for module, (_, symbol) in MODULES.items():
            assert defines[f"com.unity.modules.{module}"] == {
                "name": f"com.unity.modules.{module}",
                "expression": "1.0.0",
                "define": symbol,
            }


@pytest.mark.parametrize("mode,fail_on", [("all", ""), ("all", "physics physics2d"), ("key", "")])
def test_optional_matrix_runs_each_absent_combination_and_preserves_failures(
    harness: CompileHarness, mode: str, fail_on: str
) -> None:
    script = harness.repo / "tools/compile-optional-modules.sh"
    shutil.copy2(ROOT / "tools/compile-optional-modules.sh", script)
    (harness.repo / "tools/compile-check.sh").write_text(
        "#!/usr/bin/env bash\n"
        'printf "%s|%s|%s\\n" "$DISABLED_UNITY_MODULES" "$TEST_PROJECT" "$OUT" >> "$MATRIX_CALLS"\n'
        'if [ "$DISABLED_UNITY_MODULES" = "$FAIL_ON" ]; then exit 9; fi\n',
        encoding="utf-8",
    )
    calls = harness.output / "matrix-calls.txt"
    result = subprocess.run(
        [harness.bash, script.as_posix()],
        env={
            **os.environ,
            "OUT": (harness.output / "paths with spaces").as_posix(),
            "TEST_PROJECT": "deliberate-physics-fixtures",
            "MATRIX_CALLS": calls.as_posix(),
            "FAIL_ON": fail_on,
            "OPTIONAL_MODULE_MATRIX": mode,
        },
        capture_output=True,
        text=True,
        check=False,
    )
    observed = [line.split("|") for line in calls.read_text(encoding="utf-8").splitlines()]
    assert result.returncode == (9 if fail_on else 0), result.stdout + result.stderr
    assert all(not row[1] for row in observed)
    assert all("paths with spaces/" in row[2] for row in observed)
    if fail_on:
        assert observed[-1][0] == fail_on
        assert len(observed) == 3
    else:
        expected = {
            frozenset(values)
            for count in range(1, 4)
            for values in itertools.combinations(MODULES, count)
        }
        if mode == "key":
            expected = {frozenset((name,)) for name in MODULES} | {frozenset(MODULES)}
        assert {frozenset(row[0].split()) for row in observed} == expected
        assert len(observed) == (4 if mode == "key" else 7)


def test_proven_optional_modules_are_not_forced_by_package_dependencies() -> None:
    dependencies = json.loads((ROOT / "MCPForUnity/package.json").read_text(encoding="utf-8"))[
        "dependencies"
    ]
    assert not {f"com.unity.modules.{module}" for module in MODULES} & dependencies.keys()
    assert {
        "com.unity.modules.animation",
        "com.unity.modules.uielements",
        "com.unity.modules.imageconversion",
        "com.unity.modules.unitywebrequest",
        "com.unity.editorcoroutines",
        "com.unity.test-framework",
        "com.unity.ext.nunit",
        "com.unity.nuget.newtonsoft-json",
    } <= dependencies.keys()
