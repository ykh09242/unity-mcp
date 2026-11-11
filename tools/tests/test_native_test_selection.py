"""Required native selections and warm project evidence contracts."""

import json
from pathlib import Path
import re
from types import SimpleNamespace

import pytest

from tools import unity_editor_transport_qa as qa


def test_source_quality_acceptance_groups_include_a_separate_actual_reload():
    manifest_path = Path(__file__).resolve().parents[1] / "unity-native-groups.json"
    groups = json.loads(manifest_path.read_text(encoding="utf-8"))["groups"]
    assert {
        "source-freshness",
        "source-freshness-reload",
        "script-quality",
        "script-quality-impact",
        "verification-profiles",
    } <= groups.keys()
    reload = groups["source-freshness-reload"]
    assert reload["disruptive"] is True
    assert reload["methods"] == [
        "MCPForUnityTests.Editor.Services.SourceFreshnessReloadTests.RealDomainReloadPreservesPendingIdentityWithoutInventingCompilation"
    ]


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


def execution_pattern(selection):
    # .NET's absolute end anchor is \z; Python 3.11 calls that same anchor \Z.
    expression = selection["executionFilter"]
    assert expression.startswith(r"\A")
    assert expression.endswith(r"\z")
    assert ";" not in expression
    return re.compile(expression[:-2] + r"\Z")


def test_execution_filter_matches_only_literal_methods_and_parameterized_cases(tmp_path):
    path = manifest(tmp_path)
    data = json.loads(path.read_text())
    methods = ["Shared.Fixture.First", "Shared.Fixture.FirstLong", "Shared.Fixture.Second"]
    data["groups"]["ordinary"]["methods"] = methods
    path.write_text(json.dumps(data))
    selection = qa.load_selection(path, ["ordinary"], "win32")
    pattern = execution_pattern(selection)
    assert selection["required"] == methods
    assert selection["filter"] == ";".join(methods)
    assert selection["executionFilter"].count(r"Shared\.Fixture\.") == 1
    for method in methods:
        for suffix in ("", "()", '(1,"case")', '("nested(value)","line\nbreak")'):
            assert pattern.search(method + suffix)
    for candidate in (
        "Shared.Fixture",
        "Shared.Fixture.FirstLonger",
        "Shared.Fixture.First.More",
        "Shared.Fixture.First(1)extra",
        "Shared.Fixture.First(1",
        "Shared.Fixture.First\n",
        "Other.Shared.Fixture.First",
        "SharedxFixture.First",
        "Shared.FixturexFirst",
    ):
        assert not pattern.search(candidate)


def test_execution_filter_preserves_group_order_and_deduplicates_inventory(tmp_path):
    path = manifest(tmp_path)
    data = json.loads(path.read_text())
    data["groups"]["additional"] = {
        "methods": ["Fixture.Second", "Fixture.Third"],
        "disruptive": False,
        "platform_exclusions": {},
    }
    path.write_text(json.dumps(data))
    selection = qa.load_selection(path, ["additional", "ordinary"], "win32")
    assert selection["required"] == ["Fixture.Second", "Fixture.Third", "Fixture.First"]
    assert selection["filter"] == "Fixture.Second;Fixture.Third;Fixture.First"
    pattern = execution_pattern(selection)
    assert {
        name
        for name in ("Fixture.First", "Fixture.Second", "Fixture.Third", "Fixture.No")
        if pattern.search(name)
    } == set(selection["required"])


def test_execution_filter_escapes_dots_and_preserves_unicode_identifiers(tmp_path):
    path = manifest(tmp_path)
    data = json.loads(path.read_text())
    methods = ["Náme.Fixturé.Test_1", "Náme.Fixturé._Test"]
    data["groups"]["ordinary"]["methods"] = methods
    path.write_text(json.dumps(data))
    pattern = execution_pattern(qa.load_selection(path, ["ordinary"], "win32"))
    assert all(pattern.search(method) for method in methods)
    assert not pattern.search("NámexFixturé.Test_1")
    assert not pattern.search("Náme.FixturéxTest_1")


@pytest.mark.parametrize("method", ["Fixture.Test.*", "Fixture.Test|Other", "Fixture.Test(1)"])
def test_execution_filter_cannot_accept_raw_regex_methods(tmp_path, method):
    path = manifest(tmp_path)
    data = json.loads(path.read_text())
    data["groups"]["ordinary"]["methods"] = [method]
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="fully qualified"):
        qa.load_selection(path, ["ordinary"], "win32")


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
    all_methods = {method for group in data["groups"].values() for method in group["methods"]}
    for name in data["groups"]:
        selection = qa.load_selection(path, [name], "win32")
        pattern = execution_pattern(selection)
        assert selection["required"]
        assert {method for method in all_methods if pattern.search(method)} == set(
            selection["required"]
        )
        for method in selection["required"]:
            assert pattern.search(method + "(1)")
            assert not pattern.search(method + "Extra")
            assert not pattern.search(method.rpartition(".")[0])


def test_combined_repository_inventory_uses_one_compact_execution_expression():
    path = Path(__file__).parents[1] / "unity-native-groups.json"
    data = json.loads(path.read_text())
    groups = [name for name, group in data["groups"].items() if not group["disruptive"]]
    selection = qa.load_selection(path, groups, "win32")
    pattern = execution_pattern(selection)
    expected = list(
        dict.fromkeys(method for group in groups for method in data["groups"][group]["methods"])
    )
    assert selection["required"] == expected
    assert selection["filter"] == ";".join(expected)
    assert all(pattern.search(method) for method in expected)
    assert len(selection["executionFilter"]) < len(selection["filter"])


@pytest.mark.parametrize(
    "group,graphics",
    [("native-entity-id-66", False), ("native-panel-renderer-65", True)],
)
def test_version_native_cohorts_have_explicit_required_methods(group, graphics):
    path = Path(__file__).parents[1] / "unity-native-groups.json"
    selection = qa.load_selection(path, [group], "win32")
    assert len(selection["required"]) == 1
    assert selection["required"][0].startswith(
        "MCPForUnityTests.Editor.Services.NativeVersionAssuranceTests."
    )
    assert selection["requiresGraphics"] is graphics


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
