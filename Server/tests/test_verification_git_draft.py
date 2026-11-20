"""Exercise Git candidate selection through the installed CLI command group."""

from hashlib import sha256
import json
import subprocess

from click.testing import CliRunner
import pytest

from cli.main import cli


def git(root, *args):
    return subprocess.run(
        ["git", "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", *args],
        cwd=root,
        check=True,
        capture_output=True,
    ).stdout


@pytest.fixture
def checkout(tmp_path):
    git(tmp_path, "init")
    git(tmp_path, "config", "core.autocrlf", "false")
    project = tmp_path / "Project"
    assets = project / "Assets"
    assets.mkdir(parents=True)
    for index, name in enumerate(("Mover", "Replaced", "Deleted", "Tracked"), start=1):
        (assets / (name + ".asset")).write_bytes(b"original")
        (assets / (name + ".asset.meta")).write_bytes(f"guid: {index:032x}\n".encode())
    (project / ".gitignore").write_bytes(b"Ignored.asset\n")
    (tmp_path / "Assets").mkdir()
    (tmp_path / "Assets" / "Outside.asset").write_bytes(b"outside selected project")
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-m", "baseline")
    return project


def draft(project, selection, *extra):
    return CliRunner().invoke(
        cli,
        [
            "--no-project-context",
            "verification-candidate",
            "draft",
            "--project",
            str(project),
            "--baseline",
            "HEAD",
            "--selection",
            selection,
            *extra,
        ],
    )


def test_retained_guid_replacement_deletion_and_untracked_inputs(checkout):
    assets = checkout / "Assets"
    for ext in (".asset", ".asset.meta"):
        (assets / ("Mover" + ext)).rename(assets / ("New [Name]" + ext))
        (assets / ("Deleted" + ext)).unlink()
    (assets / "Replaced.asset.meta").write_bytes(b"guid: " + b"a" * 32 + b"\n")
    git(checkout, "add", "Assets")
    (assets / "Only Working.asset").write_bytes(b"untracked fixture")
    (assets / "Ignored.asset").write_bytes(b"ignored")
    (assets / "README.md").write_bytes(b"unsupported")
    (checkout.parent / "Assets" / "Outside.asset").write_bytes(b"also changed")

    for selection in ("staged", "working-tree"):
        result = draft(checkout, selection)
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["asset_moves"] == [
            {
                "guid": f"{1:032x}",
                "from": "Assets/Mover.asset",
                "to": "Assets/New [Name].asset",
            }
        ]
        assert data["asset_replacements"] == [
            {
                "path": "Assets/Replaced.asset",
                "old_guid": f"{2:032x}",
                "new_guid": "a" * 32,
            }
        ]
        changes = {row["path"]: row["kind"] for row in data["changed_paths"]}
        assert data["asset_deletions"] == [{"path": "Assets/Deleted.asset", "guid": f"{3:032x}"}]
        assert changes["Assets/Deleted.asset"] == "deleted"
        assert ("Assets/Only Working.asset" in changes) == (selection == "working-tree")
        assert not (
            {"Assets/Ignored.asset", "Assets/README.md", "Assets/Outside.asset"} & changes.keys()
        )
        assert "Assets/Tracked.asset" not in data["candidate"]["inputs"]
        assert (
            data["candidate"]["inputs"]["Assets/Replaced.asset"] == sha256(b"original").hexdigest()
        )
        assert data["baseline_commit"] == git(checkout, "rev-parse", "HEAD").decode().strip()
        assert str(checkout) not in result.output


def test_staged_bytes_survive_working_revert_without_writing_index(checkout):
    path = checkout / "Assets" / "Tracked.asset"
    path.write_bytes(b"staged")
    git(checkout, "add", "Assets/Tracked.asset")
    staged_entry = git(checkout, "ls-files", "--stage", "--", "Assets/Tracked.asset")
    path.write_bytes(b"original")
    staged = draft(checkout, "staged")
    working = draft(checkout, "working-tree")
    assert staged.exit_code == working.exit_code == 0
    assert (
        json.loads(staged.output)["candidate"]["inputs"]["Assets/Tracked.asset"]
        == sha256(b"staged").hexdigest()
    )
    assert json.loads(working.output)["changed_paths"] == []
    assert git(checkout, "ls-files", "--stage", "--", "Assets/Tracked.asset") == staged_entry


def test_draft_refuses_linked_source_and_never_overwrites_output(checkout):
    target = checkout / "Assets" / "Target.asset"
    target.write_bytes(b"review")
    output = checkout.parent / "review.json"
    assert draft(checkout, "working-tree", "--output", str(output)).exit_code == 0
    retained = output.read_bytes()
    assert draft(checkout, "working-tree", "--output", str(output)).exit_code != 0
    assert output.read_bytes() == retained
    try:
        (checkout / "Assets" / "Link.asset").symlink_to(target)
    except OSError:
        pytest.skip("symlink creation unavailable")
    assert draft(checkout, "working-tree").exit_code != 0
