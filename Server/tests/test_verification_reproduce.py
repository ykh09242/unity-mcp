"""Two checkouts, two frozen builds and independent project-owned expectations."""

from hashlib import sha256
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from services.verification import reproduce
from services.verification.reproduction_verdict import evaluate_native, pair_verdict
from .test_play_scenario_player import bundle
from .test_play_scenario_player_assurance import verified_bundle
from .test_verification_git_draft import git
from .test_verification_player_candidates import registered
from .test_verification_portable_candidate import invoke


def write(path, data):
    raw = json.dumps(data).encode()
    path.write_bytes(raw)
    return sha256(raw).hexdigest()


@pytest.fixture
def review(tmp_path, registered, monkeypatch):
    project, script, register = registered
    verifier = tmp_path / "verifier"
    package = verifier / "MCPForUnity"
    package.mkdir(parents=True)
    write(package / "package.json", {})
    (verifier / "Server").mkdir()
    (verifier / "Server/uv.lock").write_bytes(b"lock")
    git(verifier, "init")
    git(verifier, "add", ".")
    git(verifier, "commit", "-m", "package for frozen players")
    package_commit = git(verifier, "rev-parse", "HEAD").decode().strip()
    monkeypatch.setattr(
        reproduce, "__file__", str(verifier / "Server/src/services/verification/reproduce.py")
    )
    git(project, "init")
    git(project, "config", "core.autocrlf", "false")
    root = tmp_path / "registrations"
    pair = {"schema_version": 1, "kind": "verification_player_pair", "scenario": "wind-cycle"}
    dependency = project / "Assets/WindCurve.asset"
    dependency.write_bytes(b"curve")
    metadata = project / "Assets/WindCurve.asset.meta"
    metadata.write_bytes(b"guid: 00000000000000000000000000000004\n")
    additions = {
        "Assets/" + path.name: sha256(path.read_bytes()).hexdigest()
        for path in (dependency, metadata)
    }
    for index, name in enumerate(reproduce.SIDES, 1):
        script.write_bytes(f"class Wind {{ int speed = {index}; }}".encode())
        git(project, "add", "Assets")
        git(project, "commit", "-m", name)
        commit = git(project, "rev-parse", "HEAD").decode().strip()
        direct = {"Assets/Wind.cs": sha256(script.read_bytes()).hexdigest()}
        inputs = {**direct, **additions}
        registration = register(
            name,
            build_id=f"{index:032x}",
            inputs=inputs,
            source_revision=reproduce.source_revision(inputs),
        )
        directory = root / name
        bundle_path = directory / "build/scenario-bundle.json"
        frozen = json.loads(bundle_path.read_bytes())
        frozen["build_id"] = registration["build_id"]
        bundle_pin = write(bundle_path, frozen)
        proof = directory / "proof.json"
        export = invoke("export", project, "--candidate-id", name, "--output", proof)
        assert export.exit_code == 0, export.output
        revision = registration["source_revision"]
        closure = {
            "schema_version": 1,
            "kind": "verification_dependency_closure",
            "candidate_commit": commit,
            "expanded_source_revision": revision,
            "candidate_source_revision": reproduce.source_revision(direct),
            "input_hashes": additions,
            "coverage": "partial_static",
            "graph_commit_binding": "unverified",
            "unresolved": [],
        }
        pair[name] = {
            "project_commit": commit,
            "package_commit": package_commit,
            "proof_sha256": json.loads(export.output)["proof_sha256"],
            "closure_sha256": write(directory / "closure.json", closure),
            "build_id": registration["build_id"],
            "source_revision": revision,
            "bundle_manifest_sha256": bundle_pin,
            "payload_hash": registration["payload_hash"],
            "status": "timed_out" if index == 1 else "succeeded",
            "checkpoint": "wind ready",
            "action": "wait_state",
            "state": {"log_marker": "WIND ", "expected": {"speed": index}},
        }
        pair["definition_hash"] = frozen["definition_hash"]
    return project, verifier, root, pair


def run(review, tmp_path):
    project, _, root, pair = review
    manifest = root / "pair.json"
    pin = write(manifest, pair)
    result = invoke(
        "reproduce",
        project,
        "--manifest",
        manifest,
        "--sha256",
        pin,
        "--output",
        tmp_path / "results",
    )
    receipt = tmp_path / "results/reproduction.json"
    assert receipt.exists(), (result.output, result.exception)
    report = json.loads(result.output)
    assert report == json.loads(receipt.read_bytes())
    return result.exit_code, report


def test_clean_crlf_clone_reports_exact_metadata_path_before_launch(review, tmp_path, monkeypatch):
    project, verifier, root, pair = review
    clone = tmp_path / "crlf-project"
    git(
        tmp_path,
        "-c",
        "core.autocrlf=true",
        "clone",
        "--config",
        "core.autocrlf=true",
        "--local",
        "--",
        str(project),
        str(clone),
    )
    assert (clone / "Assets/WindCurve.asset.meta").read_bytes().endswith(b"\r\n")
    assert git(clone, "status", "--porcelain") == b""
    monkeypatch.setattr(reproduce, "run_player", lambda options: pytest.fail("unadmitted launch"))
    code, report = run((clone, verifier, root, pair), tmp_path)
    assert code == 2 and not report["runs"]
    assert "Assets/WindCurve.asset.meta" in report["reason"]
    assert "core.autocrlf=false" in report["reason"]


