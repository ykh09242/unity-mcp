"""Clean UPM staging must exercise the published manifest, without curated overrides."""

import hashlib
import json
from pathlib import Path

import pytest
import yaml

from tools import unity_upm_smoke as smoke


ROOT = Path(__file__).resolve().parents[2]


def test_clean_staging_keeps_manifest_bytes_and_lets_upm_resolve_dependencies(tmp_path):
    repo = tmp_path / "repo"
    package = repo / "MCPForUnity"
    package.mkdir(parents=True)
    original = b'{"name":"com.example.smoke","version":"1.0.0","dependencies":{"com.unity.test-framework":"1.4.6"}}\n'
    (package / "package.json").write_bytes(original)
    (package / "Fixture.cs").write_text("// package source", encoding="utf-8")
    output = repo / ".unity-ci/upm"
    project = smoke.prepare("2021.3.45f2", output, repo=repo)
    assert (output / "package/package.json").read_bytes() == original
    assert (package / "package.json").read_bytes() == original
    manifest = json.loads((project / "Packages/manifest.json").read_text())
    assert manifest == {"dependencies": {"com.example.smoke": "file:../../package"}}
    assert not (project / "Packages/packages-lock.json").exists()
    assert not (project / "Library").exists()
    assert not (output / "packages").exists()
    expected = json.loads((project / ".upm-smoke.json").read_text())
    assert expected["manifestSha256"] == hashlib.sha256(original).hexdigest()
    assert expected["dependencies"] == [{"name": "com.unity.test-framework", "version": "1.4.6"}]
    assert (project / "Assets/Tests/UntouchedPackageInstallTests.cs").is_file()


def test_smoke_matrix_uses_existing_floor_and_current_roles():
    selected = smoke.matrix(ROOT / "tools/unity-versions.json")
    config = json.loads((ROOT / "tools/unity-versions.json").read_text())
    assert [row["version"] for row in selected] == [
        row["id"] for row in config["versions"] if row["role"] in {"floor", "current"}
    ]
    assert len(selected) == 2


@pytest.mark.parametrize("roles", [["floor"], ["floor", "floor", "current"]])
def test_smoke_matrix_rejects_missing_or_ambiguous_support_roles(tmp_path, roles):
    config = json.loads((ROOT / "tools/unity-versions.json").read_text())
    config["versions"] = [dict(config["versions"][0], role=role) for role in roles]
    path = tmp_path / "versions.json"
    path.write_text(json.dumps(config))
    with pytest.raises(ValueError, match="one floor and one current"):
        smoke.matrix(path)


def test_smoke_workflow_requires_native_body_under_existing_license_gate():
    jobs = yaml.safe_load((ROOT / ".github/workflows/unity-tests.yml").read_text())["jobs"]
    job = jobs["untouchedUpmInstall"]
    assert job["needs"] == ["matrix", "license"]
    assert job["if"] == "needs.license.outputs.unity_ok == 'true'"
    assert (
        job["strategy"]["matrix"]["unity"] == "${{ fromJson(needs.matrix.outputs.upm_versions) }}"
    )
    runners = [step for step in job["steps"] if "unity-test-runner@" in step.get("uses", "")]
    assert len(runners) == 1
    assert runners[0]["with"]["testMode"] == "editmode"
    assert runners[0]["continue-on-error"] is True
    gate = next(
        step for step in job["steps"] if "--require-test UntouchedUpmSmoke." in step.get("run", "")
    )
    assert "--runner-outcome" in gate["run"]
    assert "check_unity_test_results.py" in gate["run"]
    assert not gate.get("continue-on-error")
    assert not any("actions/cache@" in step.get("uses", "") for step in job["steps"])
    commands = "\n".join(step.get("run", "") for step in job["steps"])
    assert "unity_upm_smoke.py prepare" in commands
    assert "unity_ci_packages.py prepare" not in commands
