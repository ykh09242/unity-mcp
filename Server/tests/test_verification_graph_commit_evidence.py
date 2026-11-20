"""Exercise the real graph service and committed-closure CLI with a test Editor transport."""

from copy import deepcopy
from hashlib import sha256
import json

from click.testing import CliRunner
import pytest

from cli.main import cli
from services.verification.git_draft import git_draft
from services.verification.graph_commit_evidence import capture_guard, verify_impact_page
from tests.test_reference_graph_service import setup  # noqa: F401
from tests.test_verification_git_draft import git


@pytest.mark.asyncio
async def test_commit_bound_pages_use_published_imports_and_both_disk_observations(
    setup, tmp_path, monkeypatch
):
    service, sender, ctx = setup
    monkeypatch.setattr(service, "start", lambda *_, **__: None)
    project = tmp_path / "project"
    (project / "Assets").mkdir(parents=True)
    path = "Assets/Room.unity"
    guid = "1" * 32
    fingerprint = "a" * 32
    meta = project / (path + ".meta")
    meta.write_bytes(("guid: " + guid + "\n").encode())
    (project / path).write_bytes(b"old scene")
    git(project, "init")
    git(project, "config", "core.autocrlf", "false")
    git(project, "add", ".")
    git(project, "commit", "-m", "base")
    (project / path).write_bytes(b"new scene")
    git(project, "add", ".")
    git(project, "commit", "-m", "candidate")
    git_head = git(project, "rev-parse", "HEAD").strip().decode()
    draft = git_draft(project, "HEAD~1", "committed", candidate_ref="HEAD")
    draft_file = tmp_path / "draft.json"
    draft_file.write_text(json.dumps(draft), encoding="utf-8")
    witness = {
        "guid": guid,
        "import_hash": fingerprint,
        "import_id": "e" * 32,
        "asset_sha256": sha256((project / path).read_bytes()).hexdigest(),
        "meta_sha256": sha256(meta.read_bytes()).hexdigest(),
    }

    async def send(instance, request):
        result = await sender(instance, request)
        if request["action"] == "inventory":
            result["assets"] = [
                {"guid": guid, "path": path, "kind": "asset", "source_format": "none", "keys": []}
            ]
        elif request["action"] == "dependencies":
            result.update(
                present=True,
                fingerprint=fingerprint,
                file_stamp="9:123",
                meta_stamp="39:123",
                import_witness=witness,
                snapshot="deps",
                targets=[],
                next_offset=None,
            )
        elif request["action"] == "commit_links":
            result["links"] = {path: []}
        elif request["action"] == "commit_inputs":
            result.update(
                git_head=git_head,
                build_target="StandaloneLinux64",
                unity_version="6000",
                dirty_scenes=0,
                compiling=False,
                importing=False,
                compilation_failed=False,
                assets=[
                    {
                        "guid": guid,
                        "path": path,
                        "stamp": "9:123",
                        "meta_stamp": "39:123",
                        "import_hash": fingerprint,
                        "import_witness": witness,
                        "asset_sha256": sha256((project / path).read_bytes()).hexdigest(),
                        "meta_sha256": sha256(meta.read_bytes()).hexdigest(),
                    }
                ],
            )
        return result

    service.send = send
    try:
        entry, _ = await service.resolve(ctx, "status")
        entry.workspace.set_policy({"respect_gitignore": False}, expected_version=0)
        assert await entry.engine.reconcile()
        options = {"node": "asset:" + guid, "commit": git_head}
        impact = await service.execute(ctx, "impact", options)
        diagnostics = await service.execute(ctx, "diagnostics", options)
        assert verify_impact_page(project, git_head, impact)
        assert capture_guard(impact) == capture_guard(diagnostics)
        files = []
        for name, document in (("impact", impact), ("diagnostics", diagnostics)):
            file = tmp_path / (name + ".json")
            file.write_text(json.dumps(document), encoding="utf-8")
            files.append(str(file))
        run = CliRunner().invoke(
            cli,
            [
                "--no-project-context",
                "verification-candidate",
                "dependency-closure",
                "--project",
                str(project),
                "--draft",
                str(draft_file),
                "--root",
                path,
                "--graph-page",
                files[0],
                "--diagnostics",
                options["node"],
                files[1],
            ],
        )
        assert run.exit_code == 0, run.output
        assert json.loads(run.output)["graph_commit_binding"] == "verified"
        scan = CliRunner().invoke(
            cli,
            [
                "--no-project-context",
                "verification-candidate",
                "dependency-scan",
                "--project",
                str(project),
                "--commit",
                git_head,
                "--root",
                path,
                "--checkpoint",
                str(tmp_path / "scan.db"),
                "--graph-page",
                files[0],
            ],
        )
        assert scan.exit_code == 0, scan.output
        assert json.loads(scan.output)["state"] == "complete"
        assert json.loads(scan.output)["graph_commit_binding"] == "verified"
        for field, value, reason in (
            (
                "git_head",
                git(project, "rev-parse", "HEAD~1").strip().decode(),
                "checkout_changed_during_capture",
            ),
            ("dirty_scenes", 1, "unsaved_scene"),
            ("compiling", True, "compilation_not_ready"),
            ("importing", True, "compilation_not_ready"),
        ):
            changed = deepcopy(impact["data"])
            changed["commit_evidence"]["after"][field] = value
            with pytest.raises(ValueError, match=reason):
                verify_impact_page(project, git_head, changed)
            if field == "git_head":
                changed["commit_evidence"]["before"][field] = value
                with pytest.raises(ValueError, match="editor_checkout_mismatch"):
                    verify_impact_page(project, git_head, changed)
        unwitnessed = deepcopy(impact["data"])
        for side in ("before", "after"):
            unwitnessed["commit_evidence"][side]["assets"][0]["import_witness"] = None
        with pytest.raises(ValueError, match="unobserved_import"):
            verify_impact_page(project, git_head, unwitnessed)
        for field in ("asset_sha256", "meta_sha256", "import_hash"):
            changed = deepcopy(impact["data"])
            changed["commit_evidence"]["after"]["assets"][0][field] = "d" * (
                32 if field == "import_hash" else 64
            )
            with pytest.raises(ValueError, match="collection_changed"):
                verify_impact_page(project, git_head, changed)
            changed["commit_evidence"]["before"] = deepcopy(changed["commit_evidence"]["after"])
            with pytest.raises(
                ValueError, match="published_import_mismatch|imported_source_changed"
            ):
                verify_impact_page(project, git_head, changed)
            if field.endswith("sha256"):
                for record in [
                    changed["commit_evidence"][side]["assets"][0] for side in ("before", "after")
                ]:
                    record["import_witness"][field] = record[field]
                changed["commit_evidence"]["published"]["asset:" + guid]["import_witness"][
                    field
                ] = "d" * 64
                with pytest.raises(ValueError, match="source_bytes_mismatch"):
                    verify_impact_page(project, git_head, changed)
        changed = deepcopy(impact["data"])
        changed["page_sequence"] = 77
        with pytest.raises(ValueError, match="page_identity_mismatch"):
            verify_impact_page(project, git_head, changed)
    finally:
        await service.aclose()
