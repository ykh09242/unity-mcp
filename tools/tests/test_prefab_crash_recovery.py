"""No-license controls for PID selection, hard process death and independent disk observations."""

import json
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from prefab_crash_recovery import (
    CUTS,
    OBSERVER,
    RECOVERY_RUNNER_INPUTS,
    STAGED_OBSERVER,
    admit_source,
    disk_observation,
    publish_case,
    terminate_at_cut,
    verify_reopened,
)


# The Windows venv redirector has a different PID from the interpreter running this marker.
CHILD = """
import json, os, pathlib, sys, time
directory, boundary, fault = sys.argv[1:]
marker = pathlib.Path(directory, "boundary.json")
if fault == "exit":
    sys.exit(0)
value = {"run_id": os.environ["MCP_PREFAB_FIXTURE_RUN"], "boundary": boundary, "process_id": os.getpid()}
if fault != "none":
    value[fault] = "unrelated"
temp = marker.with_suffix(".tmp")
temp.write_text(json.dumps(value))
temp.rename(marker)
time.sleep(30)
(marker.parent / "returned").touch()
"""


@pytest.mark.parametrize("cut", CUTS, ids=lambda cut: cut.boundary)
def test_parent_kills_only_its_own_barrier_child(tmp_path, cut):
    command = [sys._base_executable, "-c", CHILD, str(tmp_path), cut.boundary, "none"]
    launch = terminate_at_cut(command, tmp_path, cut, timeout=10)
    assert launch["forced_termination"] and launch["process_ended"]
    assert launch["actual_exit_code"] != 0
    marker = json.loads((tmp_path / "boundary.json").read_text())
    assert marker["process_id"] == launch["process_id"]
    assert not (tmp_path / "returned").exists()


@pytest.mark.parametrize("fault", ["process_id", "run_id", "boundary", "exit"])
def test_early_exit_and_foreign_markers_cannot_certify_a_cut(tmp_path, fault):
    cut = CUTS[0]
    with pytest.raises(ValueError, match="owned PID|Expected a process death"):
        terminate_at_cut(
            [sys._base_executable, "-c", CHILD, str(tmp_path), cut.boundary, fault],
            tmp_path,
            cut,
            timeout=10,
        )


def asset_tree(project, cut):
    root = "Assets/__McpAddressables_" + "1" * 32
    group = root + "/Config/AssetGroups/OwnedA.asset"
    (project / group).parent.mkdir(parents=True)
    guid, source, user, root_guid = (c * 32 for c in "abcd")
    for asset, asset_guid in [
        (root + "/Template.prefab", source),
        (root + "/Result.prefab", guid),
        (root, root_guid),
    ]:
        (project / (asset + ".meta")).write_text("guid: " + asset_guid + "\n")
    (project / (root + "/Result.prefab")).write_bytes(b"sample")
    yaml = f"  - m_GUID: {user}\n    m_Address: owned/first\n"
    if cut.entry_count:
        yaml += f"  - m_GUID: {guid}\n    m_Address: {root}/Result.prefab\n"
    (project / group).write_bytes(yaml.encode())
    return {
        "root": root,
        "root_guid": root_guid,
        "group": group,
        "prefab": root + "/Result.prefab",
        "settings": root + "/Config/Settings.asset",
        "run_id": "f" * 32,
        "user_entry_guid": user,
        "session": "writer",
    }


@pytest.mark.parametrize("cut", CUTS, ids=lambda cut: cut.boundary)
def test_reopened_evidence_must_match_disk_and_keep_unknown_fenced(tmp_path, cut):
    project = tmp_path / "project"
    marker = asset_tree(project, cut)
    before = disk_observation(project, marker, cut, intervene=True)
    after = disk_observation(project, marker, cut, intervene=False)
    assert before == after
    assert after["entry_count"] == cut.entry_count and after["guid"] == "a" * 32
    crash = tmp_path / cut.crash
    crash.mkdir()
    (crash / "boundary.json").write_text(json.dumps(marker))
    (crash / "disk.json").write_text(json.dumps(before))
    reopen = tmp_path / cut.reopen
    reopen.mkdir()
    observed = {
        **after,
        "run_id": marker["run_id"],
        "session": "reopened",
        "process_id": 17,
        "state": "conflict" if cut.phase == "created" else "unknown",
        "recorded_phase": cut.phase,
    }
    evidence = reopen / "reopened.json"
    evidence.write_text(json.dumps(observed))
    assert verify_reopened(project, reopen, cut, {"process_id": 17}) == observed
    for field, wrong in [
        ("state", "registered"),
        ("session", "writer"),
        ("guid", "b" * 32),
        ("entry_count", 2),
        ("user_address", "old"),
    ]:
        evidence.write_text(json.dumps({**observed, field: wrong}))
        with pytest.raises(ValueError, match="evidence differ"):
            verify_reopened(project, reopen, cut, {"process_id": 17})