@pytest.mark.parametrize(
    "fault", ["missing", "closure", "dependency", "new-native", "bad-payload", "dirty-project"]
)
def test_refuses_either_invalid_side_before_any_launch(review, tmp_path, monkeypatch, fault):
    project, verifier, root, pair = review
    if fault == "missing":
        (root / "candidate/build/scenario-bundle.json").unlink()
    elif fault in {"closure", "dependency"}:
        closure = root / "candidate/closure.json"
        value = json.loads(closure.read_bytes())
        if fault == "closure":
            value["unresolved"].append({"reason": "missing_dependency"})
        else:
            value["input_hashes"]["Assets/WindCurve.asset"] = "1" * 64
        pair["candidate"]["closure_sha256"] = write(closure, value)
    elif fault == "new-native":
        (verifier / "MCPForUnity/Changed.cs").write_bytes(b"class Changed {}")
        git(verifier, "add", ".")
        git(verifier, "commit", "-m", "new native source")
    elif fault == "bad-payload":
        (root / "candidate/build/MCPScenarioPlayer.exe").write_bytes(b"other payload")
    else:
        (project / "Assets/Wind.cs").write_bytes(b"changed since commit")
    monkeypatch.setattr(reproduce, "run_player", lambda options: pytest.fail("unadmitted launch"))
    code, report = run(review, tmp_path)
    assert code == 2 and report["status"] == "unavailable" and not report["runs"]
    assert (
        report["required_builds"]["candidate"]["payload_hash"] == pair["candidate"]["payload_hash"]
    )
    if fault == "missing":
        assert "candidate/build/scenario-bundle.json" in report["missing"]
    if fault == "new-native":
        assert "new_player_build_required" in report["reason"]


@pytest.mark.parametrize("fault", [None, "wrong-marker", "missing-marker", "forced"])
def test_receipt_requires_two_fresh_native_results_and_matching_state(
    review, tmp_path, monkeypatch, fault
):
    pair = review[-1]
    retained = {}

    def launcher(options):
        job = options.player_job_id
        directory = options.output_directory / job
        directory.mkdir(parents=True)
        side = options.output_directory.name
        row = pair[side]
        assert (
            options.require_verified_payload
            and options.expected_payload_hash == row["payload_hash"]
        )
        ok = side == "candidate"
        code = 0 if ok else 1
        raw = {
            "job_id": job,
            "status": row["status"],
            "exit_code": code,
            "execution_environment": "player",
            "finalization_state": "completed",
            "runner_resources_released": True,
            "steps": [
                {
                    "stage": "main",
                    "name": row["checkpoint"],
                    "action": row["action"],
                    "status": "passed" if ok else "timed_out",
                }
            ],
            "reproduction": {
                "definition_hash": pair["definition_hash"],
                "build_id": row["build_id"],
                "build_source_revision": row["source_revision"],
                "payload_hash": row["payload_hash"],
                "environment": {"platform": "WindowsPlayer", "scripting_backend": "mono"},
            },
        }
        write(directory / "run.json", raw)
        diagnostics = {
            "job_id": job,
            "process_id": 123,
            "cleanup_state": "native_finalized",
            "deadline_reached": False,
            "payload_verification": "launcher_admission",
            "process_exit_code": code,
        }
        write(directory / "diagnostics.json", diagnostics)
        write(
            directory / "process.json",
            {
                "job_id": job,
                "process_id": 123,
                "process_ended": True,
                "forced_termination": ok and fault == "forced",
                "actual_exit_code": code,
                "launch_environment": {"host_platform": "win32", "build_target": "windows64"},
            },
        )
        speed = 3 if ok and fault == "wrong-marker" else (2 if ok else 1)
        (directory / "player.log").write_text(
            "" if ok and fault == "missing-marker" else "WIND " + json.dumps({"speed": speed}),
            encoding="utf-8",
        )
        retained[side] = SimpleNamespace(
            report=raw,
            directory=directory,
            diagnostics=diagnostics,
            exit_code=code,
            client_error=None,
        )
        return retained[side]

    monkeypatch.setattr(reproduce, "run_player", launcher)
    code, report = run(review, tmp_path)
    assert code == {None: 0, "wrong-marker": 1, "missing-marker": 2, "forced": 2}[fault], report
    # Replay the same schema-v1 native records independently of Git and the launcher.
    expected_pair = reproduce.PlayerPair.model_validate(pair)
    replay = {}
    for name, outcome in retained.items():
        side = getattr(expected_pair, name)
        job = outcome.directory.name
        try:
            replay[name] = evaluate_native(
                outcome,
                outcome.directory.parent,
                expected_pair,
                side,
                job,
                expectation=side,
                save_upgrade=False,
            )
        except ValueError as error:
            assert code == 2 and name == "candidate" and report["reason"] == str(error)
            assert report["runs"][name]["status"] == "incomplete"
        else:
            assert replay[name] == report["runs"][name]
    if code != 2:
        verdict, replay_code = pair_verdict(replay, save_upgrade=False)
        assert (verdict, replay_code) == ({"status": report["status"]}, code)
        assert report["status"] == ("reproduced" if fault is None else "expectation_mismatch")
    if fault is None:
        assert report["status"] == "reproduced"
        assert report["admissions"]["candidate"]["closure"]["verified_additional_files"] == 2
        assert report["runs"]["baseline"]["job_id"] != report["runs"]["candidate"]["job_id"]
        for row in report["runs"].values():
            raw = tmp_path / "results" / row["artifacts"] / "run.json"
            assert row["files"]["run.json"] == sha256(raw.read_bytes()).hexdigest()
        monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / "tools"))
        from compare_player_pair_replays import compare

        project, _, root, _ = review
        pin = report["manifest_sha256"]
        second = tmp_path / "second-results"
        again, code = reproduce.reproduce_pair(project, root / "pair.json", pin, root, second)
        assert code == 0, again
        first = tmp_path / "results"
        assert compare(first, second, pin)["status"] == "consistent"
        assert compare(first, first, pin)["reason"] == "independent_replay_jobs_required"
        assert compare(first, second, "f" * 64)["status"] == "unavailable"
        # Two passing receipts must still agree on every main-step outcome.
        candidate = again["runs"]["candidate"]
        native_file = second / candidate["artifacts"] / "run.json"
        native = json.loads(native_file.read_bytes())
        extra_step = {
            "stage": "cleanup",
            "name": "another cleanup",
            "action": "wait",
            "status": "passed",
        }
        native["steps"].append(extra_step)
        candidate["steps"].append(extra_step)
        candidate["files"]["run.json"] = write(native_file, native)
        write(second / "reproduction.json", again)
        assert compare(first, second, pin)["status"] == "disagreement"
        evidence = second / again["runs"]["baseline"]["artifacts"] / "player.log"
        evidence.write_bytes(b"modified after upload")
        assert "retained_replay_evidence_changed" in compare(first, second, pin)["reason"]


