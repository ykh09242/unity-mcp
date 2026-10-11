"""Real filesystem/Git boundaries for the standalone lifecycle sample generator."""

import json
from pathlib import Path
import subprocess

import pytest

from create_play_scenario_lifecycle_sample import create_sample

PACKAGES = {
    "com.unity.editorcoroutines": "1.1.0",
    "com.unity.ext.nunit": "2.0.5",
    "com.unity.nuget.newtonsoft-json": "3.2.2",
    "com.unity.test-framework": "1.6.0",
    "com.unity.ugui": "2.0.0",
}


def git(repo, *args):
    return subprocess.check_output(["git", "-C", str(repo), *args], text=True).strip()


@pytest.fixture
def repository(tmp_path):
    repo = tmp_path / "repository"
    package = repo / "MCPForUnity"
    template = repo / "tools/fixtures/play_scenario_lifecycle"
    package.mkdir(parents=True)
    (template / "Packages").mkdir(parents=True)
    (template / "ProjectSettings").mkdir()
    (template / "Assets").mkdir()
    (package / "package.json").write_text(
        json.dumps({"name": "com.ykh09242.unity-mcp", "version": "1.2.1"})
    )
    (package / "Owner.cs").write_text("public class Owner {}")
    (package / ".gitignore").write_text(".env\nLibrary/\n")
    (template / "Assets/Sample.cs").write_text("public class Sample {}")
    (template / "Packages/manifest.json").write_text(json.dumps({"dependencies": PACKAGES}))
    (template / "ProjectSettings/ProjectVersion.txt").write_text("m_EditorVersion: 6000.0.69f1\n")
    git(repo.parent, "init", str(repo))
    git(repo, "add", ".")
    git(
        repo,
        "-c",
        "user.name=Sample Test",
        "-c",
        "user.email=sample-test@example.invalid",
        "commit",
        "-m",
        "fixture",
    )
    return repo


def test_creates_independent_project_and_only_tracked_clean_package(repository, tmp_path):
    (repository / "MCPForUnity/.env").write_text("fake excluded fixture value")
    (repository / "MCPForUnity/Library").mkdir()
    (repository / "MCPForUnity/Library/cache.bin").write_bytes(b"ignored cache")
    output = tmp_path / "new-sample"
    project = create_sample(output, repository=repository)
    assert (project / "Assets/Sample.cs").read_text() == "public class Sample {}"
    assert (output / "package/Owner.cs").read_text() == "public class Owner {}"
    assert not (output / "package/.env").exists()
    assert not (output / "package/Library").exists()
    dependencies = json.loads((project / "Packages/manifest.json").read_text())["dependencies"]
    assert dependencies["com.ykh09242.unity-mcp"] == "file:../../package"
    provenance = json.loads((output / "sample-provenance.json").read_text())
    assert provenance["source_revision"] == git(repository, "rev-parse", "HEAD")
    assert "Owner.cs" in provenance["package_sha256"]
    assert provenance["project_sha256"]["Assets/Sample.cs"]
    assert git(repository, "diff") == ""


def test_existing_destination_is_preserved(repository, tmp_path):
    output = tmp_path / "existing"
    output.mkdir()
    (output / "keep.txt").write_text("user data")
    with pytest.raises(FileExistsError):
        create_sample(output, repository=repository)
    assert (output / "keep.txt").read_text() == "user data"


def test_refuses_output_inside_source_package(repository):
    output = repository / "MCPForUnity/new-sample"
    with pytest.raises(ValueError, match="source"):
        create_sample(output, repository=repository)
    assert not output.exists()


@pytest.mark.parametrize("staged", [False, True])
def test_dirty_product_source_is_not_labeled_as_commit(repository, tmp_path, staged):
    (repository / "MCPForUnity/Owner.cs").write_text("changed source")
    if staged:
        git(repository, "add", "MCPForUnity/Owner.cs")
    output = tmp_path / "new-sample"
    with pytest.raises(ValueError, match="clean"):
        create_sample(output, repository=repository)
    assert not output.exists()


def test_local_cache_is_explicit_and_has_matching_package_identity(repository, tmp_path):
    cache = tmp_path / "packages"
    for name, version in PACKAGES.items():
        folder = cache / name
        folder.mkdir(parents=True)
        (folder / "package.json").write_text(json.dumps({"name": name, "version": version}))
    project = create_sample(tmp_path / "cached-sample", cache, repository=repository)
    dependencies = json.loads((project / "Packages/manifest.json").read_text())["dependencies"]
    for name in PACKAGES:
        assert dependencies[name] == "file:" + (cache / name).resolve().as_posix()


def test_incomplete_cache_fails_before_creating_destination(repository, tmp_path):
    output = tmp_path / "new-sample"
    with pytest.raises(ValueError, match="cache"):
        create_sample(output, tmp_path / "absent", repository=repository)
    assert not output.exists()
