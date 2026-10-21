"""Selected upstream history maps to explicit checks without executing upstream code."""

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tools import fork_compatibility as compat


def git(root, *args):
    return (
        subprocess.check_output(
            [
                "git",
                "-c",
                "core.hooksPath=" + os.devnull,
                "-c",
                "commit.gpgsign=false",
                "-c",
                "user.name=Fixture",
                "-c",
                "user.email=fixture@example.invalid",
                "-C",
                str(root),
                *args,
            ],
            env={**os.environ, "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull},
        )
        .decode()
        .strip()
    )


@pytest.fixture
def history(tmp_path):
    root = tmp_path / "repository"
    root.mkdir()
    git(root, "init", "-q")
    path = root / "owner.py"
    path.write_text("original\n")
    (root / "version.json").write_text("{}")
    git(root, "add", ".")
    git(root, "commit", "-qm", "base")
    start = git(root, "rev-parse", "HEAD")
    path.write_text("upstream mutation\n")
    git(root, "commit", "-qam", "mutation")
    path.write_text("original\n")
    (root / "new unknown.txt").write_text("unclassified")
    git(root, "add", ".")
    git(root, "commit", "-qm", "restore")
    manifest = {
        "schema_version": 1,
        "invariants": [
            {
                "id": "owner",
                "contract": "retain owner",
                "owners": ["owner.py"],
                "checks": ["test"],
                "version_evidence": ["version.json"],
            }
        ],
        "checks": {"test": {"kind": "components"}},
    }
    inventory_path = tmp_path / "inventory.json"
    inventory_path.write_text(json.dumps(manifest))
    return root, start, manifest, inventory_path


def test_range_keeps_reverted_changes_and_reports_unclassified_paths(history):
    root, start, inventory, path = history
    report = compat.inspect_range(root, start, "HEAD", inventory, path)
    assert report["impacts"][0]["paths"] == ["owner.py"]
    assert report["unclassified_paths"] == ["new unknown.txt"]
    assert len(report["upstream_range"]["commits"]) == 2
    assert report["validation"]["status"] == "not_run"
    assert report["fork_checkout"]["dirty"] is False


def test_renamed_owner_keeps_old_path_in_review_scope(history):
    root, start, inventory, path = history
    git(root, "mv", "owner.py", "renamed.py")
    git(root, "commit", "-qm", "rename")
    report = compat.inspect_range(root, start, "HEAD", inventory, path)
    assert report["impacts"][0]["paths"] == ["owner.py"]
    assert "renamed.py" in report["unclassified_paths"]


def test_nonancestral_range_is_refused(history):
    root, start, inventory, path = history
    with pytest.raises(subprocess.CalledProcessError):
        compat.inspect_range(root, "HEAD", start, inventory, path)


def test_actual_inventory_maps_the_four_required_drift_families():
    inventory = compat.load_inventory()
    cases = {
        "Server/src/services/tools/manage_physics.py": "catalog",
        "Server/src/core/config.py": "defaults",
        "Server/src/transport/plugin_hub.py": "ownership",
        "MCPForUnity/package.json": "identity",
    }
    for path, check in cases.items():
        impacts, remaining = compat.select(inventory, [path])
        assert not remaining
        assert check in {c for row in impacts for c in row["checks"]}


@pytest.mark.parametrize("path", ["../escape.py", "/outside.py", "C:/outside.py", "tools\\test.py"])
def test_inventory_paths_cannot_leave_checkout(tmp_path, path):
    with pytest.raises(ValueError):
        compat.local_path(tmp_path, path)


def test_dirty_checkout_cannot_be_reported_as_checked(history, tmp_path):
    root, start, inventory, path = history
    (root / "owner.py").write_text("uncommitted")
    report = compat.inspect_range(root, start, "HEAD", inventory, path)
    with pytest.raises(ValueError, match="clean committed"):
        compat.run_checks(root, inventory, report, tmp_path / "evidence")


def test_failed_mapped_contract_stays_failed(history, tmp_path, monkeypatch):
    from tools import check_release_evidence

    root, start, inventory, path = history
    report = compat.inspect_range(root, start, "HEAD", inventory, path)

    def reject(*_):
        raise ValueError("Wrong Unity package identity")

    monkeypatch.setattr(check_release_evidence, "components", reject)
    compat.run_checks(root, inventory, report, tmp_path / "evidence")
    assert report["validation"]["status"] == "failed"
    assert report["validation"]["checks"][0]["returncode"] == 1


def test_no_mapped_checks_cannot_be_reported_as_compatible(history, tmp_path):
    root, start, inventory, path = history
    report = compat.inspect_range(root, start, start, inventory, path)
    with pytest.raises(ValueError, match="No mapped checks"):
        compat.run_checks(root, inventory, report, tmp_path / "evidence")


@pytest.mark.parametrize("exit_code", [0, 1])
def test_real_check_process_is_current_fork_code_with_preserved_exit(history, tmp_path, exit_code):
    root, start, inventory, path = history
    (root / "check.py").write_text(
        f"import sys\nassert sys.argv[1:] == ['--check']\nraise SystemExit({exit_code})\n"
    )
    git(root, "add", "check.py")
    git(root, "commit", "-qm", "fork contract check")
    inventory["checks"]["test"] = {"kind": "generator", "path": "check.py"}
    report = compat.inspect_range(root, start, "HEAD", inventory, path)
    compat.run_checks(root, inventory, report, tmp_path / "evidence")
    assert report["validation"]["status"] == ("passed" if exit_code == 0 else "failed")
    check = report["validation"]["checks"][0]
    assert check["returncode"] == exit_code
    assert check["log_sha256"] == compat.digest(check["log"])
    assert report["validation"]["checkout_unchanged"]
