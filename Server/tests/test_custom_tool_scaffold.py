"""Fresh-file publication protects source, metadata and foreign replacements."""

from tests.timing import watchdog_timeout

import importlib
import importlib.util
import os
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from threading import Event

import pytest


@pytest.fixture
def project(tmp_path):
    (tmp_path / "Assets").mkdir()
    (tmp_path / "ProjectSettings").mkdir()
    (tmp_path / "ProjectSettings/ProjectVersion.txt").write_text("m_EditorVersion: 2021.3.0f1")
    (tmp_path / "Packages").mkdir()
    (tmp_path / "Packages/manifest.json").write_text("{}")
    return tmp_path


def helper():
    assert importlib.util.find_spec("cli.utils.custom_tool_scaffold") is not None, (
        "offline fresh-file scaffold is not implemented"
    )
    return importlib.import_module("cli.utils.custom_tool_scaffold")


def invoke(project, **changes):
    module = helper()
    options = {
        "class_name": "InventoryTool",
        "namespace": "My.Game",
        "tool_name": "inventory_tool",
        "project_root": project,
        "output": None,
        "template": "sync",
        "dry_run": False,
    }
    options.update(changes)
    return module.scaffold(module.ScaffoldOptions(**options))


def test_fresh_source_is_complete_and_default_path_preserved(project):
    receipt = invoke(project)
    path = project / receipt.destination
    assert path.is_file()
    assert "SchemaVersion = 1" in path.read_text()
    assert not path.with_suffix(".cs.meta").exists()
    assert not (project / ".unity-mcp-scaffold.lock").exists()
    assert not list(path.parent.glob(".unity-mcp-*.tmp"))


def test_existing_source_and_metadata_are_never_overwritten(project):
    path = project / "Assets/Editor/CustomTools/InventoryTool.cs"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"owned original")
    path.with_suffix(".cs.meta").write_bytes(b"guid: original")
    with pytest.raises(helper().ScaffoldError, match="destination_exists"):
        invoke(project)
    assert path.read_bytes() == b"owned original"
    assert path.with_suffix(".cs.meta").read_bytes() == b"guid: original"


def test_orphan_metadata_is_not_adopted(project):
    path = project / "Assets/Editor/CustomTools/InventoryTool.cs.meta"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"guid: orphan")
    with pytest.raises(helper().ScaffoldError, match="metadata_exists"):
        invoke(project)
    assert path.read_bytes() == b"guid: orphan"
    assert not path.with_suffix("").exists()


@pytest.mark.parametrize(
    "name",
    [
        "Parameters",
        "@Parameters",
        "HandleCommand",
        "@HandleCommand",
        "Result",
        "ActionKind",
        "JobRecord",
        "JobState",
        "ExecuteWork",
        "currentJob",
        "MaxTextLength",
        "System",
        "@Newtonsoft",
        "MCPForUnity",
        "class",
        "__Private",
        "bad-name",
        "x\\u0041",
        "e\u0301",
        "A\u200b",
        "a" * 65,
    ],
)
def test_invalid_class_refuses_without_writes(project, name):
    before = sorted(p.relative_to(project).as_posix() for p in project.rglob("*"))
    with pytest.raises(helper().ScaffoldError):
        invoke(project, class_name=name)
    assert sorted(p.relative_to(project).as_posix() for p in project.rglob("*")) == before


@pytest.mark.parametrize(
    "namespace",
    ["System.Game", "Systematic.Tools", "@System.Game", "A..B", "A.class", "A/B", "a." + "b" * 65],
)
def test_unsupported_namespace_refuses_before_writes(project, namespace):
    with pytest.raises(helper().ScaffoldError):
        invoke(project, namespace=namespace)
    assert not (project / "Assets/Editor").exists()


@pytest.mark.parametrize("namespace", ["", "@namespace.Tools", "Game.도구", "Other.Inventory"])
def test_supported_namespace_preserves_spelling(project, namespace):
    receipt = invoke(project, namespace=namespace)
    text = (project / receipt.destination).read_text()
    assert ("namespace " + namespace in text) if namespace else "namespace " not in text


@pytest.mark.parametrize(
    "output",
    [
        "../outside.cs",
        "Assets/NoEditor/A.cs",
        "Packages/Editor/A.cs",
        "Assets/editor/A.cs",
        "Assets/Editor/CON.cs",
        "Assets/Editor/A.cs.",
        "Assets/Editor/../A.cs",
        "Assets//Editor/A.cs",
        "C:/Editor/A.cs",
        "Assets/Editor/A.txt",
        "Assets/Editor/A:bad.cs",
    ],
)
def test_output_boundary_refuses(project, output):
    with pytest.raises(helper().ScaffoldError):
        invoke(project, output=output)
    assert not (project / "Assets/Editor").exists()


def test_dry_run_creates_nothing(project):
    before = list(project.rglob("*"))
    receipt = invoke(project, dry_run=True)
    assert not receipt.created
    assert receipt.reason == "dry_run"
    assert list(project.rglob("*")) == before


