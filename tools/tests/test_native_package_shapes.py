"""Boundary contracts for native package selection, process evidence and artifact ownership."""

from pathlib import Path
import json
import sys
import xml.etree.ElementTree as ET

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import run_native_package_shapes as shapes


def test_profiles_cover_exact_native_shapes():
    assert {name for name, p in shapes.PROFILES.items() if p.suite == "package"} == {
        "atlas-v1",
        "atlas-v2",
        "addressables-legacy",
        "auditor-package",
        "auditor-absent",
    }
    assert {name for name, p in shapes.PROFILES.items() if p.suite == "integrated"} == {
        "auditor-integrated",
        "auditor-integrated-no-rules",
    }
    for version in ("V1", "V2"):
        profile = shapes.PROFILES[f"atlas-{version.lower()}"]
        assert profile.graphics and profile.phases == ((f"Author{version}",), (f"Reopen{version}",))
        assert "com.unity.2d.sprite" in profile.packages
    for name in ("auditor-absent", "auditor-integrated-no-rules"):
        profile = shapes.PROFILES[name]
        assert not profile.packages
        assert shapes.fixture_sources(profile)[
            "Assets/Scripts/ProjectAuditorAuditFixture.cs"
        ].is_file()
    used = [profile.packages for profile in shapes.PROFILES.values() if profile.packages]
    assert all(packages in used for packages in shapes.NATIVE_PACKAGES.values())
    assert all(packages in shapes.NATIVE_PACKAGES.values() for packages in used)


def test_runner_rejects_mixed_suites_existing_roots_and_wrong_editor(tmp_path):
    editor = tmp_path / "Editor/Unity"
    (editor.parent / "Data").mkdir(parents=True)
    editor.touch()
    output = tmp_path / "used"
    output.mkdir()
    with pytest.raises(FileExistsError):
        shapes.run("package", editor, shapes.VERSIONS["package"], output)
    with pytest.raises(shapes.PreparationError, match="outside suite"):
        shapes.run("package", editor, shapes.VERSIONS["package"], output, "auditor-integrated")
    with pytest.raises(shapes.PreparationError, match="requires Unity"):
        shapes.run("package", editor, shapes.VERSIONS["integrated"], output)
    with pytest.raises(shapes.PreparationError, match="Editor module"):
        shapes.run("integrated", editor, shapes.VERSIONS["integrated"], output)


def test_requires_expected_manifest_and_actual_package_lock(tmp_path):
    packages = tmp_path / "Packages"
    packages.mkdir()
    rules = "com.unity.project-auditor-rules"
    deps = {"com.unity.test-framework": "1.8.0", rules: "file:../../packages/rules"}
    manifest = packages / "manifest.json"
    manifest.write_text(json.dumps({"dependencies": deps}))
    locked = {
        name: {"version": version, "source": "local" if version.startswith("file:") else "builtin"}
        for name, version in deps.items()
    }
    lock = packages / "packages-lock.json"
    lock.write_text(json.dumps({"dependencies": locked}))
    shapes.verify_lock(tmp_path, deps)
    with pytest.raises(shapes.PreparationError, match="manifest"):
        shapes.verify_lock(tmp_path, {})
    lock.write_text(json.dumps({"dependencies": {**locked, "com.unity.project-auditor": {}}}))
    with pytest.raises(shapes.PreparationError, match="Unexpected resolved"):
        shapes.verify_lock(tmp_path, deps)
    locked[rules]["source"] = "registry"
    lock.write_text(json.dumps({"dependencies": locked}))
    with pytest.raises(shapes.PreparationError, match="locked source"):
        shapes.verify_lock(tmp_path, deps)


@pytest.mark.parametrize(
    "failure",
    [
        "",
        "exit",
        "timeout",
        "no-xml",
        "skip",
        "aggregate-skip",
        "root-skipped",
        "wrong-test",
        "extra",
        "wrong-editor",
    ],
)
def test_phase_checks_native_evidence_and_owns_fresh_directory(tmp_path, monkeypatch, failure):
    profile = shapes.PROFILES["atlas-v1"]
    version = shapes.VERSIONS["package"]
    directory = tmp_path / "phase"

    def execute(invocation):
        assert invocation.timeout == 900
        command = invocation.command
        assert "-nographics" not in command
        name = command[command.index("-testFilter") + 1]
        assert name == "MCPForUnityTests.Editor.Tools.SpriteAtlasReopenTests.AuthorV1"
        count = 2 if failure in ("extra", "aggregate-skip") else 1
        skipped = int(failure in ("skip", "aggregate-skip"))
        root = ET.Element(
            "test-run",
            total=str(count),
            passed=str(count - skipped),
            failed="0",
            inconclusive="0",
            skipped=str(skipped),
            result="Skipped:Ignored" if failure == "root-skipped" else "Passed",
        )
        ET.SubElement(
            root,
            "test-case",
            fullname="Other.Class.Method" if failure == "wrong-test" else name,
            result="Skipped" if failure == "skip" else "Passed",
        )
        if failure == "extra":
            ET.SubElement(root, "test-case", fullname="Other.Class.Method", result="Passed")
        if failure != "no-xml":
            ET.ElementTree(root).write(directory / "results.xml")
        build = shapes.VERSIONS["integrated"] if failure == "wrong-editor" else version
        (directory / "editor.log").write_text(
            f"Built from '6000/staging' branch; Version is '{build} (hash)'"
        )
        return {
            "actual_exit_code": int(failure == "exit"),
            "forced_termination": failure == "timeout",
        }

    monkeypatch.setattr(shapes, "execute", execute)

    def phase():
        shapes.run_phase(
            tmp_path / "Unity", version, tmp_path, directory, profile, profile.phases[0]
        )

    if failure:
        with pytest.raises(shapes.PreparationError):
            phase()
    else:
        phase()
    with pytest.raises(FileExistsError):
        phase()
