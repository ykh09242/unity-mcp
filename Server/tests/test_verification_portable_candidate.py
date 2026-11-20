"""A second checkout verifies a pinned proof and independently admits its local Player."""

from hashlib import sha256
import json
import os
import shutil

from click.testing import CliRunner
import pytest

from cli.main import cli
from services.verification import portable_candidate
from services.verification.player_candidate import prepare_candidate
from .test_play_scenario_player import bundle
from .test_play_scenario_player_assurance import verified_bundle
from .test_verification_player_candidates import registered
from .test_verification_git_draft import git


def invoke(action, project, *options):
    return CliRunner().invoke(
        cli,
        [
            "--no-project-context",
            "verification-candidate",
            action,
            "--project",
            str(project),
            *map(str, options),
        ],
    )


@pytest.fixture(params=[1, 2], ids=["inline", "chunked"])
def transferred(registered, tmp_path, monkeypatch, request):
    original, _, register = registered
    manifest = register("original")
    source_dir = tmp_path / "registrations" / "original"
    if request.param == 2:
        chunk = json.dumps({"inputs": manifest.pop("inputs")}).encode()
        digest = sha256(chunk).hexdigest()
        (source_dir / "input-chunks").mkdir()
        (source_dir / "input-chunks" / (digest + ".json")).write_bytes(chunk)
        manifest.update(
            schema_version=2,
            input_chunks=[{"sha256": digest, "count": 1, "size_bytes": len(chunk)}],
        )
        (source_dir / "candidate.json").write_text(json.dumps(manifest), encoding="utf-8")
    git(original, "init")
    git(original, "add", "Assets")
    git(original, "commit", "-m", "source")
    other = tmp_path / "another checkout"
    git(tmp_path, "clone", "--local", "--", str(original), str(other))
    proof = tmp_path / "exported" / "proof.json"
    exported = invoke("export", original, "--candidate-id", "original", "--output", proof)
    assert exported.exit_code == 0, exported.output
    assert str(original) not in proof.read_text(encoding="utf-8")
    assert "project" not in json.loads(proof.read_bytes())["registration"]
    pin = json.loads(exported.output)["proof_sha256"]
    assert pin == sha256(proof.read_bytes()).hexdigest()
    delivered = tmp_path / "delivered"
    shutil.copytree(proof.parent, delivered)
    root = tmp_path / "operator"
    monkeypatch.setenv("UNITY_MCP_VERIFICATION_ROOT", str(root))
    return other, source_dir, root, ["--proof", delivered / "proof.json", "--sha256", pin]


def test_other_checkout_requires_explicit_local_admission(transferred):
    project, source_dir, root, options = transferred
    receipt = invoke("verify", project, *options)
    assert receipt.exit_code == 0, receipt.output
    assert json.loads(receipt.output)["registered"] is False
    assert not root.exists()
    assert invoke("register", project, *options, "--candidate-id", "copy").exit_code != 0
    assert not root.exists()

    directory = root / "copy"
    shutil.copytree(source_dir / "build", directory / "build")
    admitted = invoke("register", project, *options, "--candidate-id", "copy")
    assert admitted.exit_code == 0, admitted.output
    assert json.loads(admitted.output)["registered"] is True
    selection = prepare_candidate({"projectRoot": str(project)}, {"candidate_id": "copy"})
    assert selection.candidate.project == str(project)
    assert selection.live == selection.candidate.inputs
    before = (directory / "candidate.json").read_bytes()
    assert invoke("register", project, *options, "--candidate-id", "copy").exit_code != 0
    assert (directory / "candidate.json").read_bytes() == before
    assert not (directory / "verification-runs").exists()


def test_changed_source_names_input_and_proof_requires_expected_digest(transferred):
    project, _, root, options = transferred
    (project / "Assets" / "Wind.cs").write_bytes(b"class Changed {}")
    result = invoke("verify", project, *options)
    assert (
        result.exit_code != 0
        and "candidate_source_digest_mismatch: Assets/Wind.cs" in result.output
    )
    options[1].write_bytes(options[1].read_bytes() + b" ")
    result = invoke("register", project, *options, "--candidate-id", "copy")
    assert result.exit_code != 0 and "candidate_proof_digest_mismatch" in result.output
    assert not root.exists()


@pytest.mark.parametrize("changed", ["manifest", "executable"])
def test_corrupt_operator_provisioned_build_blocks_registration(transferred, changed):
    project, source_dir, root, options = transferred
    build = root / "copy" / "build"
    shutil.copytree(source_dir / "build", build)
    leaf = "scenario-bundle.json" if changed == "manifest" else "MCPScenarioPlayer.exe"
    (build / leaf).write_bytes((build / leaf).read_bytes() + b" ")
    result = invoke("register", project, *options, "--candidate-id", "copy")
    assert result.exit_code != 0
    assert not (build.parent / "candidate.json").exists()
    assert not (build.parent / "verification-runs").exists()


def test_pinned_proof_cannot_supply_an_executable_location(transferred):
    project, source_dir, root, options = transferred
    proof = options[1]
    document = json.loads(proof.read_bytes())
    document["registration"]["build_directory"] = str(source_dir / "build")
    proof.write_bytes(json.dumps(document).encode())
    options[-1] = sha256(proof.read_bytes()).hexdigest()
    result = invoke("register", project, *options, "--candidate-id", "copy")
    assert result.exit_code != 0 and "build_directory" in result.output
    assert not root.exists()


@pytest.mark.parametrize("change", ["replace-executable", "add-file"])
def test_payload_change_after_hashing_blocks_publication_and_permits_retry(
    transferred, monkeypatch, change
):
    project, source_dir, root, options = transferred
    build = root / "copy" / "build"
    shutil.copytree(source_dir / "build", build)
    (build / "Data").mkdir()
    executable = build / "MCPScenarioPlayer.exe"
    extra = build / "Data" / "late.bin"
    original = executable.read_bytes()
    publish = portable_candidate._publish
    changed = []

    def change_before_link(path, content, **kwargs):
        if path == build.parent / "candidate.json":
            changed.append(change)
            if change == "replace-executable":
                stamp = executable.stat()
                replacement = build.parent / "replacement.bin"
                replacement.write_bytes(b"!" * len(original))
                os.utime(replacement, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
                replacement.replace(executable)
            else:
                extra.write_bytes(b"new file after payload verification")
        return publish(path, content, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(portable_candidate, "_publish", change_before_link)
        result = invoke("register", project, *options, "--candidate-id", "copy")
    assert changed == [change]
    assert result.exit_code != 0 and "candidate_payload_changed_during_admission" in result.output
    assert not (build.parent / "candidate.json").exists()
    if change == "replace-executable":
        executable.write_bytes(original)
    else:
        extra.unlink()
    retry = invoke("register", project, *options, "--candidate-id", "copy")
    assert retry.exit_code == 0, retry.output
    assert json.loads(retry.output)["registered"] is True