@pytest.mark.parametrize(
    ("source", "reason"),
    [
        ([], "candidate_json_object_required"),
        (
            {"package_checkout_sha256": "bad"},
            "reproduction_native_package_checkout_digest_required",
        ),
        ({"package_checkout_sha256": "a" * 64}, "reproduction_native_build_source_mismatch"),
    ],
)
def test_invalid_candidate_provenance_prevents_even_baseline_launch(
    review, tmp_path, monkeypatch, source, reason
):
    _, _, artifacts, pair = review
    pair["candidate"]["capture_sha256"] = "a" * 64
    write(artifacts / "candidate/build/build-source.json", source)
    monkeypatch.setattr(reproduce, "run_player", lambda options: pytest.fail("unadmitted launch"))
    code, report = run(review, tmp_path)
    assert (code, report["status"], report["reason"]) == (2, "unavailable", reason)
    assert report["runs"] == {} and set(report["admissions"]) == {"baseline"}


@pytest.mark.parametrize(
    ("save_upgrade", "phases"),
    [
        (False, ()),
        (False, ("baseline",)),
        (False, ("candidate",)),
        (False, ("baseline", "candidate", "restart")),
        (True, ("baseline", "candidate")),
        (True, ("baseline", "restart")),
        (True, ("candidate", "restart")),
        (True, ("baseline", "candidate", "restart", "extra")),
    ],
)
def test_pair_verdict_requires_exact_recorded_phases(save_upgrade, phases):
    runs = {phase: {"matched": True, "status": "succeeded"} for phase in phases}
    verdict, code = pair_verdict(runs, save_upgrade=save_upgrade)
    assert (verdict["status"], code) == ("unavailable", 2)


@pytest.mark.parametrize("save_upgrade", [False, True])
def test_pair_verdict_requires_completed_final_phase(save_upgrade):
    runs = {"baseline": {"matched": True, "status": "succeeded"}}
    if save_upgrade:
        runs["candidate"] = {"matched": True, "status": "succeeded"}
    runs["restart" if save_upgrade else "candidate"] = {"matched": False, "status": "incomplete"}
    verdict, code = pair_verdict(runs, save_upgrade=save_upgrade)
    assert (verdict["status"], code) == ("unavailable", 2)


def test_cli_preserves_partial_phase_records_as_unavailable(review, tmp_path, monkeypatch):
    partial = {"baseline": {"matched": True, "status": "succeeded"}}

    def incomplete_execution(artifacts, output, pair, report, *, launch):
        report["runs"].update(partial)

    monkeypatch.setattr(reproduce, "execute_pair", incomplete_execution)
    code, report = run(review, tmp_path)
    assert (code, report["status"], report["reason"]) == (
        2,
        "unavailable",
        "reproduction_run_phases_incomplete",
    )
    assert report["runs"] == partial
