"""Read-only, bounded diagnostic contracts; no Docker or cache repairs."""

import importlib.util
import json
from pathlib import Path
import sys
from unittest.mock import Mock

import pytest


TOOLS = Path(__file__).resolve().parents[1]
for name in ("unity_ci", "unity_compile_cache", "unity_compile_cache_diagnostics"):
    spec = importlib.util.spec_from_file_location(name, TOOLS / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
diagnostics = sys.modules["unity_compile_cache_diagnostics"]
cache = diagnostics.cache
VERSION = "2022.3.76f1"
IMAGE = f"unityci/editor:ubuntu-{VERSION}-base-3@sha256:" + "a" * 64
BASE = "unityci/base:ubuntu-3.2.2@sha256:" + "b" * 64


@pytest.fixture
def environment(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    directory = repo / ".unity-ci-sdk" / VERSION
    data = directory / "Data/Managed"
    data.mkdir(parents=True)
    for index in range(25):
        (data / f"Reference{index:02d}.dll").write_bytes(f"PUBLIC-FILE-CONTENT-{index}".encode())
    manifest_path = repo / "versions.json"
    manifest_path.write_text(json.dumps({"defaultVersion": VERSION, "previewBaseImage": BASE,
        "versions": [{"id": VERSION, "channel": "lts", "role": "lts", "image": IMAGE}]}), encoding="utf-8")
    manifest = cache.unity_ci.load_manifest(manifest_path)
    monkeypatch.setattr(cache, "ROOT", repo)
    monkeypatch.setattr(cache.subprocess, "run", Mock(side_effect=AssertionError("No Docker in diagnostics")))
    monkeypatch.setattr(cache.unity_ci, "prepare", Mock(side_effect=AssertionError("No image preparation")))
    details = cache.identity(manifest, VERSION)
    receipt = {"cache_key": details["cache_key"], "provenance": details["provenance"],
               "directories": ["Managed"], "files": cache._records(directory / "Data")}
    (directory / cache.RECEIPT).write_text(json.dumps(receipt), encoding="utf-8")
    return repo, directory, manifest, manifest_path, receipt


def test_matching_cache_is_read_only_and_has_no_mismatches(environment):
    _, directory, manifest, _, _ = environment
    before = {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in directory.rglob("*") if path.is_file()}
    result = diagnostics.diagnose(manifest, VERSION, directory)
    assert result["total_mismatches"] == 0 and result["mismatches"] == []
    assert result["truncated"] is False
    assert {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in before} == before
    cache.subprocess.run.assert_not_called()
    cache.unity_ci.prepare.assert_not_called()


def test_completed_cli_diagnostic_reports_size_hash_and_mode_without_contents(environment, monkeypatch, capsys):
    repo, directory, _, manifest_path, receipt = environment
    receipt["files"]["Managed/Reference00.dll"]["mode"] ^= 0o022
    (directory / cache.RECEIPT).write_text(json.dumps(receipt), encoding="utf-8")
    (directory / "Data/Managed/Reference01.dll").write_bytes(b"CHANGED-PUBLIC-CONTENT")
    output = repo / "github-output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    assert diagnostics.main([VERSION, "--cache", f".unity-ci-sdk/{VERSION}", "--manifest", str(manifest_path)]) == 0
    text = capsys.readouterr().out
    result = json.loads(text)
    assert result["total_mismatches"] == 2
    assert [record["path"] for record in result["mismatches"]] == ["Managed/Reference00.dll", "Managed/Reference01.dll"]
    assert set(result["mismatches"][0]["actual"]) == {"size", "mode", "sha256"}
    assert result["mismatches"][0]["expected"]["mode"] != result["mismatches"][0]["actual"]["mode"]
    assert "PUBLIC-FILE-CONTENT" not in text and "CHANGED-PUBLIC-CONTENT" not in text
    assert not output.exists()


def test_details_are_bounded_but_total_is_complete(environment):
    _, directory, manifest, _, _ = environment
    for path in (directory / "Data/Managed").iterdir():
        path.write_bytes(b"changed")
    result = diagnostics.diagnose(manifest, VERSION, directory)
    assert result["total_mismatches"] == 25 and len(result["mismatches"]) == 20
    assert result["truncated"] is True


def test_missing_and_extra_public_files_are_reported_without_repair(environment):
    _, directory, manifest, _, _ = environment
    missing = directory / "Data/Managed/Reference00.dll"
    missing.unlink()
    extra = directory / "Data/Managed/Extra.dll"
    extra.write_bytes(b"extra public data")
    result = diagnostics.diagnose(manifest, VERSION, directory)
    assert result["total_mismatches"] == 2
    assert result["mismatches"][0]["expected"] is None
    assert result["mismatches"][1]["actual"] is None
    assert not missing.exists() and extra.read_bytes() == b"extra public data"


@pytest.mark.parametrize("version,path", [("../../outside", "../../outside"), ("6000.3.25f1", ".unity-ci-sdk/6000.3.25f1"),
                                         (VERSION, "outside"), (VERSION, f".unity-ci-sdk/../.unity-ci-sdk/{VERSION}")])
def test_unknown_version_or_non_exact_path_is_rejected_before_records(environment, monkeypatch, version, path):
    monkeypatch.setattr(cache, "_records", Mock(side_effect=AssertionError("Do not inspect unsafe path")))
    with pytest.raises(ValueError):
        diagnostics.diagnose(environment[2], version, Path(path))
    cache._records.assert_not_called()


@pytest.mark.parametrize("mutation", ["source", "traversal", "scope", "invalid_record"])
def test_untrusted_receipt_fails_before_record_reads(environment, monkeypatch, capsys, mutation):
    _, directory, _, manifest_path, receipt = environment
    if mutation == "source":
        receipt["cache_key"] = "other source"
    elif mutation == "traversal":
        receipt["files"]["../outside"] = receipt["files"]["Managed/Reference00.dll"]
    elif mutation == "scope":
        receipt["directories"] = ["Resources/Private"]
    else:
        receipt["files"]["Managed/Reference00.dll"]["sha256"] = "not a hash"
    (directory / cache.RECEIPT).write_text(json.dumps(receipt), encoding="utf-8")
    monkeypatch.setattr(cache, "_records", Mock(side_effect=AssertionError("Do not read untrusted scope")))
    assert diagnostics.main([VERSION, "--cache", str(directory), "--manifest", str(manifest_path)]) == 1
    output = capsys.readouterr()
    assert output.out == "" and "diagnostic failed" in output.err
    cache._records.assert_not_called()


def test_extra_private_tree_is_not_hashed(environment, monkeypatch):
    _, directory, manifest, _, _ = environment
    forbidden = directory / "Data/Resources/Private/Secret.cs"
    forbidden.parent.mkdir(parents=True)
    forbidden.write_bytes(b"not inspected")
    monkeypatch.setattr(cache, "_records", Mock(side_effect=AssertionError("Do not hash private data")))
    with pytest.raises(ValueError, match="outside the public cache scope"):
        diagnostics.diagnose(manifest, VERSION, directory)
    cache._records.assert_not_called()


def test_linked_data_is_not_followed(environment, monkeypatch, tmp_path):
    _, directory, manifest, _, _ = environment
    outside = tmp_path / "outside"
    outside.write_bytes(b"not inspected")
    link = directory / "Data/Managed/External.dll"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("Host does not permit unprivileged symbolic links")
    monkeypatch.setattr(cache, "_records", Mock(side_effect=AssertionError("Do not follow linked input")))
    with pytest.raises(ValueError, match="Linked compiler input"):
        diagnostics.diagnose(manifest, VERSION, directory)
    cache._records.assert_not_called()


def test_exact_ui_file_allowlist_does_not_allow_descendant_content(environment, monkeypatch):
    _, directory, manifest, _, receipt = environment
    ui_path = cache.LIBCACHE + "/template/ScriptAssemblies/UnityEngine.UI.dll"
    receipt["directories"].append(ui_path)
    (directory / cache.RECEIPT).write_text(json.dumps(receipt), encoding="utf-8")
    forbidden = directory / "Data" / ui_path / "Unexpected.cs"
    forbidden.parent.mkdir(parents=True)
    forbidden.write_bytes(b"not inspected")
    monkeypatch.setattr(cache, "_records", Mock(side_effect=AssertionError("Do not widen exact file scope")))
    with pytest.raises(ValueError, match="outside the public cache scope"):
        diagnostics.diagnose(manifest, VERSION, directory)
    cache._records.assert_not_called()
