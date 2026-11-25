"""Candidate Editor lanes consume the intended commit with frozen comparison dependencies."""

import json
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.experiments.live_editor_fixture import lane, registry


def test_candidate_archive_contains_selected_commit_package(monkeypatch, tmp_path):
    # Given the current repository commit plus a private trial location.
    revision = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=lane.ROOT, text=True
    ).strip()
    expected_manifest = subprocess.check_output(
        ["git", "show", f"{revision}:MCPForUnity/package.json"], cwd=lane.ROOT
    )
    monkeypatch.setattr(lane, "WORK", tmp_path)
    monkeypatch.setattr(lane, "REVISION", revision)

    # When the fixture extracts the selected archive.
    package = lane.product_archive(tmp_path)

    # Then the archive and extracted package carry the same commit identity and bytes.
    assert (package / "package.json").read_bytes() == expected_manifest
    with zipfile.ZipFile(tmp_path / "mcp.zip") as archive:
        assert archive.comment == revision.encode("ascii")


def test_candidate_archive_rejects_archive_from_another_commit(monkeypatch, tmp_path):
    # Given a private trial directory with an archive from a different commit.
    monkeypatch.setattr(lane, "WORK", tmp_path)
    monkeypatch.setattr(lane, "REVISION", "0" * 40)
    subprocess.run(
        ["git", "archive", "-o", str(tmp_path / "mcp.zip"), "HEAD", "MCPForUnity"],
        cwd=lane.ROOT,
        check=True,
    )

    # When the fixture tries to consume it as the requested commit.
    # Then it rejects the old source before extracting anything.
    with pytest.raises(ValueError, match="does not match commit"):
        lane.product_archive(tmp_path)
    assert not (tmp_path / "MCPForUnity").exists()


@pytest.mark.parametrize("candidate", [None, "file:/candidate/MCPForUnity"])
def test_registry_replay_keeps_baseline_inventory_and_updates_both_product_pins(
    tmp_path, candidate
):
    # Given a frozen local-origin baseline and an independent destination.
    baseline = tmp_path / "baseline"
    dependencies = baseline / "dependencies"
    dependencies.mkdir(parents=True)
    packages = baseline / "lane-0/project/Packages"
    packages.mkdir(parents=True)
    output = tmp_path / "registry"
    output.mkdir()
    original_product = "file:/recorded/MCPForUnity"
    pinned = {
        lane.PRODUCT_PACKAGE: original_product,
        "com.unity.cinemachine": "file:/cached/cinemachine.tgz",
        "com.unity.inputsystem": "file:/cached/inputsystem",
        "com.unity.modules.terrain": "1.0.0",
    }
    lock = {
        name: {
            "version": version,
            "depth": 0,
            "source": "local" if version.startswith("file:") else "builtin",
            "dependencies": {},
        }
        for name, version in pinned.items()
    }
    manifest_baseline = dependencies / "manifest.json"
    lock_baseline = packages / "packages-lock.json"
    lane.dump(manifest_baseline, {"dependencies": pinned})
    lane.dump(lock_baseline, {"dependencies": lock})
    old_manifest, old_lock = manifest_baseline.read_bytes(), lock_baseline.read_bytes()

    # When the registry lane is provisioned from that baseline.
    registry.configure_registry(baseline, output, candidate)

    # Then only the registered product pin and the two recorded Unity origins are selected.
    observed = json.loads((output / "manifest.json").read_bytes())["dependencies"]
    resolved = json.loads((output / "packages-lock.json").read_bytes())["dependencies"]
    assert observed == {
        lane.PRODUCT_PACKAGE: candidate or original_product,
        "com.unity.cinemachine": "2.6.11",
        "com.unity.inputsystem": "1.14.2",
        "com.unity.modules.terrain": "1.0.0",
    }
    assert resolved == {
        lane.PRODUCT_PACKAGE: lock[lane.PRODUCT_PACKAGE]
        | {"version": candidate or original_product},
        "com.unity.cinemachine": lock["com.unity.cinemachine"]
        | {"version": "2.6.11", "source": "registry", "url": "https://packages.unity.com"},
        "com.unity.inputsystem": lock["com.unity.inputsystem"]
        | {"version": "1.14.2", "source": "registry", "url": "https://packages.unity.com"},
        "com.unity.modules.terrain": lock["com.unity.modules.terrain"],
    }
    assert manifest_baseline.read_bytes() == old_manifest
    assert lock_baseline.read_bytes() == old_lock
