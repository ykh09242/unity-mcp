"""The compile queue keeps every validated target while starting newer Editors first."""

import json
from pathlib import Path
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "tools" / "compile_matrix.py"


def invoke(manifest):
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--manifest", str(manifest)],
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )


def test_compile_order_preserves_the_complete_current_matrix():
    manifest = ROOT / "tools" / "unity-versions.json"
    result = invoke(manifest)
    assert result.returncode == 0, result.stderr
    rows = json.loads(result.stdout)
    original = json.loads(manifest.read_text(encoding="utf-8"))["versions"]
    assert {tuple(sorted(row.items())) for row in rows} == {
        tuple(sorted({"version": row["id"], "channel": row["channel"]}.items())) for row in original
    }
    assert len(rows) == len(original)
    assert rows[0]["version"] == "7000.0.0a7"
    assert rows[-1]["version"] == "2021.3.45f2"


def test_compile_queue_compares_numeric_version_components(tmp_path):
    versions = ["6000.9.0f1", "6000.10.0f2", "6000.10.0f10", "2022.3.99f1"]
    manifest = {
        "defaultVersion": versions[0],
        "previewBaseImage": "unityci/base:ubuntu-3.2.2@sha256:" + "a" * 64,
        "versions": [
            {
                "id": version,
                "role": "fixture",
                "channel": "lts",
                "image": f"unityci/editor:ubuntu-{version}-base-3@sha256:" + "b" * 64,
            }
            for version in versions
        ],
    }
    path = tmp_path / "versions.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    result = invoke(path)
    assert result.returncode == 0, result.stderr
    assert [row["version"] for row in json.loads(result.stdout)] == [
        versions[2],
        versions[1],
        versions[0],
        versions[3],
    ]


@pytest.mark.parametrize("problem", ["duplicate", "unpinned", "missing_default"])
def test_invalid_compile_manifest_fails_without_partial_matrix(tmp_path, problem):
    data = json.loads((ROOT / "tools" / "unity-versions.json").read_text(encoding="utf-8"))
    if problem == "duplicate":
        data["versions"].append(data["versions"][0])
    elif problem == "unpinned":
        data["versions"][0]["image"] = "unityci/editor:latest"
    else:
        data["defaultVersion"] = "missing"
    path = tmp_path / "versions.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    result = invoke(path)
    assert result.returncode == 1
    assert result.stdout == ""
    assert "Unity compile matrix failed" in result.stderr
