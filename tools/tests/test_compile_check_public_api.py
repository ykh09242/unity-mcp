"""Exercise baseline, candidate, and external-client routing through the compile harness."""

import shutil

import pytest

from .compile_check_helpers import CompileHarness, ROOT, harness


def test_missing_public_api_input_fails_before_compiling(
    harness: CompileHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PUBLIC_API_INPUTS", harness.repo.as_posix())
    result = harness.run("2021.3.45f2")
    assert result.returncode == 2
    assert "missing prepared public API input: revision.txt" in result.stderr


def test_public_api_uses_matching_libraries_and_preserves_unity_defines(
    harness: CompileHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    policy = harness.repo / "tools/public-api"
    shutil.copytree(ROOT / "tools/public-api", policy)
    prepared = harness.repo / "prepared"
    shutil.copytree(harness.repo / "MCPForUnity", prepared / "MCPForUnity")
    shutil.copyfile(policy / "baseline.txt", prepared / "revision.txt")
    (prepared / "checker").mkdir()
    (prepared / "checker/PublicApi.dll").write_bytes(b"fixture")
    for name in ("Editor", "Runtime"):
        (prepared / f"MCPForUnity/{name}/MCPForUnity.{name}.asmdef").write_text(
            "{}", encoding="utf-8"
        )
    calls = prepared / "checks"
    host = prepared / "checker-host"
    host.write_text(
        '#!/usr/bin/env bash\nprintf "%s\\n" "$*" >> "$CHECK_CALLS"\n', encoding="utf-8"
    )
    host.chmod(0o755)
    monkeypatch.setenv("PUBLIC_API_INPUTS", prepared.as_posix())
    monkeypatch.setenv("PUBLIC_API_DOTNET", host.as_posix())
    monkeypatch.setenv("CHECK_CALLS", calls.as_posix())
    result = harness.run("2021.3.45f2", platforms="linux win")
    assert result.returncode == 0, result.stdout + result.stderr
    comparisons = calls.read_text(encoding="utf-8").splitlines()
    assert len(comparisons) == 8
    assert sum("--run-consumer" in call for call in comparisons) == 4
    assert all("/linux/" in call and "/win/" not in call for call in comparisons)
    linux = harness.output / "linux"
    for profile in ("", "player", "player-scenarios"):
        candidate = linux / profile
        baseline = candidate / "public-api/baseline"
        for name in ("Runtime", "Editor") if not profile else ("Runtime",):
            candidates = (candidate / f"MCPForUnity.{name}.rsp").read_text().splitlines()
            originals = (baseline / f"MCPForUnity.{name}.rsp").read_text().splitlines()
            assert [arg for arg in candidates if arg.startswith("-define:")] == [
                arg for arg in originals if arg.startswith("-define:")
            ]
            if name == "Editor":
                assert f'-r:"{baseline.as_posix()}/MCPForUnity.Runtime.dll"' in originals
            for side in (candidate, baseline):
                rsp = side / f"public-api-consumer/IndependentExtension.{name}.rsp"
                client = rsp.read_text().splitlines()
                assert f'-r:"{side.as_posix()}/MCPForUnity.{name}.dll"' in client
                assert all("/MCPForUnity/" not in arg for arg in client if arg.endswith('.cs"'))
