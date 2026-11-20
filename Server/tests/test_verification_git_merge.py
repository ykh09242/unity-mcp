"""Verify an isolated real Git merge and its static Resources evidence through the CLI."""

import json
from contextlib import contextmanager
from time import perf_counter

import pytest
from cli.main import cli
from click.testing import CliRunner
from services.verification.merge_resources import compare_resources

from .test_verification_git_draft import git

_GUID = "e" * 32
_BEFORE = "Assets/Resources/UI/Shop.prefab"
_AFTER = "Assets/Resources/Store/Shop.prefab"
_SCRIPT = "Assets/Consume.cs"


@pytest.fixture
def repo(tmp_path):
    git(tmp_path, "init")
    git(tmp_path, "config", "core.autocrlf", "false")
    project = tmp_path / "Game"
    (project / "ProjectSettings").mkdir(parents=True)
    (project / "ProjectSettings/ProjectVersion.txt").write_bytes(b"m_EditorVersion: 6000.0.69f1\n")
    asset = project / _BEFORE
    asset.parent.mkdir(parents=True)
    asset.write_bytes(b"prefab: synthetic\n")
    (project / (_BEFORE + ".meta")).write_bytes(f"guid: {_GUID}\n".encode())
    (project / _SCRIPT).write_bytes(
        b"// base\nusing UnityEngine;\nclass Consume {\n    void Open() { }\n}\n"
    )
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-m", "base")
    git(tmp_path, "branch", "fixture-base")
    git(tmp_path, "switch", "-c", "receiving")
    return project


def _invoke(project):
    result = CliRunner().invoke(
        cli,
        [
            "--no-project-context",
            "verification-candidate",
            "anticipate-merge",
            "--project",
            str(project),
            "--target",
            "receiving",
            "--head",
            "review-head",
        ],
    )
    assert result.output.startswith("{"), (result.output, result.exception)
    return result.exit_code, json.loads(result.output)


@pytest.mark.parametrize(("lookup", "key"), [("Load", "UI/Shop"), ("LoadAll", "UI")])
def test_reports_only_merged_resource_regression_from_actual_merged_blob(
    repo, tmp_path, lookup, key, request
):
    script = repo / _SCRIPT
    original = script.read_bytes()
    (repo / _AFTER).parent.mkdir()
    for extension in ("", ".meta"):
        git(repo, "mv", _BEFORE + extension, _AFTER + extension)
    script.write_bytes(original.replace(b"// base", b"// receiving branch"))
    git(repo, "add", _SCRIPT)
    git(repo, "commit", "-m", "move resource and annotate script")

    git(repo, "switch", "-c", "review-head", "fixture-base")
    caller = f'void Open() {{ Resources.{lookup}("{key}"); }}'.encode()
    script.write_bytes(original.replace(b"void Open() { }", caller))
    git(repo, "add", _SCRIPT)
    git(repo, "commit", "-m", "add old Resources lookup")

    expected = tmp_path / "expected-merged-content"
    expected.write_bytes(script.read_bytes().replace(b"// base", b"// receiving branch"))
    expected_blob = git(repo, "hash-object", str(expected)).decode().strip()
    script.write_bytes(b"// staged content must be ignored\n")
    git(repo, "add", _SCRIPT)
    script.write_bytes(b"// additional working edit\n")
    untracked = repo / "Assets/Untracked.cs"
    untracked.write_bytes(b"// untracked content\n")
    index = tmp_path / ".git/index"
    index_before = index.read_bytes()
    objects = tmp_path / ".git/objects"
    original_objects = sorted(path.relative_to(objects) for path in objects.rglob("*"))
    revisions = {
        name: git(repo, "rev-parse", ref).decode().strip()
        for name, ref in (
            ("base", "fixture-base"),
            ("target", "receiving"),
            ("head", "review-head"),
        )
    }

    started = perf_counter()
    code, report = _invoke(repo)
    seconds = perf_counter() - started

    assert code == 1 and report["status"] == "regression", report
    assert report["commits"] == revisions
    resources = report["resources"]
    sides = resources["snapshots"]
    assert all(not sides[name]["failures"] for name in ("base", "target", "head"))
    assert sides["head"]["resolved_references"] == 1
    [failure] = resources["regressions"]
    assert (failure["blob"], failure["path"], failure["key"], failure["reason"]) == (
        expected_blob,
        _SCRIPT,
        key,
        "key_not_found",
    )
    assert failure["previous_providers"] == [
        {"guid": _GUID, "path": _BEFORE, "merged_path": _AFTER}
    ]
    assert index.read_bytes() == index_before
    assert script.read_bytes() == b"// additional working edit\n"
    assert untracked.read_bytes() == b"// untracked content\n"
    assert git(repo, "rev-parse", "HEAD").decode().strip() == revisions["head"]
    assert sorted(path.relative_to(objects) for path in objects.rglob("*")) == original_objects
    request.node.user_properties.append(
        (
            "merge_observation",
            json.dumps(
                {
                    "exit_code": code,
                    "status": report["status"],
                    "seconds": round(seconds, 6),
                    "lookup": lookup,
                    "commits": report["commits"],
                    "trees": report["trees"],
                    "snapshots": sides,
                    "regressions": resources["regressions"],
                },
                sort_keys=True,
            ),
        )
    )