@pytest.mark.parametrize("suffix", ["asmdef", "asmref"])
def test_assembly_boundary_is_rejected(project, suffix):
    (project / f"Assets/user.{suffix}").write_text("{}")
    with pytest.raises(helper().ScaffoldError, match="assembly_boundary"):
        invoke(project)


def test_case_alias_is_rejected(project):
    (project / "Assets/editor").mkdir()
    with pytest.raises(helper().ScaffoldError, match="case_collision"):
        invoke(project)


def test_link_failure_removes_only_owned_prepublication_artifacts(project, monkeypatch):
    def refuse(*_args, **_kwargs):
        raise OSError("unsupported hard link")

    monkeypatch.setattr(helper().os, "link", refuse)
    with pytest.raises(helper().ScaffoldError, match="publication_failed"):
        invoke(project)
    assert not (project / "Assets/Editor").exists()
    assert not (project / ".unity-mcp-scaffold.lock").exists()


def test_racing_metadata_is_preserved_and_provenance_unknown(project, monkeypatch):
    original = os.link

    def import_race(src, dst, **kwargs):
        Path(str(dst) + ".meta").write_bytes(b"guid: raced")
        original(src, dst, **kwargs)

    monkeypatch.setattr(helper().os, "link", import_race)
    receipt = invoke(project)
    assert receipt.created
    assert receipt.metadata_provenance == "unknown"
    assert (project / receipt.destination).read_text().find("InventoryTool") >= 0
    assert (project / (receipt.destination + ".meta")).read_bytes() == b"guid: raced"


def test_metadata_created_after_real_link_preserves_both_published_paths(project, monkeypatch):
    original = os.link
    published = []

    def import_after_link(src, dst, **kwargs):
        original(src, dst, **kwargs)
        published.append(Path(dst).read_bytes())
        Path(str(dst) + ".meta").write_bytes(b"guid: after-publication")

    monkeypatch.setattr(helper().os, "link", import_after_link)
    receipt = invoke(project)
    assert receipt.created and receipt.cleanup_complete
    assert receipt.metadata_provenance == "unknown"
    assert (project / receipt.destination).read_bytes() == published[0]
    assert (project / (receipt.destination + ".meta")).read_bytes() == b"guid: after-publication"


def test_failed_staging_cleanup_preserves_published_destination(project, monkeypatch):
    original = Path.unlink

    def refuse(path, *args, **kwargs):
        if path.name.endswith(".tmp"):
            raise PermissionError("held staging")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", refuse)
    receipt = invoke(project)
    assert receipt.created and not receipt.cleanup_complete
    assert receipt.exit_code == 1
    assert (project / receipt.destination).is_file()
    assert len(receipt.residual_paths) == 1
    assert os.path.samefile(project / receipt.destination, project / receipt.residual_paths[0])


def test_busy_lock_is_not_removed(project):
    lock = project / ".unity-mcp-scaffold.lock"
    lock.write_bytes(b"foreign owner")
    with pytest.raises(helper().ScaffoldError, match="busy"):
        invoke(project)
    assert lock.read_bytes() == b"foreign owner"


def test_racing_source_is_never_replaced(project, monkeypatch):
    original = os.link

    def another_writer(src, dst, **kwargs):
        Path(dst).write_bytes(b"another writer")
        original(src, dst, **kwargs)

    monkeypatch.setattr(helper().os, "link", another_writer)
    with pytest.raises(helper().ScaffoldError, match="publication_failed"):
        invoke(project)
    destination = project / "Assets/Editor/CustomTools/InventoryTool.cs"
    assert destination.read_bytes() == b"another writer"
    assert not list(destination.parent.glob(".unity-mcp-*.tmp"))


def test_fsync_failure_removes_owned_staging_and_empty_directories(project, monkeypatch):
    def fail(_fd):
        raise OSError("flush failed")

    monkeypatch.setattr(helper().os, "fsync", fail)
    with pytest.raises(helper().ScaffoldError, match="publication_failed"):
        invoke(project)
    assert not (project / "Assets/Editor").exists()
    assert not (project / ".unity-mcp-scaffold.lock").exists()


def test_same_class_in_different_namespaces_is_preserved(project):
    one = invoke(project, namespace="One.Tools", output="Assets/Editor/One/InventoryTool.cs")
    two = invoke(
        project,
        namespace="Two.Tools",
        output="Assets/Editor/Two/InventoryTool.cs",
        tool_name="second_tool",
    )
    assert "namespace One.Tools" in (project / one.destination).read_text()
    assert "namespace Two.Tools" in (project / two.destination).read_text()


def test_oversized_directory_refuses_without_creating_output(project):
    for index in range(1025):
        (project / "Assets" / str(index)).touch()
    with pytest.raises(helper().ScaffoldError, match="directory_limit"):
        invoke(project)
    assert not (project / "Assets/Editor").exists()


