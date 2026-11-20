"""Exercise Git draft limits, transfer, and immutable commit comparison through the CLI."""

from hashlib import sha256
import json
import subprocess

from click.testing import CliRunner
import pytest

from cli.main import cli
from services.verification.git_draft_artifacts import pack_inputs
from services.verification.input_chunks import CHUNK_BYTES, CHUNK_PATHS
from services.verification.player_candidate import read_manifest
from .test_verification_git_draft import checkout as checkout
from .test_verification_git_draft import draft, git


@pytest.mark.parametrize("count", [128, 129, 1024, 1025])
@pytest.mark.parametrize("selection", ["staged", "working-tree"])
def test_git_draft_path_boundaries(tmp_path, monkeypatch, count, selection):
    project = tmp_path / "project"
    assets = project / "Assets"
    assets.mkdir(parents=True)
    git(project, "init")
    git(project, "config", "core.autocrlf", "false")
    expected = {}
    prefix = "long-name-" * 11 if count >= 1024 else "item-"
    for number in range((count + 1) // 2):
        source = assets / f"{prefix}{number:04}.asset"
        source.write_bytes(b"before")
        expected[source.relative_to(project).as_posix()] = b"before"
        if 2 * number + 1 < count:
            metadata = source.with_suffix(".asset.meta")
            content = f"guid: {number + 1:032x}\n".encode()
            metadata.write_bytes(content)
            expected[metadata.relative_to(project).as_posix()] = content
    git(project, "add", "Assets")
    git(project, "commit", "-m", "initial sources")
    for source in assets.glob("*.asset"):
        source.write_bytes(b"after")
    if selection == "staged":
        git(project, "add", "Assets")
        for source in assets.glob("*.asset"):
            source.write_bytes(b"unrelated working content")

    review = tmp_path / "review" / "draft.json"
    result = draft(project, selection, "--output", str(review))
    if count > 1024:
        assert result.exit_code != 0
        assert "1024" in result.output and "Split" in result.output
        assert not review.parent.exists()
        return
    assert result.exit_code == 0, result.output
    document = json.loads(review.read_bytes())
    assert document["schema_version"] == (1 if count == 128 else 2)
    assert {row["kind"] for row in document["changed_paths"]} == {"modified"}
    assert len(document["changed_paths"]) == (count + 1) // 2

    registrations = tmp_path / "registrations"
    monkeypatch.setenv("UNITY_MCP_VERIFICATION_ROOT", str(registrations))
    for side in ("baseline", "candidate"):
        hashes = {
            path: sha256(
                b"after" if side == "candidate" and path.endswith(".asset") else content
            ).hexdigest()
            for path, content in expected.items()
        }
        expected_revision = sha256(
            "".join(f"{path}\n{hashes[path]}\n" for path in sorted(hashes)).encode()
        ).hexdigest()
        assert document[side]["source_revision"] == expected_revision
        if count == 128:
            assert document[side]["inputs"] == hashes
            assert not (review.parent / "input-chunks").exists()
            continue

        chunks = document[side]["input_chunks"]
        full, remainder = divmod(count, 128)
        assert [row["count"] for row in chunks] == [128] * full + ([remainder] if remainder else [])
        assert len(document[side]["assets"]) == count // 2
        arguments = [
            "--no-project-context",
            "verification-candidate",
            "draft-inputs",
            "--draft",
            str(review),
            "--side",
            side,
            "--candidate-id",
            side,
        ]
        imported = CliRunner().invoke(cli, arguments)
        assert imported.exit_code == 0, imported.output
        assert CliRunner().invoke(cli, arguments).output == imported.output
        fields = json.loads(imported.output)
        assert fields["schema_version"] == 2
        assert fields["input_chunks"] == chunks
        assert fields["assets"] == document[side]["assets"]
        entry = registrations / side / "candidate.json"
        entry.write_text(
            json.dumps(
                {
                    "project": str(project),
                    "scenario": "example",
                    "build_id": "1" * 32,
                    "payload_hash": "2" * 64,
                    **fields,
                }
            ),
            encoding="utf-8",
        )
        if count == 1024:
            assert entry.stat().st_size > CHUNK_BYTES
        manifest, _ = read_manifest(registrations, side, project)
        assert manifest.inputs == hashes


def test_128_long_git_paths_switch_to_chunks_for_registration(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    git(project, "init")
    git(project, "commit", "--allow-empty", "-m", "empty baseline")
    source = b"staged source"
    (project / "blob-source").write_bytes(source)
    files = ["blob-source"]
    assets = {}
    inputs = {}
    for number in range(64):
        path = f"Assets/{('a' * 84 + '/') * 4}Item{number:02}.asset"
        guid = f"{number + 1:032x}"
        metadata = f"guid: {guid}\n".encode()
        name = f"blob-meta-{number:02}"
        (project / name).write_bytes(metadata)
        files.append(name)
        assets[path] = guid
        inputs[path] = sha256(source).hexdigest()
        inputs[path + ".meta"] = sha256(metadata).hexdigest()
    source_oid, *metadata_oids = (
        git(project, "hash-object", "-w", "--", *files).decode().splitlines()
    )
    # Populate the index without requiring the host to materialize long paths.
    entries = "".join(
        f"100644 {source_oid}\t{path}\n100644 {meta_oid}\t{path}.meta\n"
        for path, meta_oid in zip(assets, metadata_oids, strict=True)
    )
    subprocess.run(
        ["git", "update-index", "--index-info"], cwd=project, input=entries.encode(), check=True
    )
    assert len(inputs) == CHUNK_PATHS
    assert len(json.dumps({"inputs": inputs, "assets": assets}).encode()) > CHUNK_BYTES

    review = tmp_path / "draft.json"
    result = draft(project, "staged", "--output", str(review))
    assert result.exit_code == 0, result.output
    document = json.loads(review.read_bytes())
    assert document["schema_version"] == 2
    assert document["baseline"]["inputs"] == {}
    assert document["candidate"]["assets"] == assets
    assert [chunk["count"] for chunk in document["candidate"]["input_chunks"]] == [128]

    root = tmp_path / "registrations"
    monkeypatch.setenv("UNITY_MCP_VERIFICATION_ROOT", str(root))
    imported = CliRunner().invoke(
        cli,
        [
            "--no-project-context",
            "verification-candidate",
            "draft-inputs",
            "--draft",
            str(review),
            "--side",
            "candidate",
            "--candidate-id",
            "long-paths",
        ],
    )
    assert imported.exit_code == 0, imported.output
    entry = root / "long-paths" / "candidate.json"
    entry.write_text(
        json.dumps(
            {
                "project": str(project),
                "scenario": "example",
                "build_id": "1" * 32,
                "payload_hash": "2" * 64,
                **json.loads(imported.output),
            }
        ),
        encoding="utf-8",
    )
    manifest, _ = read_manifest(root, "long-paths", project)
    assert manifest.inputs == inputs
    assert manifest.assets == assets


def test_draft_from_stdout_with_separate_chunks_rejects_tampering_before_transfer(
    tmp_path, monkeypatch
):
    project = tmp_path / "project"
    assets = project / "Assets"
    assets.mkdir(parents=True)
    git(project, "init")
    git(project, "commit", "--allow-empty", "-m", "empty baseline")
    for number in range(129):
        (assets / f"Added{number:03}.cs").write_bytes(b"new source")
    missing_destination = draft(project, "working-tree")
    assert missing_destination.exit_code != 0
    assert "--output" in missing_destination.output

    chunk_directory = tmp_path / "evidence"
    result = draft(project, "working-tree", "--chunk-directory", str(chunk_directory))
    assert result.exit_code == 0, result.output
    review = tmp_path / "review.json"
    review.write_text(result.output, encoding="utf-8")
    document = json.loads(result.output)
    assert document["baseline"]["inputs"] == {}
    assert len(document["changed_paths"]) == 129
    assert all(row["kind"] == "added" for row in document["changed_paths"])

    root = tmp_path / "registrations"
    monkeypatch.setenv("UNITY_MCP_VERIFICATION_ROOT", str(root))
    runner = CliRunner()
    group = ["--no-project-context", "verification-candidate"]
    arguments = group + [
        "draft-inputs",
        "--draft",
        str(review),
        "--side",
        "candidate",
        "--candidate-id",
        "added",
        "--chunk-directory",
        str(chunk_directory),
    ]
    digests = [row["sha256"] for row in document["candidate"]["input_chunks"]]
    last_chunk = chunk_directory / "input-chunks" / (digests[-1] + ".json")
    retained = last_chunk.read_bytes()
    last_chunk.write_bytes(b"{}")
    tampered = runner.invoke(cli, arguments)
    assert tampered.exit_code != 0
    assert "candidate_draft_chunk_inventory_incomplete" in tampered.output
    assert not root.exists()

    last_chunk.write_bytes(retained)
    imported = runner.invoke(cli, arguments)
    assert imported.exit_code == 0, imported.output
    index = runner.invoke(
        cli,
        group
        + ["input-chunks", "index", "--candidate-id", "added"]
        + [item for digest in digests for item in ("--chunk", digest)],
    )
    assert index.exit_code == 0, index.output
    assert json.loads(index.output)["source_revision"] == document["candidate"]["source_revision"]


def test_chunk_byte_budget_counts_long_utf8_paths():
    folder = ("\N{SNOWMAN}" * 124 + "/") * 3
    inputs = {f"Assets/{folder}File{number:03}.asset": "a" * 64 for number in range(129)}
    chunks = pack_inputs(inputs)
    assert len(chunks) > 2
    recovered = {}
    for ref, content in chunks:
        assert ref.size_bytes == len(content) <= CHUNK_BYTES
        assert ref.count <= CHUNK_PATHS
        assert ref.sha256 == sha256(content).hexdigest()
        recovered.update(json.loads(content)["inputs"])
    assert list(recovered) == sorted(inputs)
    assert recovered == inputs


def test_committed_draft_resolves_diverged_refs_and_ignores_checkout(checkout):
    branch_point = git(checkout, "rev-parse", "HEAD").decode().strip()
    git(checkout, "switch", "-c", "review-base")
    assets = checkout / "Assets"
    (assets / "BaseOnly.asset").write_bytes(b"base branch")
    git(checkout, "add", "Assets")
    git(checkout, "commit", "-m", "base branch commit")
    base_commit = git(checkout, "rev-parse", "HEAD").decode().strip()
    git(checkout, "switch", "-c", "review-head", branch_point)
    (assets / "Mover.asset").rename(assets / "Moved.asset")
    (assets / "Mover.asset.meta").rename(assets / "Moved.asset.meta")
    (assets / "Deleted.asset").unlink()
    (assets / "Deleted.asset.meta").unlink()
    (assets / "Tracked.asset").write_bytes(b"head blob")
    git(checkout, "add", "Assets")
    git(checkout, "commit", "-m", "head branch commit")
    candidate_commit = git(checkout, "rev-parse", "HEAD").decode().strip()
    git(checkout, "switch", "review-base")
    (assets / "Tracked.asset").write_bytes(b"distracting index")
    git(checkout, "add", "Assets")
    (assets / "Tracked.asset").write_bytes(b"distracting worktree")
    (assets / "Untracked.asset").write_bytes(b"untracked")
    before = git(checkout, "status", "--porcelain", "-z")

    result = draft(
        checkout, "committed", "--baseline", "review-base", "--candidate-ref", "review-head"
    )
    assert result.exit_code == 0, result.output
    document = json.loads(result.output)
    assert document["base_commit"] == base_commit
    assert document["baseline_commit"] == branch_point
    assert document["candidate_commit"] == candidate_commit
    assert (
        document["candidate"]["inputs"]["Assets/Tracked.asset"] == sha256(b"head blob").hexdigest()
    )
    assert document["asset_moves"] == [
        {"guid": f"{1:032x}", "from": "Assets/Mover.asset", "to": "Assets/Moved.asset"}
    ]
    assert document["asset_deletions"] == [{"path": "Assets/Deleted.asset", "guid": f"{3:032x}"}]
    assert {row["path"]: row["kind"] for row in document["changed_paths"]} == {
        "Assets/Deleted.asset": "deleted",
        "Assets/Deleted.asset.meta": "deleted",
        "Assets/Moved.asset": "added",
        "Assets/Moved.asset.meta": "added",
        "Assets/Mover.asset": "deleted",
        "Assets/Mover.asset.meta": "deleted",
        "Assets/Tracked.asset": "modified",
    }
    pinned = draft(
        checkout, "committed", "--baseline", base_commit, "--candidate-ref", candidate_commit
    )
    assert pinned.output == result.output
    assert git(checkout, "status", "--porcelain", "-z") == before


def test_committed_draft_rejects_multiple_merge_bases_and_unrelated_tips(checkout):
    root = git(checkout, "rev-parse", "HEAD").decode().strip()
    tree = git(checkout, "rev-parse", "HEAD^{tree}").decode().strip()

    def commit(message, *parents):
        parent_options = [item for parent in parents for item in ("-p", parent)]
        return git(checkout, "commit-tree", tree, *parent_options, "-m", message).decode().strip()

    first, second = commit("first", root), commit("second", root)
    left, right = commit("left", first, second), commit("right", second, first)
    ambiguous = draft(checkout, "committed", "--baseline", left, "--candidate-ref", right)
    assert ambiguous.exit_code != 0
    assert "candidate_git_multiple_merge_bases" in ambiguous.output

    unrelated = draft(
        checkout, "committed", "--baseline", root, "--candidate-ref", commit("unrelated")
    )
    assert unrelated.exit_code != 0
    assert "candidate_git_no_merge_base" in unrelated.output


def test_committed_draft_rejects_shallow_comparison(checkout):
    local_clone = checkout.parent / "shallow"
    git(checkout, "clone", "--depth=1", checkout.parent.as_uri(), str(local_clone))
    result = draft(local_clone / "Project", "committed", "--candidate-ref", "HEAD")
    assert result.exit_code != 0
    assert "candidate_git_shallow_history" in result.output
