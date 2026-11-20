"""Exercise build capture, assembly and rejection before launching any binary."""

from hashlib import sha256
import json
import os
from pathlib import Path
import runpy

import pytest

from services.verification import reproduce
from .test_verification_reproduce import (
    bundle,
    git,
    invoke,
    registered,
    review,
    verified_bundle,
    write,
)


def native_stamp(project, package, source, row):
    from services.verification.pair_transfer import package_checkout_digest

    bundle_path = source / "build/scenario-bundle.json"
    frozen = json.loads(bundle_path.read_bytes())
    receipt = {
        "schema_version": 1,
        "kind": "verification_player_build_source",
        "source_revision": row["source_revision"],
        "build_id": row["build_id"],
        "project_commit": git(project, "rev-parse", "HEAD").decode().strip(),
        "package_commit": git(package, "rev-parse", "HEAD").decode().strip(),
        "package_tree": git(package, "rev-parse", "HEAD:MCPForUnity").decode().strip(),
        "package_checkout_sha256": package_checkout_digest(
            package, row["package_commit"], reproduce.SourceSample()
        ),
    }
    file = source / "build/build-source.json"
    digest = write(file, receipt)
    frozen["payload_inventory"].append(
        {"path": "build-source.json", "size_bytes": file.stat().st_size, "sha256": digest}
    )
    encoded = json.dumps(frozen["payload_inventory"], sort_keys=True, separators=(",", ":"))
    frozen.update(payload_inventory_json=encoded, payload_hash=sha256(encoded.encode()).hexdigest())
    write(bundle_path, frozen)
    registration_path = source / "candidate.json"
    registration = json.loads(registration_path.read_bytes())
    registration["payload_hash"] = frozen["payload_hash"]
    write(registration_path, registration)


@pytest.mark.parametrize(
    "fault",
    [
        None,
        "wrong-closure",
        "mixed-capture",
        "tampered-build",
        "untracked-transfer",
        "relabelled-package",
        "ignored-package-input",
        "hidden-package-edit",
    ],
)
def test_capture_and_pack_real_files_in_fresh_directories(review, tmp_path, monkeypatch, fault):
    project, package, registry, pair = review
    monkeypatch.setattr(reproduce, "run_player", lambda options: pytest.fail("unexpected launch"))
    destination = tmp_path / "transfer"
    for name in reproduce.SIDES:
        git(project, "checkout", "-q", "--detach", pair[name]["project_commit"])
        native_stamp(project, package, registry / name, pair[name])
        if name == "candidate" and fault == "relabelled-package":
            (package / "MCPForUnity/Different.cs").write_bytes(b"class Different {}")
            git(package, "add", "--", "MCPForUnity")
            git(package, "commit", "-m", "unrelated native package")
        if name == "candidate" and fault == "ignored-package-input":
            (package / ".git/info/exclude").write_bytes(b"*.ignored.cs\n")
            (package / "MCPForUnity/Other.ignored.cs").write_bytes(b"class Other {}")
        if name == "candidate" and fault == "hidden-package-edit":
            git(package, "update-index", "--assume-unchanged", "MCPForUnity/package.json")
            (package / "MCPForUnity/package.json").write_bytes(b'{"version":"different"}')
        closure = registry / name / "closure.json"
        if name == "candidate" and fault == "wrong-closure":
            closure = registry / "baseline/closure.json"
        captured = invoke(
            "capture-pair-side",
            project,
            "--package-checkout",
            package,
            "--candidate-id",
            name,
            "--closure",
            closure,
            "--output",
            destination / name,
        )
        if name == "candidate" and fault == "wrong-closure":
            assert (
                captured.exit_code
                and "capture_dependency_closure_commit_mismatch" in captured.output
            )
            assert not (destination / name / "capture.json").exists()
            return
        if name == "candidate" and fault == "relabelled-package":
            assert captured.exit_code and "native_build_source_mismatch" in captured.output
            assert not (destination / name / "capture.json").exists()
            return
        if name == "candidate" and fault in {"ignored-package-input", "hidden-package-edit"}:
            reason = (
                "untracked_package_input"
                if fault == "ignored-package-input"
                else "native_package_checkout_bytes_mismatch"
            )
            assert captured.exit_code and reason in captured.output
            assert not (destination / name / "capture.json").exists()
            return
        assert captured.exit_code == 0, captured.output
        assert json.loads(captured.output)["project_commit"] == pair[name]["project_commit"]
    if fault == "mixed-capture":
        target = destination / "candidate/capture.json"
        target.write_bytes((destination / "baseline/capture.json").read_bytes())
    if fault == "tampered-build":
        (destination / "baseline/build/MCPScenarioPlayer.exe").write_bytes(b"unexpected data")
    if fault == "untracked-transfer":
        (destination / "candidate/unrelated.txt").write_bytes(b"unrelated to build")
    expected = tmp_path / "expectations.json"
    maintained = json.loads(
        (
            Path(__file__).resolve().parents[2]
            / "TestProjects/UnityMCPTests/Verification/prefab-pair-expectations.json"
        ).read_bytes()
    )
    write(
        expected,
        {s: maintained[s] for s in reproduce.SIDES},
    )
    result = invoke("pack-pair", project, "--artifacts", destination, "--expectations", expected)
    if fault:
        assert result.exit_code != 0 and not (destination / "pair.json").exists()
    else:
        assert result.exit_code == 0, result.output
        published = json.loads((destination / "pair.json").read_bytes())
        assert published["baseline"]["state"]["expected"]["loaded"] == ""
        for name in reproduce.SIDES:
            assert published[name]["build_id"] == pair[name]["build_id"]
            assert published[name]["package_commit"] == pair[name]["package_commit"]
            assert published[name]["closure_sha256"] == pair[name]["closure_sha256"]
            assert len(published[name]["capture_sha256"]) == 64
        assert (
            invoke(
                "pack-pair", project, "--artifacts", destination, "--expectations", expected
            ).exit_code
            != 0
        )