def test_symlink_ancestor_refuses_without_touching_target(project, tmp_path):
    target = tmp_path / "outside"
    target.mkdir()
    try:
        (project / "Assets/Editor").symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip("symlink creation is not available on this filesystem/account")
    with pytest.raises(helper().ScaffoldError, match="unsafe_path"):
        invoke(project)
    assert list(target.iterdir()) == []


def test_unencodable_output_is_a_structured_prewrite_refusal(project):
    with pytest.raises(helper().ScaffoldError, match="invalid_output_path"):
        invoke(project, output="Assets/Editor/\ud800.cs")
    assert not (project / "Assets/Editor").exists()


def test_invalid_root_is_a_structured_refusal(project):
    with pytest.raises(helper().ScaffoldError, match="invalid_project_root"):
        invoke(Path(str(project) + "\0"))


def test_prepublication_cleanup_failure_is_disclosed(project, monkeypatch):
    def refuse_link(*_args, **_kwargs):
        raise OSError("unsupported link")

    def refuse_directory(*_args, **_kwargs):
        raise PermissionError("owned empty directory held")

    monkeypatch.setattr(helper().os, "link", refuse_link)
    monkeypatch.setattr(Path, "rmdir", refuse_directory)
    with pytest.raises(helper().ScaffoldError) as failure:
        invoke(project)
    assert failure.value.residual_paths == ("Assets/Editor/CustomTools", "Assets/Editor")


@pytest.mark.parametrize(
    "namespace,name",
    [
        ("Newtonsoft.Json.Linq", "JObject"),
        ("Newtonsoft.Json.Linq.JObject.Tools", "InventoryTool"),
        ("MCPForUnity.Editor", "Helpers"),
    ],
)
def test_dependency_qualified_name_shadowing_is_refused(project, namespace, name):
    with pytest.raises(helper().ScaffoldError, match="dependency_shadow"):
        invoke(project, namespace=namespace, class_name=name)
    assert not (project / "Assets/Editor").exists()


@pytest.mark.parametrize(
    "namespace,name",
    [
        ("MCPForUnity.Editor.Tools.Custom", "InventoryTool"),
        ("Newtonsoft.Json.Linq.Neighbor", "JObject"),
        ("newtonsoft.Json.Linq", "JObject"),
        ("@My.@Game", "@Task"),
        ("Newtonsoft.Json.Linq.Tools", "StringEnumConverter"),
    ],
)
def test_dependency_near_matches_preserve_supported_spelling(project, namespace, name):
    receipt = invoke(project, namespace=namespace, class_name=name)
    text = (project / receipt.destination).read_text()
    assert "namespace " + namespace in text
    assert "public static class " + name in text


def test_verbatim_dependency_shadow_is_refused(project):
    with pytest.raises(helper().ScaffoldError, match="dependency_shadow"):
        invoke(project, namespace="@Newtonsoft.Json.Linq", class_name="@JObject")


def test_concurrent_writer_is_busy_until_complete_publication(project, monkeypatch):
    admitted, release = Event(), Event()
    original = os.link

    def held_link(*args, **kwargs):
        admitted.set()
        assert release.wait(watchdog_timeout()), "test failed to release publication"
        return original(*args, **kwargs)

    monkeypatch.setattr(helper().os, "link", held_link)
    with ThreadPoolExecutor(max_workers=1) as executor:
        writer = executor.submit(invoke, project)
        try:
            assert admitted.wait(watchdog_timeout())
            with pytest.raises(helper().ScaffoldError, match="busy"):
                invoke(project)
        finally:
            release.set()
        receipt = writer.result(timeout=watchdog_timeout())
    assert receipt.created and receipt.cleanup_complete


def test_short_write_never_publishes_partial_source(project, monkeypatch):
    original = helper().os.fdopen

    @contextmanager
    def short_writer(descriptor, mode):
        with original(descriptor, mode) as stream:

            class Writer:
                fileno = stream.fileno
                flush = stream.flush

                def write(self, payload):
                    if len(payload) > 1000:
                        return stream.write(payload[:-1])
                    return stream.write(payload)

            yield Writer()

    monkeypatch.setattr(helper().os, "fdopen", short_writer)
    with pytest.raises(helper().ScaffoldError, match="publication_failed"):
        invoke(project)
    assert not (project / "Assets/Editor").exists()
    assert not (project / ".unity-mcp-scaffold.lock").exists()


def test_foreign_lock_replacement_is_preserved_after_publication(project, monkeypatch):
    original = os.link
    replacement = project / "foreign.lock"
    replacement.write_bytes(b"foreign replacement")

    def replace_lock(*args, **kwargs):
        original(*args, **kwargs)
        replacement.replace(project / ".unity-mcp-scaffold.lock")

    monkeypatch.setattr(helper().os, "link", replace_lock)
    receipt = invoke(project)
    assert receipt.created and not receipt.cleanup_complete
    assert (project / ".unity-mcp-scaffold.lock").read_bytes() == b"foreign replacement"
