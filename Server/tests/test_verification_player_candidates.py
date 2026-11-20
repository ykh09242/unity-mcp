"""Registered build admission and the public verification handle lifecycle."""

from hashlib import sha256
import json
from pathlib import Path
import shutil

import pytest

from services.verification.player_candidate import prepare_candidate, source_revision
from services.verification.player_runner import _state_result
from services.verification.player_candidate import StateCheck
from .test_play_scenario_player import bundle, child
from .test_play_scenario_player_assurance import verified_bundle
from .test_verification_service import service


@pytest.fixture
def registered(tmp_path, verified_bundle, monkeypatch):
    source_build, _, original = verified_bundle
    root = tmp_path / "registrations"
    project = tmp_path / "project"
    script = project / "Assets" / "Wind.cs"
    script.parent.mkdir(parents=True)
    script.write_bytes(b"class Wind {}")
    monkeypatch.setenv("UNITY_MCP_VERIFICATION_ROOT", str(root))

    def register(identity, scenario="wind-cycle", **extras):
        directory = root / identity
        build = directory / "build"
        shutil.copytree(source_build, build)
        inputs = {"Assets/Wind.cs": sha256(script.read_bytes()).hexdigest()}
        doc = {
            "schema_version": 1,
            "project": str(project),
            "scenario": scenario,
            "source_revision": source_revision(inputs),
            "inputs": inputs,
            "build_id": original["build_id"],
            "payload_hash": original["payload_hash"],
            **extras,
        }
        frozen = {
            **original,
            "definition": {**original["definition"], "name": scenario},
            "scenario_name": scenario,
            "build_source_revision": doc["source_revision"],
        }
        frozen["definition_json"] = json.dumps(
            frozen["definition"], sort_keys=True, ensure_ascii=False, separators=(",", ":")
        )
        frozen["definition_hash"] = sha256(frozen["definition_json"].encode()).hexdigest()
        (build / "scenario-bundle.json").write_text(json.dumps(frozen), encoding="utf-8")
        (directory / "candidate.json").write_text(json.dumps(doc), encoding="utf-8")
        return doc

    return project, script, register


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("identity", "scenario", "mode", "verdict"),
    [
        ("wind-v2", "wind-cycle", "success", "passed"),
        ("lighting-r2", "shadow-transition", "failed", "failed"),
    ],
)
async def test_registered_scenarios_execute_fresh_owned_child(
    registered, service, child, identity, scenario, mode, verdict
):
    project, _, register = registered
    register(identity, scenario, broader_check="complete-mode")
    subject, ctx, target, native = service
    native.return_value["data"]["projectRoot"] = str(project)
    child[0][0] = mode
    response = await subject.execute(ctx, "prepare", {"candidate_id": identity})
    assert response["success"], response
    handle = response["data"]["preparation_id"]
    started = await subject.execute(ctx, "run", {"preparation_id": handle, "wait_seconds": 5})
    run_id = started["data"]["run_id"]
    result = (await subject.execute(ctx, "report", {"run_id": run_id}))["data"]
    assert result["state"] == verdict, result
    assert result["required_permissions"] == ["read", "run"]
    player = next(row for row in result["stages"] if row["name"] == "standalone_player")["evidence"]
    assert player["scenario"] == scenario
    assert result["executed_checks"] == [
        {"kind": "scenario", "id": scenario, "job_id": player["job_id"], "status": player["status"]}
    ]
    assert result["additional_checks_outstanding"] == [{"kind": "profile", "id": "complete-mode"}]
    assert player["exit_code"] == (0 if verdict == "passed" else 1)
    assert Path(player["artifacts"], "run.json").is_file()
    replay = await subject.execute(ctx, "run", {"preparation_id": handle})
    assert replay["data"]["run_id"] == run_id and len(child[1]) == 1
    target.scope.key = ("u", "other-project")
    assert (await subject.execute(ctx, "report", {"run_id": run_id}))[
        "code"
    ] == "verification_not_found"


@pytest.mark.asyncio
@pytest.mark.parametrize("source_mode", ["current", "frozen"])
async def test_concurrent_source_edit_after_prepare_blocks_dispatch(
    registered, service, child, source_mode
):
    project, script, register = registered
    register("wind")
    subject, ctx, _, native = service
    native.return_value["data"]["projectRoot"] = str(project)
    response = await subject.execute(
        ctx, "prepare", {"candidate_id": "wind", "source_mode": source_mode}
    )
    script.write_bytes(b"class UpdatedWind {}")
    result = await subject.execute(
        ctx, "run", {"preparation_id": response["data"]["preparation_id"], "wait_seconds": 5}
    )
    assert result["data"]["state"] == "stale" and not child[1]