def test_serialized_reference_requires_committed_nonzero_reference_to_target(review):
    project, _, _, _ = review
    guid = "0" * 31 + "4"
    source, target = "Assets/Code.unity", "Assets/WindCurve.asset"
    ref = reproduce.SerializedReference(source=source, target=target, field="Curve")
    data = (
        b"%YAML 1.1\n--- !u!114 &7\nMonoBehaviour:\n  Curve: {fileID: 72, guid: "
        + guid.encode()
        + b", type: 3}\n"
    )
    for expected, body in (
        (True, data),
        (False, data.replace(b"72", b"0")),
        (False, data.replace(guid.encode(), b"9" * 32)),
    ):
        (project / source).write_bytes(body)
        git(project, "add", source)
        git(project, "commit", "-m", "serialized field")
        repo = reproduce._Git(project)
        blobs = reproduce._blob_ids(repo, reproduce._head(repo), [source])
        if expected:
            rows = reproduce._serialized_references(repo, blobs, {target: guid}, [ref])
            assert rows[0]["guid"] == guid and rows[0]["file_id"] == "72"
        else:
            with pytest.raises(ValueError, match="serialized_reference_mismatch"):
                reproduce._serialized_references(repo, blobs, {target: guid}, [ref])


def test_admitted_source_bundle_materializes_distinct_commits_and_rejects_mismatch(
    review, tmp_path
):
    from services.verification.pair_project import bundle_digest, materialize

    project, _, _, pair = review
    commits = [pair[name]["project_commit"] for name in reproduce.SIDES]
    for name, commit in zip(reproduce.SIDES, commits, strict=True):
        git(project, "branch", name, commit)
    archive = tmp_path / "project.bundle"
    git(project, "bundle", "create", str(archive), "refs/heads/baseline", "refs/heads/candidate")
    digest = bundle_digest(archive, *commits)
    with pytest.raises(ValueError, match="digest_mismatch"):
        materialize(tmp_path, *commits, "0" * 64, tmp_path / "invalid")
    with pytest.raises(ValueError, match="refs_mismatch"):
        bundle_digest(archive, *reversed(commits))
    checkout = materialize(tmp_path, *commits, digest, tmp_path / "clean-project")
    assert git(checkout, "rev-parse", "HEAD").decode().strip() == commits[1]
    assert git(checkout, "rev-parse", "baseline").decode().strip() == commits[0]
    assert (checkout / "Assets/Wind.cs").read_bytes() == (project / "Assets/Wind.cs").read_bytes()


