"""Harness cleanup and reuse never acquire unrelated projects or run resources."""

import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import local_harness as lh


@pytest.mark.parametrize("name", ["GameAssets", "MyAssets", "Assets"])
def test_reuse_project_name_is_not_a_partial_assets_suffix(tmp_path, name):
    requested = tmp_path / name
    requested.mkdir()
    status = tmp_path / "status"
    status.mkdir()
    foreign = status / "unity-mcp-status-aaaaaaaa.json"
    foreign.write_text(
        json.dumps({"project_path": str(tmp_path / "Game" / "Assets")}), encoding="utf-8"
    )
    assert lh._find_reuse_status(status, requested) is None
    owned = status / "unity-mcp-status-bbbbbbbb.json"
    owned.write_text(json.dumps({"project_path": str(requested / "Assets")}), encoding="utf-8")
    assert Path(lh._find_reuse_status(status, requested)) == owned


def test_docker_uses_unique_names_and_removes_only_returned_owned_ids(tmp_path, monkeypatch):
    calls = []
    ids = iter(["a" * 64, "b" * 64])

    def command(argv, **kwargs):
        calls.append(argv)
        if argv[:2] == ["docker", "run"]:
            return subprocess.CompletedProcess(argv, 0, stdout=next(ids) + "\n")
        return subprocess.CompletedProcess(argv, 0, stdout="running\n")

    monkeypatch.setattr(lh.subprocess, "run", command)
    monkeypatch.setenv("UNITY_IMAGE", "owned-image")
    args = lh.build_arg_parser().parse_args(["--ci"])
    first, second = lh.DockerLauncher(args), lh.DockerLauncher(args)
    a = first.launch("ignored", tmp_path, tmp_path, tmp_path / "log", [])
    b = second.launch("ignored", tmp_path, tmp_path, tmp_path / "log", [])
    runs = [call for call in calls if call[:2] == ["docker", "run"]]
    assert runs[0][runs[0].index("--name") + 1] != runs[1][runs[1].index("--name") + 1]
    assert all("--label" in call for call in runs)
    assert not any(call[:3] == ["docker", "rm", "-f"] for call in calls)
    assert a.container == "a" * 64 and b.container == "b" * 64
    first.teardown(b)
    assert not any(call[:3] == ["docker", "rm", "-f"] for call in calls)
    first.teardown(a)
    second.teardown(b)
    assert [call[-1] for call in calls if call[:3] == ["docker", "rm", "-f"]] == [
        a.container,
        b.container,
    ]


def test_docker_never_recursively_changes_supplied_directory_permissions(tmp_path, monkeypatch):
    foreign = tmp_path / "foreign.txt"
    foreign.write_text("owned by caller", encoding="utf-8")
    before = foreign.stat().st_mode
    calls = []
    monkeypatch.setattr(lh.subprocess, "run", lambda argv, **kwargs: calls.append(argv))
    launcher = lh.DockerLauncher(lh.build_arg_parser().parse_args(["--ci"]))
    launcher.fixup_permissions(tmp_path)
    assert calls == []
    assert foreign.stat().st_mode == before