@pytest.mark.asyncio
async def test_missing_requested_baseline_survives_valid_native_child(registered, service, child):
    project, _, register = registered
    register("wind")
    subject, ctx, _, native = service
    native.return_value["data"]["projectRoot"] = str(project)
    result = await subject.execute(
        ctx, "run", {"candidate_id": "wind", "baseline_candidate_id": "absent", "wait_seconds": 5}
    )
    assert result["data"]["state"] == "incomplete", result
    assert result["data"]["comparison"]["baseline_status"] == "missing"
    assert result["data"]["comparison"]["changed_paths"] is None
    assert len(child[1]) == 1


@pytest.mark.parametrize("key", ["build_id", "payload_hash", "scenario"])
@pytest.mark.asyncio
async def test_unrelated_installed_build_is_refused_before_launch(registered, service, child, key):
    project, _, register = registered
    doc = register("wind")
    options = {"candidate_id": "wind"}
    prepared = prepare_candidate({"projectRoot": str(project)}, options)
    doc[key] = {"build_id": "c" * 32, "payload_hash": "f" * 64, "scenario": "other-name"}[key]
    (prepared.root / "wind" / "candidate.json").write_text(json.dumps(doc), encoding="utf-8")
    subject, ctx, _, native = service
    native.return_value["data"]["projectRoot"] = str(project)
    result = await subject.execute(ctx, "run", {**options, "wait_seconds": 5})
    assert result["data"]["state"] == "incomplete", result
    assert not child[1]


def test_registration_cannot_supply_data_for_another_project(registered):
    project, _, register = registered
    register("wind")
    with pytest.raises(ValueError, match="candidate_project_mismatch"):
        prepare_candidate({"projectRoot": str(project.parent / "other")}, {"candidate_id": "wind"})


@pytest.mark.asyncio
async def test_edit_during_standalone_run_supersedes_success(
    registered, service, child, monkeypatch
):
    from services.verification import player_runner

    project, script, register = registered
    register("wind")
    subject, ctx, _, native = service
    native.return_value["data"]["projectRoot"] = str(project)
    start = player_runner.run_player

    def edit_during_run(options):
        script.write_text("class Changed {}", encoding="utf-8")
        return start(options)

    monkeypatch.setattr(player_runner, "run_player", edit_during_run)
    result = await subject.execute(ctx, "run", {"candidate_id": "wind", "wait_seconds": 5})
    data = result["data"]
    assert data["state"] == "stale" and len(child[1]) == 1
    assert (
        next(stage for stage in data["stages"] if stage["name"] == "standalone_player")["state"]
        == "passed"
    )


@pytest.mark.parametrize(
    ("lines", "state"),
    [
        (['STATE {"visible":true}'], "passed"),
        (['STATE {"visible":false}'], "failed"),
        (['STATE {"visible":1}'], "failed"),
        (['STATE {"visible":true}', 'STATE {"visible":false}'], "incomplete"),
        (["no witness"], "incomplete"),
    ],
)
def test_observed_state_requires_matching_typed_native_fields(tmp_path, lines, state):
    (tmp_path / "player.log").write_text("\n".join(lines), encoding="utf-8")
    check = StateCheck(log_marker="STATE ", expected={"visible": True})
    assert _state_result(tmp_path, check)[0] == state


def test_partial_log_cannot_prove_complete_state_observation(tmp_path):
    from cli.utils.play_scenario_player import PROCESS_LOG_LIMIT

    prefix = b'STATE {"visible":true}\n'
    (tmp_path / "player.log").write_bytes(prefix.ljust(PROCESS_LOG_LIMIT, b" "))
    check = StateCheck(log_marker="STATE ", expected={"visible": True})
    assert _state_result(tmp_path, check) == ("incomplete", {"reason": "state_log_retention_limit"})