def test_prefab_case_receipt_requires_preserved_reference_and_actual_loader_state():
    fixture = Path(__file__).resolve().parents[2] / "TestProjects/UnityMCPTests/Verification"
    confirmed = runpy.run_path(str(fixture / "check_prefab_pair.py"))["confirmed"]

    spec = json.loads((fixture / "prefab-pair-expectations.json").read_bytes())
    guid = "a" * 32
    ref = dict(spec["serialized_references"][0], guid=guid, file_id="17")
    semantic = {"scenario": "verified-shop", "runs": {}, "admissions": {}, "builds": {}}
    for side in reproduce.SIDES:
        expected = spec[side]
        semantic["runs"][side] = {
            **expected,
            "log_marker": expected["state"]["log_marker"],
            "state": {"observed": dict(expected["state"]["expected"])},
        }
        semantic["admissions"][side] = {
            "assets": {ref["target"]: guid},
            "serialized_references": [dict(ref)],
            "source_observations": {"loader_guid": "" if side == "baseline" else guid},
        }
        semantic["builds"][side] = {"capture_sha256": "f" * 64}
    report = {"status": "consistent", "semantics": semantic}
    assert confirmed(report)
    semantic["admissions"]["candidate"]["source_observations"]["loader_guid"] = ""
    with pytest.raises(ValueError, match="conflicting_editor"):
        confirmed(report)
    semantic["admissions"]["candidate"]["source_observations"]["loader_guid"] = guid
    semantic["runs"]["baseline"]["state"]["observed"]["same_asset"] = 0
    with pytest.raises(ValueError, match="control_observation"):
        confirmed(report)


def test_fixture_requires_staged_definition_and_detects_build_rewrites(tmp_path):
    root = Path(__file__).resolve().parents[2]
    source = (
        root
        / "TestProjects/UnityMCPTests/Assets/Scripts/ScenarioPlayerFixture/Editor/ShopWorkflowFixtureBuild.cs"
    )
    text = source.read_text(encoding="utf-8")
    assert (
        text.index("store.Save(scenario)")
        < text.index('"--shop-prepare-only"')
        < text.index("PlayScenarioPlayerBuild.Build(")
    )
    frozen = runpy.run_path(
        str(root / "TestProjects/UnityMCPTests/Verification/build_reviewed_prefab_pair.py")
    )["frozen_definition"]
    git(tmp_path, "init")
    file = tmp_path / "ProjectSettings/MCPForUnity/PlayScenarios/verified-shop.json"
    file.parent.mkdir(parents=True)
    file.write_bytes(b'{"name":"verified-shop"}')
    with pytest.raises(ValueError):
        frozen(tmp_path, "")
    git(tmp_path, "add", "ProjectSettings")
    before = frozen(tmp_path, "")
    git(tmp_path, "commit", "-m", "prepared")
    assert frozen(tmp_path, "HEAD") == before
    st = file.stat()
    os.utime(file, ns=(st.st_atime_ns, st.st_mtime_ns + 2_000_000_000))
    assert frozen(tmp_path, "HEAD") != before
    file.write_bytes(b'{"name":"different"}')
    with pytest.raises(ValueError, match="not_frozen"):
        frozen(tmp_path, "HEAD")
