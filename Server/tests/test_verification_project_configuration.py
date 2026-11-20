"""Compare settings-only commits and their registered check selections."""

import json

import pytest

from services.verification.git_draft import git_draft
from services.verification.player_candidate import prepare_candidate
from services.verification.project_configuration import LOCK_PATH, package_lock_evidence
from tests.test_verification_git_draft import git

QUALITY = "ProjectSettings/QualitySettings.asset"
LOCK = b'{"dependencies":{"com.example.render":{"version":"1.0.0","depth":0}}}'
REORDERED = b'{ "dependencies": { "com.example.render": { "depth": 0, "version": "1.0.0" } } }'


@pytest.mark.parametrize(
    ("path", "before", "after", "effect", "tests"),
    [
        (
            QUALITY,
            b"customRenderPipeline: {fileID: 0}",
            b"customRenderPipeline: {fileID: 11400000, guid: 11111111111111111111111111111111, type: 2}",
            "render_settings_changed",
            ["Render.QualityPipeline"],
        ),
        (LOCK_PATH, LOCK, REORDERED, "representation_only", []),
        (
            LOCK_PATH,
            LOCK,
            LOCK.replace(b"1.0.0", b"1.1.0"),
            "package_resolution_changed",
            ["Render.Packages"],
        ),
    ],
)
def test_sole_project_input_change(tmp_path, monkeypatch, path, before, after, effect, tests):
    project = tmp_path / "project"
    source = project / path
    source.parent.mkdir(parents=True)
    git(project, "init")
    git(project, "config", "core.autocrlf", "false")
    source.write_bytes(before)
    git(project, "add", ".")
    git(project, "commit", "-m", "baseline")
    source.write_bytes(after)
    git(project, "add", ".")
    git(project, "commit", "-m", "candidate")
    draft = git_draft(project, "HEAD~1", "committed", candidate_ref="HEAD")
    assert draft["changed_paths"] == [{"path": path, "kind": "modified"}]
    assert draft["baseline"]["source_revision"] != draft["candidate"]["source_revision"]
    assert draft["configuration_changes"][0]["effect"] == effect
    registrations = tmp_path / "registrations"
    monkeypatch.setenv("UNITY_MCP_VERIFICATION_ROOT", str(registrations))
    for side in ("baseline", "candidate"):
        directory = registrations / side
        directory.mkdir(parents=True)
        doc = {
            **draft[side],
            "schema_version": 1,
            "scenario": "lighting",
            "project": str(project),
            "build_id": "a" * 32,
            "payload_hash": "b" * 64,
            "broader_check": "all-tests",
            "mappings": [
                {"path": QUALITY, "tests": ["Render.QualityPipeline"]},
                {"path": LOCK_PATH, "tests": ["Render.Packages"]},
            ],
        }
        (directory / "candidate.json").write_text(json.dumps(doc), encoding="utf-8")
    report = prepare_candidate(
        {"projectRoot": str(project)},
        {"candidate_id": "candidate", "baseline_candidate_id": "baseline"},
    ).comparison()
    assert report["configuration_changes"] == draft["configuration_changes"]
    assert report["changed_paths"] == [path]
    assert report["selected_checks"]["checks"] == [
        {"id": name, "kind": "test", "reasons": [path]} for name in tests
    ]
    assert report["selected_checks"]["mapping_complete"]


def test_package_lock_requires_unambiguous_object_and_preserves_array_order():
    with pytest.raises(ValueError, match="duplicate_json_key"):
        package_lock_evidence(b'{"dependencies":{},"dependencies":{}}')
    first = package_lock_evidence(b'{"dependencies":{},"order":["one","two"]}')
    second = package_lock_evidence(b'{"dependencies":{},"order":["two","one"]}')
    assert first.semantic_sha256 != second.semantic_sha256
