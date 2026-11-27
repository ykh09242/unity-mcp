"""A project-scoped edit trial must prove both sides of its filesystem boundary."""

import json
from pathlib import Path
import subprocess
import sys

import pytest

from tools.experiments import live_agent_workspace as scope
from tools.experiments.live_editor_fixture import lane


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setattr(lane, "WORK", tmp_path)
    owned = tmp_path / "lane-0"
    project = owned / "project"
    project.mkdir(parents=True)
    (owned / "owned-lane.json").write_text(
        json.dumps({"lane": 0, "project": str(project), "revision": lane.REVISION})
    )
    return project


def test_command_uses_only_the_owned_project_and_preserves_original_stdin(project):
    cmd = ["codex", "exec", "--approve-for-me", "--ignore-user-config", "-C", "neutral", "-"]
    actual = scope.project_scope_command(cmd, project=project)
    assert actual[actual.index("-C") + 1] == str(project)
    assert cmd[-2:] == ["neutral", "-"] and actual[-1] == "-"
    assert "--approve-for-me" in actual and "--ignore-user-config" in actual
    assert 'sandbox_mode="workspace-write"' in actual and "--add-dir" not in actual
    assert "sandbox_workspace_write.writable_roots=[]" in actual
    with pytest.raises(ValueError):
        scope.project_scope_command(cmd, project=project.parent)


def test_native_identity_stays_available_without_a_heartbeat_file(project, monkeypatch):
    owned = project.parent
    (owned / "launch.json").write_text(json.dumps({"pid": 42, "project": str(project)}))
    response = {"ok": True, "pid": 42, "project_root": str(project), "instance": "project@12345678"}

    def ping(directory, operation, *, timeout):
        assert directory == owned and operation == "ping" and timeout > 0
        return response

    monkeypatch.setattr(lane, "request", ping)
    assert scope.verify_project_editor(project, "project@12345678") == "project@12345678"
    for key, other in (("pid", 55), ("project_root", str(owned)), ("instance", "other@87654321")):
        expected = response[key]
        response[key] = other
        with pytest.raises(scope.ActorCommissioningError, match="owned launch"):
            scope.verify_project_editor(project, "project@12345678")
        response[key] = expected


def test_probe_rejects_unrestricted_child_and_leaves_no_markers(project, monkeypatch):
    def unrestricted(cmd, **kwargs):
        # Deliberately execute the same two controlled operations without filesystem confinement.
        return subprocess.run(cmd[cmd.index("--") + 1 :], **kwargs)

    monkeypatch.setattr(scope, "run_sandbox_probe", unrestricted)
    with pytest.raises(RuntimeError, match="outside"):
        scope.verify_project_scope(sys.executable, project)
    assert list(project.iterdir()) == []
    assert {p.name for p in project.parent.iterdir()} == {"owned-lane.json", "project"}


def test_project_write_requires_real_changed_bytes_and_denied_sibling(project, monkeypatch):
    def no_write(cmd, **kwargs):
        return subprocess.CompletedProcess(
            cmd, 0, stdout='{"outside_write_denied": true}', stderr=""
        )

    monkeypatch.setattr(scope, "run_sandbox_probe", no_write)
    with pytest.raises(RuntimeError, match="inside"):
        scope.verify_project_scope(sys.executable, project)


def test_successful_probe_and_actor_share_the_same_root_and_sandbox_settings(project, monkeypatch):
    cmd = ["codex", "exec", "--approve-for-me", "--ignore-user-config", "-C", "neutral", "-"]
    actor = scope.project_scope_command(cmd, project=project)

    def restricted(command, **kwargs):
        assert kwargs["cwd"] == project
        assert command[2 : command.index("--")] == actor[actor.index("-c") : -1]
        inside, outside, nonce = command[-3:]
        assert Path(inside).parent == project
        assert Path(outside).parent == project.parent
        Path(inside).write_text(nonce, encoding="ascii")
        return subprocess.CompletedProcess(command, 0, stdout='{"outside_write_denied": true}')

    monkeypatch.setattr(scope, "run_sandbox_probe", restricted)
    proof = scope.verify_project_scope(sys.executable, project)
    assert proof["inside_write_verified"] and proof["outside_write_denied"]
    assert proof["project"] == str(project)
    assert len(proof["actor_executable_sha256"]) == len(proof["inside_marker_sha256"]) == 64
    assert list(project.iterdir()) == []