@pytest.mark.parametrize(
    "fault", ["duplicate_entry", "wrong_root", "duplicate_prefab", "user_changed", "foreign_path"]
)
def test_intervention_refuses_ambiguous_disk_or_foreign_paths(tmp_path, fault):
    cut = CUTS[-1]
    marker = asset_tree(tmp_path, cut)
    group = tmp_path / marker["group"]
    if fault == "duplicate_entry":
        group.write_bytes(group.read_bytes() + group.read_bytes())
    elif fault == "wrong_root":
        marker["root_guid"] = "0" * 32
    elif fault == "duplicate_prefab":
        (tmp_path / marker["root"] / "Result 1.prefab").touch()
    elif fault == "user_changed":
        group.write_bytes(group.read_bytes().replace(marker["prefab"].encode(), b"changed"))
    else:
        marker["group"] = "Assets/foreign.asset"
    original = group.read_bytes()
    with pytest.raises(ValueError):
        disk_observation(tmp_path, marker, cut, intervene=True)
    assert group.read_bytes() == original


@pytest.mark.parametrize(
    "fault",
    ["none", "dirty_admission", "dirty_runner", "new_head", "staged_edit", "extra_compiler_input"],
)
def test_native_attribution_binds_admitted_commit_and_prepared_bytes(tmp_path, fault):
    repo = tmp_path / "checkout"
    profile = tmp_path / "profile"
    repo.mkdir()

    def git(*args):
        return subprocess.check_output(
            ["git", "-c", "user.name=fixture", "-c", "user.email=fixture@example.invalid", *args],
            cwd=repo,
            text=True,
        ).strip()

    git("init", "--quiet")
    for name in (
        *RECOVERY_RUNNER_INPUTS,
        OBSERVER,
        "MCPForUnity/Editor/Workflow.cs",
        "Fixtures/Reopen.cs",
    ):
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"admitted bytes\n")
    git("add", ".")
    git("commit", "--quiet", "-m", "admitted inputs")
    fixtures = {"Assets/Tests/Reopen.cs": repo / "Fixtures/Reopen.cs"}
    with pytest.raises(ValueError, match="requested source revision"):
        admit_source(repo, profile / "source", fixtures, "0" * 40)
    if fault == "dirty_admission":
        (repo / "Fixtures/Reopen.cs").write_bytes(b"uncommitted fixture")
        with pytest.raises(ValueError, match="clean, committed"):
            admit_source(repo, profile / "source", fixtures)
        assert not (profile / "process-death.json").exists()
        return
    source = admit_source(repo, profile / "source", fixtures)
    assert source.commit == git("rev-parse", "HEAD")
    prepared = profile / "prepared"
    shutil.copytree(source.archive_root / "MCPForUnity", prepared / "package")
    (prepared / STAGED_OBSERVER).parent.mkdir(parents=True)
    shutil.copyfile(source.archive_root / OBSERVER, prepared / STAGED_OBSERVER)
    test_file = prepared / "project/Assets/Tests/Reopen.cs"
    test_file.parent.mkdir(parents=True)
    shutil.copyfile(source.archive_root / "Fixtures/Reopen.cs", test_file)
    source.verify(prepared)
    if fault == "none":
        return
    if fault == "dirty_runner":
        (repo / RECOVERY_RUNNER_INPUTS[0]).write_text("changed")
    elif fault == "new_head":
        git("commit", "--allow-empty", "--quiet", "-m", "changed checkout")
    elif fault == "staged_edit":
        test_file.write_text("changed")
    else:
        (test_file.parent / "Foreign.cs").touch()
    with pytest.raises(ValueError, match="clean, committed|changed|Unexpected"):
        publish_case(profile, source)
    assert not (profile / "process-death.json").exists()