@pytest.mark.parametrize("keep_alive,stop_fails", [(False, False), (True, False), (False, True)])
def test_status_cleanup_follows_editor_lifetime(tmp_path, monkeypatch, keep_alive, stop_fails):
    state = {"stopped": False}

    def launch(editor, project, status, log, extra):
        state["status"] = status
        (status / "unity-mcp-status-owned.json").write_text("{}", encoding="utf-8")
        return lh.Handle(container="owned-editor", log_path=str(log))

    def stop(handle):
        state["stopped"] = not stop_fails

    launcher = SimpleNamespace(
        resolve_editor=lambda *_: lh.EditorSpec("owned-editor", "6000"),
        launch=launch,
        teardown=stop,
        is_alive=lambda _: not state["stopped"],
        tail_log=lambda *_: "",
    )
    monkeypatch.setattr(lh, "make_launcher", lambda _: launcher)
    monkeypatch.setattr(
        lh, "wait_for_ready", lambda *_: lh.ReadyInfo(6400, "owned@01234567", "owned.json")
    )
    monkeypatch.setattr(lh, "compile_probe", lambda *_: True)
    monkeypatch.setattr(
        lh, "run_smoke_leg", lambda *a, **k: lh.LegOutcome("smoke", "pass", blocking=True)
    )
    monkeypatch.setattr(lh.signal, "signal", lambda *_: None)
    monkeypatch.setenv("UNITY_MCP_STATUS_DIR", "original-fixture")
    monkeypatch.setenv("UNITY_MCP_DEFAULT_INSTANCE", "original@01234567")
    mkdtemp = lh.tempfile.mkdtemp
    monkeypatch.setattr(lh.tempfile, "mkdtemp", lambda **kw: mkdtemp(**{"dir": tmp_path, **kw}))
    reports = tmp_path / "reports"
    args = [
        "--project-path",
        str(tmp_path / "project"),
        "--no-warmup",
        "--legs",
        "smoke",
        "--reports",
        str(reports),
        "--junit",
        str(reports / "junit.xml"),
    ]
    if keep_alive:
        args.append("--keep-alive")
    assert lh.main(args) == 0
    retained = keep_alive or stop_fails
    assert state["status"].exists() is retained
    assert state["stopped"] is not retained
    if retained:
        receipt = json.loads((reports / "reuse.json").read_text(encoding="utf-8"))
        assert receipt["status_dir"] == str(state["status"])
        assert (Path(receipt["status_dir"]) / "unity-mcp-status-owned.json").exists()


def test_ci_scene_reuses_run_identity_and_removes_only_owned_files(tmp_path):
    project = tmp_path / "project"
    existing = project / "Assets" / "__MCPHarness"
    existing.mkdir(parents=True)
    foreign = existing / "User.unity"
    foreign.write_text("user scene", encoding="utf-8")
    args = lh.build_arg_parser().parse_args(["--ci", "--project-path", str(project)])
    names = []

    def save(command, params, **kwargs):
        names.append(params["name"])
        target = project / params["path"] / (params["name"] + ".unity")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("owned scene", encoding="utf-8")
        Path(str(target) + ".meta").write_text("owned meta", encoding="utf-8")
        return {"success": True}

    assert lh.prepare_ci_scene(args, "owned@01234567", send=save)
    assert lh.prepare_ci_scene(args, "owned@01234567", send=save)
    assert names[0] == names[1]
    lh.cleanup_ci_scene(args, project)
    assert sorted(p.name for p in existing.iterdir()) == ["User.unity"]
    assert foreign.read_text(encoding="utf-8") == "user scene"


def test_docker_permission_repair_targets_only_owned_status_files(tmp_path, monkeypatch):
    directory = tmp_path / "run"
    directory.mkdir()
    owned = directory / "unity-mcp-status-01234567.json"
    owned.write_text("{}", encoding="utf-8")
    foreign = directory / "unrelated.txt"
    foreign.write_text("preserve", encoding="utf-8")
    calls = []

    def command(argv, **kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, stdout="a" * 64 + "\n")

    monkeypatch.setattr(lh.subprocess, "run", command)
    monkeypatch.setattr(lh.os, "getuid", lambda: owned.stat().st_uid + 1, raising=False)
    monkeypatch.setattr(lh.os, "getgid", lambda: 4242, raising=False)
    monkeypatch.setenv("UNITY_IMAGE", "owned-image")
    args = lh.build_arg_parser().parse_args(["--ci"])
    args._owned_status_dir = directory
    launcher = lh.DockerLauncher(args)
    handle = launcher.launch("unused", tmp_path, directory, directory / "log", [])
    launcher.fixup_permissions(directory)
    repairs = [call for call in calls if call[:2] == ["docker", "exec"]]
    assert len(repairs) == 1
    assert repairs[0][2] == handle.container
    assert repairs[0][3:5] == ["chown", "--no-dereference"]
    assert repairs[0][6:] == [str(owned)]
    assert foreign.read_text(encoding="utf-8") == "preserve"
    assert all("-R" not in call and "a+rwx" not in call for call in calls)