def test_reports_git_conflict_without_treating_marker_tree_as_source(repo):
    script = repo / _SCRIPT
    original = script.read_bytes()
    script.write_bytes(original.replace(b"// base", b"// receiving version"))
    git(repo, "add", _SCRIPT)
    git(repo, "commit", "-m", "receiving edit")
    git(repo, "switch", "-c", "review-head", "fixture-base")
    script.write_bytes(original.replace(b"// base", b"// conflicting head version"))
    git(repo, "add", _SCRIPT)
    git(repo, "commit", "-m", "conflicting edit")

    code, report = _invoke(repo)

    assert code == 1 and report["status"] == "conflict", report
    assert report["conflicts"]["paths"] == ["Game/" + _SCRIPT]
    assert {row["type"] for row in report["conflicts"]["details"]} == {"CONFLICT (contents)"}
    assert "resources" not in report and "merged" not in report["trees"]


def test_refuses_directory_without_a_tracked_unity_project(repo, tmp_path):
    doc_root = tmp_path / "docs"
    (doc_root / "Assets").mkdir(parents=True)
    (doc_root / "Assets/Readme.txt").write_bytes(b"a documentation asset\n")
    git(tmp_path, "add", "docs")
    git(tmp_path, "commit", "-m", "track a documentation directory with an assets folder")
    git(repo, "branch", "review-head")

    code, report = _invoke(doc_root)

    assert code == 2 and report["status"] == "unavailable", report
    assert report["reason"].startswith("candidate_anticipated_merge_project_scope_missing")
    assert "resources" not in report


def test_refuses_changed_protected_tree_names_before_generating_the_merge(repo, tmp_path):
    blob = git(tmp_path, "rev-parse", "HEAD:Game/" + _SCRIPT).decode().strip()
    protected = "Game/Packages/fixture/Assets/Resources/GameData/Private.asset"
    git(tmp_path, "update-index", "--add", "--cacheinfo", f"100644,{blob},{protected}")
    git(tmp_path, "commit", "-m", "introduce a synthetic protected Git path")
    git(repo, "branch", "review-head", "fixture-base")

    code, report = _invoke(repo)

    assert code == 2 and report["status"] == "unavailable", report
    assert report["reason"] == "candidate_anticipated_merge_protected_paths_changed"
    assert "merged" not in report["trees"] and protected not in json.dumps(report)


def test_never_catalogs_unchanged_nested_protected_resources():
    class TreeWithProtectedNames:
        def read(self, *args):
            if "-r" in args:
                return (
                    b"100644 blob "
                    + b"1" * 40
                    + b"\tGame/Packages/fixture/Assets/Resources/GameData/Private.asset\0"
                )
            return (
                b"040000 tree " + b"2" * 40 + b"\tGame/Assets\0"
                b"100644 blob " + b"3" * 40 + b"\tGame/ProjectSettings/ProjectVersion.txt\0"
            )

        @contextmanager
        def blobs(self, requested):
            assert not requested
            yield iter(())

        def remaining(self):
            return 60.0

    trees = dict.fromkeys(("base", "target", "head", "merged"), "e" * 40)
    resources = compare_resources(TreeWithProtectedNames(), trees, "Game/")
    assert resources["regressions"] == []