@pytest.mark.asyncio
async def test_incidental_marker_cannot_pass_public_verification(
    registered, service, child, monkeypatch
):
    from services.verification import player_runner

    project, _, register = registered
    register("wind", state_check={"log_marker": "STATE ", "expected": {"visible": True}})
    subject, ctx, _, native = service
    native.return_value["data"]["projectRoot"] = str(project)
    launch = player_runner.run_player

    def run_with_diagnostic(options):
        outcome = launch(options)
        with (outcome.directory / "player.log").open("a", encoding="utf-8") as log:
            log.write('No witness received; expected STATE {"visible":true}\n')
        return outcome

    monkeypatch.setattr(player_runner, "run_player", run_with_diagnostic)
    result = await subject.execute(ctx, "run", {"candidate_id": "wind", "wait_seconds": 5})
    report = result["data"]
    assert report["state"] == "incomplete" and report["native_settled"] is True
    stages = {row["name"]: row for row in report["stages"]}
    assert stages["standalone_player"]["state"] == "passed"
    assert stages["observed_state"]["state"] == "incomplete"
    assert stages["observed_state"]["evidence"] == {"reason": "state_observation_missing_or_ambiguous"}


def test_predispatch_cancellation_creates_no_unsettled_job(verified_bundle, child):
    from cli.utils.play_scenario_player import PlayerLaunchError, PlayerRunOptions, run_player

    build, output, _ = verified_bundle
    with pytest.raises(PlayerLaunchError, match="canceled before dispatch"):
        run_player(PlayerRunOptions(build, output, cancel_requested=lambda: True))
    assert not output.exists() and not child[1]


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["request_write", "launch_deadline", "os_refusal"])
async def test_confirmed_launch_refusals_release_verification_capacity(
    registered, service, child, monkeypatch, fault
):
    from cli.utils import play_scenario_player as launcher

    project, _, register = registered
    register("wind")
    subject, ctx, _, native = service
    native.return_value["data"]["projectRoot"] = str(project)
    original_write = Path.write_text
    original_deadline = launcher.check_deadline
    output = project.parent / "registrations" / "wind" / "verification-runs"
    definition_written = False

    def write_text(path, *args, **kwargs):
        nonlocal definition_written
        if path.name == "request.json" and fault == "request_write":
            raise OSError("request write refused")
        size = original_write(path, *args, **kwargs)
        if path.name == "definition.json":
            definition_written = True
        return size

    def check_deadline(deadline):
        if definition_written and fault == "launch_deadline":
            raise TimeoutError("deadline expired after request creation")
        original_deadline(deadline)

    def refuse_spawn(*args, **kwargs):
        raise PermissionError("OS refused to start executable")

    with monkeypatch.context() as patch:
        patch.setattr(Path, "write_text", write_text)
        patch.setattr(launcher, "check_deadline", check_deadline)
        if fault == "os_refusal":
            patch.setattr(launcher.subprocess, "Popen", refuse_spawn)
        # The third call crosses the two-active-runs-per-owner capacity limit.
        for _ in range(3):
            definition_written = False
            response = await subject.execute(
                ctx, "run", {"candidate_id": "wind", "wait_seconds": 5}
            )
            report = response["data"]
            assert report["state"] == "incomplete" and report["native_settled"] is True
            job = report["children"][0]
            assert job["settled"] is True
            assert job["dispatch"] == ("refused" if fault == "os_refusal" else "preparing")
            directory = output / job["job_id"]
            assert directory.is_dir() and not (directory / "process.json").exists()
        assert not child[1]
    success = await subject.execute(ctx, "run", {"candidate_id": "wind", "wait_seconds": 5})
    assert success["data"]["state"] == "passed" and len(child[1]) == 1


@pytest.mark.asyncio
async def test_uncertain_dispatch_stays_unsettled_without_matching_exit(
    registered, service, child, monkeypatch
):
    import errno

    from cli.utils import play_scenario_player as launcher

    project, _, register = registered
    register("wind")
    subject, ctx, _, native = service
    native.return_value["data"]["projectRoot"] = str(project)

    def ambiguous(*args, **kwargs):
        raise OSError(errno.EIO, "OS dispatch outcome unavailable")

    monkeypatch.setattr(launcher.subprocess, "Popen", ambiguous)
    result = await subject.execute(ctx, "run", {"candidate_id": "wind", "wait_seconds": 5})
    report = result["data"]
    job = report["children"][0]
    assert report["state"] == "incomplete" and report["native_settled"] is False
    assert job["dispatch"] == "dispatching" and job["settled"] is False
    output = project.parent / "registrations" / "wind" / "verification-runs"
    path = output / job["job_id"] / "process.json"
    for identity in ("wrong-job", job["job_id"]):
        path.write_text(json.dumps({"job_id": identity, "process_ended": True}), encoding="utf-8")
        observed = await subject.execute(ctx, "status", {"run_id": report["run_id"]})
        assert observed["data"]["native_settled"] == (identity == job["job_id"])
