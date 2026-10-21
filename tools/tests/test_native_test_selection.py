"""Required native selections and warm project evidence contracts."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from tools import unity_editor_transport_qa as qa


def manifest(tmp_path):
    path = tmp_path / "groups.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "groups": {
                    "ordinary": {
                        "methods": ["Fixture.First", "Fixture.Second"],
                        "disruptive": False,
                        "platform_exclusions": {},
                    },
                    "reload": {
                        "methods": ["Reload.ActualReload"],
                        "disruptive": True,
                        "platform_exclusions": {
                            "linux": "No licensed native evidence in this cohort"
                        },
                    },
                },
            }
        ),
        encoding="utf-8",
    )
    return path


def test_group_selection_has_complete_inventory_and_exact_method_filter(tmp_path):
    selection = qa.load_selection(manifest(tmp_path), ["ordinary"], "win32")
    assert selection["required"] == ["Fixture.First", "Fixture.Second"]
    assert selection["filter"] == "Fixture.First;Fixture.Second"
    assert len(selection["manifestSha256"]) == 64


def test_disruptive_selection_cannot_share_an_editor_launch(tmp_path):
    with pytest.raises(ValueError, match="disruptive"):
        qa.load_selection(manifest(tmp_path), ["ordinary", "reload"], "win32")


def test_platform_exclusion_is_explicit_and_cannot_be_reported_as_pass(tmp_path):
    with pytest.raises(ValueError, match="No licensed native evidence"):
        qa.load_selection(manifest(tmp_path), ["reload"], "linux")


def test_empty_unknown_or_duplicate_selection_is_rejected(tmp_path):
    path = manifest(tmp_path)
    for groups in ([], ["absent"], ["ordinary", "ordinary"]):
        with pytest.raises(ValueError):
            qa.load_selection(path, groups, "win32")


def owned_inputs(tmp_path):
    output = tmp_path / "reports/owned"
    output.mkdir(parents=True)
    snapshot = output / "snapshot"
    source = snapshot / "MCPForUnity/Editor/tool.cs"
    source.parent.mkdir(parents=True)
    source.write_text("class Tool {}")
    project = snapshot / "isolated/project"
    project.mkdir(parents=True)
    (project / "Library").mkdir()
    (project / "Library/cache").write_bytes(b"keep-cache")
    installed = snapshot / "isolated/package/Editor/tool.cs"
    installed.parent.mkdir(parents=True)
    installed.write_bytes(source.read_bytes())
    editor = tmp_path / "Unity.exe"
    editor.write_bytes(b"owned-editor")
    (output / "source.json").write_text(json.dumps({"ref": "a" * 40}))
    prep = {"projectAbsolute": str(project), "unityVersion": "6000.0.69f1"}
    return output, prep, editor, source, installed


def test_identity_detects_source_prepared_project_or_editor_drift(tmp_path):
    output, prep, editor, source, installed = owned_inputs(tmp_path)
    receipt = qa.capture_inputs(output, prep, editor)
    qa.verify_inputs(output, prep, editor, receipt)
    for path in (source, installed, editor):
        original = path.read_bytes()
        path.write_bytes(original + b"changed")
        with pytest.raises(ValueError, match="changed"):
            qa.verify_inputs(output, prep, editor, receipt)
        path.write_bytes(original)
    (Path(prep["projectAbsolute"]) / "Library/cache").write_bytes(b"warmed")
    qa.verify_inputs(output, prep, editor, receipt)


def test_new_source_input_is_not_hidden_by_previous_receipt(tmp_path):
    output, prep, editor, _, installed = owned_inputs(tmp_path)
    receipt = qa.capture_inputs(output, prep, editor)
    installed.with_name("new.cs").write_text("class Added {}")
    with pytest.raises(ValueError, match="changed"):
        qa.verify_inputs(output, prep, editor, receipt)


def test_refresh_replaces_only_changed_owned_files_and_retains_library(tmp_path):
    source = tmp_path / "source"
    target = tmp_path / "project"
    incoming = tmp_path / "incoming"
    for root in (source, target, incoming):
        root.mkdir()
    for root in (source, target):
        (root / "old.cs").write_text("old")
        (root / "kept.cs").write_text("unchanged")
    (incoming / "kept.cs").write_text("unchanged")
    (incoming / "new.cs").write_text("new")
    cache = target / "Library"
    cache.mkdir()
    (cache / "cache").write_bytes(b"keep-cache")
    before = (target / "kept.cs").stat().st_mtime_ns
    qa.refresh_tree(source, incoming, target)
    assert not (target / "old.cs").exists()
    assert (target / "new.cs").read_text() == "new"
    assert (target / "kept.cs").stat().st_mtime_ns == before
    assert (cache / "cache").read_bytes() == b"keep-cache"


def test_refresh_preflights_conflicts_before_first_write(tmp_path):
    source, incoming, target = [tmp_path / name for name in ("old", "incoming", "target")]
    for root in (source, incoming, target):
        root.mkdir()
        (root / "a.cs").write_text("old")
    (incoming / "a.cs").write_text("new")
    (incoming / "z.cs").write_text("new")
    (target / "z.cs").write_text("unowned")
    with pytest.raises(ValueError, match="conflict"):
        qa.refresh_tree(source, incoming, target)
    assert (target / "a.cs").read_text() == "old"


def refresh_fixture(tmp_path, monkeypatch):
    import shutil
    from tools.native_test_inputs import tree_files

    monkeypatch.setattr(qa, "ROOT", tmp_path)
    output, prep, editor, source, installed = owned_inputs(tmp_path)
    old = output / "snapshot"
    source_json = {"name": "com.example.qa", "dependencies": {"com.example.dependency": "1.0.0"}}
    (old / "MCPForUnity/package.json").write_text(json.dumps(source_json))
    (old / "isolated/package/package.json").write_text(json.dumps(source_json))
    for root in (old / "TestProjects/UnityMCPTests", Path(prep["projectAbsolute"])):
        for rel in ("Assets/Tests", "Assets/Scripts", "ProjectSettings"):
            (root / rel).mkdir(parents=True, exist_ok=True)
        (root / "Assets/Tests/test.cs").write_text("class Test {}")
        (root / "ProjectSettings/ProjectVersion.txt").write_text("version")
    (old / "tools").mkdir()
    for name in ("unity_ci_packages.py", "unity-ci-packages.json"):
        (old / "tools" / name).write_text("resolver")
    incoming = tmp_path / "candidate"
    incoming.mkdir()
    for name in ("MCPForUnity", "TestProjects", "tools"):
        shutil.copytree(old / name, incoming / name)
    (incoming / "MCPForUnity/Editor/tool.cs").write_text("class Changed {}")
    prep["inputs"] = qa.capture_inputs(output, prep, editor)
    (output / "preparation.json").write_text(json.dumps(prep))

    def snapshot(_args, stage):
        shutil.copytree(incoming, stage / "snapshot")
        (stage / "source.json").write_text(json.dumps({"ref": "b" * 40}))

    monkeypatch.setattr(qa, "snapshot_source", snapshot)
    args = SimpleNamespace(output=str(output), editor=editor, version=prep["unityVersion"])
    return args, incoming, output, prep, installed, tree_files


def test_refresh_command_is_idempotent_and_preserves_cache(tmp_path, monkeypatch):
    args, _, output, prep, installed, _ = refresh_fixture(tmp_path, monkeypatch)
    qa.refresh(args)
    assert installed.read_text() == "class Changed {}"
    stamp = installed.stat().st_mtime_ns
    qa.refresh(args)
    assert installed.stat().st_mtime_ns == stamp
    assert (Path(prep["projectAbsolute"]) / "Library/cache").read_bytes() == b"keep-cache"
    refreshed = json.loads((output / "preparation.json").read_text())
    qa.verify_inputs(output, refreshed, args.editor, refreshed["inputs"])
    assert refreshed["inputs"]["source"]["ref"] == "b" * 40


def test_package_change_is_refused_without_writes(tmp_path, monkeypatch):
    args, incoming, _, _, installed, _ = refresh_fixture(tmp_path, monkeypatch)
    p = incoming / "MCPForUnity/package.json"
    package = json.loads(p.read_text())
    package["dependencies"]["com.example.dependency"] = "2.0.0"
    p.write_text(json.dumps(package))
    with pytest.raises(ValueError, match="dependency"):
        qa.refresh(args)
    assert installed.read_text() == "class Tool {}"


def test_interrupted_refresh_is_marked_unlaunchable(tmp_path, monkeypatch):
    args, _, output, _, _, _ = refresh_fixture(tmp_path, monkeypatch)

    def fail(_plan):
        raise OSError("publication interrupted")

    monkeypatch.setattr(qa, "apply_refresh", fail)
    with pytest.raises(OSError, match="interrupted"):
        qa.refresh(args)
    assert json.loads((output / "preparation.json").read_text())["refreshPending"] is True
    with pytest.raises(ValueError, match="incomplete"):
        qa.refresh(args)


def test_repository_manifest_method_inventory_is_valid():
    path = Path(__file__).parents[1] / "unity-native-groups.json"
    data = json.loads(path.read_text())
    for name in data["groups"]:
        assert qa.load_selection(path, [name], "win32")["required"]


def test_generated_editor_user_settings_do_not_change_source_identity(tmp_path):
    output, prep, editor, _, _ = owned_inputs(tmp_path)
    receipt = qa.capture_inputs(output, prep, editor)
    settings = Path(prep["projectAbsolute"]) / "UserSettings"
    settings.mkdir()
    (settings / "EditorUserSettings.asset").write_text("derived local Editor state")
    qa.verify_inputs(output, prep, editor, receipt)


def test_graphics_requirement_combines_selected_groups_and_rejects_invalid_flag(tmp_path):
    path = manifest(tmp_path)
    data = json.loads(path.read_text())
    data["groups"]["panels"] = {
        "methods": ["Panels.Attached"],
        "disruptive": False,
        "platform_exclusions": {},
        "requires_graphics": True,
    }
    path.write_text(json.dumps(data))
    assert qa.load_selection(path, ["ordinary", "panels"], "win32")["requiresGraphics"]
    assert not qa.load_selection(path, ["ordinary"], "win32")["requiresGraphics"]
    data["groups"]["panels"]["requires_graphics"] = "false"
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="boolean"):
        qa.load_selection(path, ["panels"], "win32")
