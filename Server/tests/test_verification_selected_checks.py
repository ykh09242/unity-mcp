"""Selected checks use project-owned native jobs through the public candidate run lifecycle."""

import asyncio
from hashlib import sha256
import json
from unittest.mock import AsyncMock

import pytest

from .test_play_scenario_player import bundle, child
from .test_play_scenario_player_assurance import verified_bundle
from .test_verification_console import WindowIO
from .test_verification_input_chunks import publish_index, setup_inventory
from .test_verification_player_candidates import registered
from .test_verification_runner import freshness
from .test_verification_service import service


@pytest.fixture
def mapped(registered, service, monkeypatch):
    project, script, register = registered
    subject, ctx, _, native = service
    register("old")
    script.write_text("class Wind { int speed; }", encoding="utf-8")
    register(
        "new",
        mappings=[
            {
                "path": "Assets/Wind.cs",
                "tests": ["Wind.Gust"],
                "scenarios": ["a-missing", "b-storm", "wind-cycle"],
            }
        ],
    )
    window = WindowIO()
    window.tests["result_page"] = {
        "items": [{"fullName": "Wind.Gust", "state": "Passed"}],
        "total_count": 1,
        "has_more": False,
        "next_cursor": None,
    }
    jobs = {}
    profile_document = native.return_value["data"]["document"]

    async def dispatch(_send, _instance, command, params, **kwargs):
        assert kwargs["retry_on_reload"] is False
        if command == "manage_verification":
            return {"success": True, "data": {"byte_hash": "missing", "document": profile_document}}
        if command == "get_project_info":
            return {"success": True, "data": {"projectRoot": str(project)}}
        if command != "manage_play_scenario":
            return await window.native(command, params, 0)
        action = params["action"]
        if action == "get":
            return {"success": params["name"] != "a-missing", "data": {"name": params["name"]}}
        job_id = params["job_id"]
        if action == "run":
            assert job_id not in jobs
            jobs[job_id] = {
                "job_id": job_id,
                "scenario": {"name": params["name"]},
                "status": "running",
                "execution_environment": "editor",
                "reproduction": {
                    "source_revision": params["source_revision"],
                    "definition_hash": "b" * 64,
                },
            }
        elif action == "status":
            jobs[job_id].update(status="failed", phase="finished", runner_resources_released=True)
        return {"success": True, "data": jobs[job_id].copy()}

    native.side_effect = dispatch
    monkeypatch.setattr(
        "services.verification.service.VerificationIO.freshness",
        AsyncMock(return_value=freshness()),
    )
    return subject, ctx, native, window, jobs, profile_document


OPTIONS = {"candidate_id": "new", "baseline_candidate_id": "old", "execute_selected_checks": True}


@pytest.mark.asyncio
async def test_selected_jobs_keep_independent_native_results_and_never_replay(mapped, child):
    subject, ctx, native, window, jobs, _ = mapped
    plan = await subject.execute(ctx, "prepare", {**OPTIONS, "selected_test_mode": "PlayMode"})
    handle = plan["data"]["preparation_id"]
    report = (await subject.execute(ctx, "run", {"preparation_id": handle, "wait_seconds": 5}))[
        "data"
    ]
    assert report["state"] == "failed" and report["native_settled"] is True
    assert report["required_permissions"] == ["read", "run", "test"]
    rows = {item["id"]: item for item in report["selected_check_results"]}
    assert rows["a-missing"]["state"] == "incomplete" and rows["a-missing"]["job_id"] is None
    assert rows["b-storm"]["state"] == "failed" and rows["b-storm"]["job_id"] in jobs
    assert (
        rows["wind-cycle"]["state"] == "passed"
        and rows["wind-cycle"]["evidence"]["target"] == "standalone_player"
    )
    assert rows["Wind.Gust"]["state"] == "passed"
    assert rows["Wind.Gust"]["evidence"]["summary"]["passed"] == 1
    assert rows["Wind.Gust"]["evidence"]["test_result"] == {
        "job_id": rows["Wind.Gust"]["job_id"],
        "full_name": "Wind.Gust",
        "state": "Passed",
    }
    test = next(params for command, params in window.calls if command == "run_tests")
    assert test["testNames"] == ["Wind.Gust"] and test["mode"] == "PlayMode"
    assert test["source_token"] == "a" * 64 and test["require_source_freshness"] is True
    assert set(item["id"] for item in report["executed_checks"]) == {
        "wind-cycle",
        "b-storm",
        "Wind.Gust",
    }
    for execution in report["executed_checks"]:
        row = rows[execution["id"]]
        evidence = row["evidence"]
        assert evidence["candidate_source_revision"] == report["comparison"]["source_revision"]
        owned = next(job for job in report["children"] if job["job_id"] == row["job_id"])
        assert owned["check_kind"] == row["kind"] and owned["check_id"] == row["id"]
        assert (
            owned["definition_hash"]
            == execution["definition_hash"]
            == evidence["definition_hash"]
            == row["definition_hash"]
        )
    assert (
        rows["b-storm"]["evidence"]["definition_hash"]
        == next(iter(jobs.values()))["reproduction"]["definition_hash"]
    )
    assert (
        rows["wind-cycle"]["evidence"]["definition_hash"]
        != rows["b-storm"]["evidence"]["definition_hash"]
    )
    assert report["additional_checks_outstanding"] == [{"kind": "scenario", "id": "a-missing"}]
    for action, options in (
        ("run", {"preparation_id": handle}),
        ("status", {"run_id": report["run_id"]}),
        ("report", {"run_id": report["run_id"]}),
    ):
        read = (await subject.execute(ctx, action, options))["data"]
        assert read["selected_check_results"] == report["selected_check_results"]
    assert len(jobs) == len(child[1]) == 1
    assert sum(command == "run_tests" for command, _ in window.calls) == 1


@pytest.mark.asyncio
async def test_foreign_retained_case_cannot_witness_the_selected_name(mapped, child):
    subject, ctx, _, window, _, _ = mapped
    window.tests["result_page"]["items"][0]["fullName"] = "Wind.Other"
    report = (await subject.execute(ctx, "run", {**OPTIONS, "wait_seconds": 5}))["data"]
    checked = next(row for row in report["selected_check_results"] if row["kind"] == "test")
    assert checked["evidence"]["test_result_gap"] == "exact_test_case_unavailable"
    assert "test_result" not in checked["evidence"]


@pytest.mark.asyncio
async def test_unknown_scenario_start_observes_only_its_owned_id_and_blocks_replay(mapped, child):
    subject, ctx, native, window, jobs, _ = mapped
    dispatch = native.side_effect

    async def lost(*args, **kwargs):
        value = await dispatch(*args, **kwargs)
        if args[2] == "manage_play_scenario" and args[3]["action"] == "run":
            raise ConnectionError("lost acknowledgment")
        return value

    native.side_effect = lost
    report = (await subject.execute(ctx, "run", {**OPTIONS, "wait_seconds": 5}))["data"]
    rows = {row["id"]: row for row in report["selected_check_results"]}
    assert rows["b-storm"]["state"] == "incomplete" and rows["b-storm"]["job_id"] in jobs
    assert rows["Wind.Gust"]["evidence"]["reason"] == "previous_native_child_unsettled"
    assert not report["native_settled"] and not window.calls
    settled = (await subject.execute(ctx, "status", {"run_id": report["run_id"]}))["data"]
    assert settled["native_settled"] and len(jobs) == 1
    assert settled["state"] == "incomplete"


@pytest.mark.asyncio
async def test_foreign_scenario_definition_never_settles_or_receives_cancel(mapped, child):
    subject, ctx, native, _, jobs, _ = mapped
    dispatch = native.side_effect

    async def changed(*args, **kwargs):
        result = await dispatch(*args, **kwargs)
        if args[2] == "manage_play_scenario" and args[3]["action"] == "status":
            result["data"]["reproduction"] = {
                **result["data"]["reproduction"],
                "definition_hash": "c" * 64,
            }
        return result

    native.side_effect = changed
    report = (await subject.execute(ctx, "run", {**OPTIONS, "wait_seconds": 5}))["data"]
    scenario = next(row for row in report["selected_check_results"] if row["id"] == "b-storm")
    assert (
        scenario["state"] == "incomplete"
        and scenario["evidence"]["reason"] == "child_scenario_definition_changed"
    )
    observed = (await subject.execute(ctx, "cancel", {"run_id": report["run_id"]}))["data"]
    assert not observed["native_settled"] and len(jobs) == 1
    assert all(call.args[3].get("action") != "cancel" for call in native.await_args_list)


@pytest.mark.parametrize("witness", ["matching", "foreign", "missing"])
@pytest.mark.asyncio
async def test_prestart_barrier_requires_matching_no_job_witness(mapped, child, witness):
    subject, ctx, native, window, jobs, _ = mapped
    dispatch = native.side_effect

    async def refused(*args, **kwargs):
        params = args[3]
        if args[2] == "manage_play_scenario" and params["action"] == "run":
            data = (
                {
                    "scenario_job_created": False,
                    "requested_job_id": params["job_id"] if witness == "matching" else "other",
                }
                if witness != "missing"
                else {}
            )
            return {"success": False, "error": "native_job_active", "data": data}
        return await dispatch(*args, **kwargs)

    native.side_effect = refused
    for _attempt in range(3 if witness == "matching" else 1):
        report = (await subject.execute(ctx, "run", {**OPTIONS, "wait_seconds": 5}))["data"]
        assert report["native_settled"] == (witness == "matching")
        assert report["state"] == "incomplete"
        assert any(cmd == "run_tests" for cmd, _ in window.calls) == (witness == "matching")
    assert not jobs


@pytest.mark.asyncio
async def test_frozen_historical_player_never_dispatches_live_editor_checks(
    registered, mapped, child
):
    subject, ctx, native, window, jobs, _ = mapped
    _, script, _ = registered
    script.write_text("class Wind { bool historical; }", encoding="utf-8")
    report = (
        await subject.execute(ctx, "run", {**OPTIONS, "source_mode": "frozen", "wait_seconds": 5})
    )["data"]
    assert report["state"] == "incomplete"
    assert not jobs and not window.calls and len(child[1]) == 1
    rows = report["selected_check_results"]
    assert all(
        r["evidence"]["reason"] == "historical_candidate_unavailable_in_editor"
        for r in rows
        if r["id"] != "wind-cycle"
    )


@pytest.mark.asyncio
async def test_broader_profile_uses_settings_frozen_at_preparation(registered, mapped, child):
    subject, ctx, native, window, jobs, document = mapped
    project, directory, candidate, paths = setup_inventory(registered, 257)
    candidate["broader_check"] = "project-wide"
    publish_index(
        project, directory, candidate, [paths[n : n + 128] for n in range(0, len(paths), 128)]
    )
    document["profiles"]["project-wide"] = {
        "analysis_required": False,
        "refresh_policy": "never",
        "impact": "off",
        "test_mode": "EditMode",
        "assembly_names": ["ProjectTests"],
    }
    prepared = await subject.execute(
        ctx, "prepare", {"candidate_id": "large", "execute_selected_checks": True}
    )
    document["profiles"]["project-wide"]["assembly_names"] = ["OtherTests"]
    started = (
        await subject.execute(ctx, "run", {"preparation_id": prepared["data"]["preparation_id"]})
    )["data"]
    await asyncio.wait_for(subject.tasks[started["run_id"]], 60)
    report = (await subject.execute(ctx, "report", {"run_id": started["run_id"]}))["data"]
    assert report["state"] == "passed"
    (row,) = report["selected_check_results"]
    assert row["kind"] == "profile" and row["state"] == "passed"
    params = next(p for command, p in window.calls if command == "run_tests")
    assert params["assemblyNames"] == ["ProjectTests"]
    definition = {"profile": prepared["data"]["broader_profile"], "selection": {}}

    def fingerprint(value):
        return sha256(
            json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
                "utf-8"
            )
        ).hexdigest()

    digest = fingerprint(definition)
    assert row["evidence"]["definition_hash"] == digest
    assert row["evidence"]["candidate_source_revision"] == candidate["source_revision"]
    (execution,) = [c for c in report["executed_checks"] if c["kind"] == "profile"]
    assert execution["definition_hash"] == digest
    (native_job,) = [c for c in report["children"] if c.get("check_id") == row["id"]]
    assert native_job["check_kind"] == "profile" and native_job["definition_hash"] == digest
    assert execution["child_job_ids"] == row["evidence"]["child_job_ids"] == [native_job["job_id"]]
    assert digest != fingerprint(
        {**definition, "profile": {**definition["profile"], "assembly_names": ["OtherTests"]}}
    )


@pytest.mark.asyncio
async def test_cancel_waits_for_matching_scenario_release_and_skips_next_check(mapped, child):
    subject, ctx, native, window, jobs, _ = mapped
    dispatch = native.side_effect
    observing, release = asyncio.Event(), asyncio.Event()
    canceled = []

    async def block(*args, **kwargs):
        params = args[3]
        if args[2] == "manage_play_scenario" and params["action"] == "status" and not canceled:
            observing.set()
            await release.wait()
            return {"success": True, "data": jobs[params["job_id"]].copy()}
        if args[2] == "manage_play_scenario" and params["action"] == "cancel":
            canceled.append(params["job_id"])
        result = await dispatch(*args, **kwargs)
        if args[2] == "manage_play_scenario" and params["action"] == "status":
            result["data"]["status"] = "cancelled"
        return result

    native.side_effect = block
    started = (await subject.execute(ctx, "run", OPTIONS))["data"]
    await asyncio.wait_for(observing.wait(), 3)
    await subject.execute(ctx, "cancel", {"run_id": started["run_id"]})
    release.set()
    await asyncio.wait_for(subject.tasks[started["run_id"]], 3)
    report = (await subject.execute(ctx, "report", {"run_id": started["run_id"]}))["data"]
    assert report["state"] == "canceled" and report["native_settled"]
    assert canceled == list(jobs) and len(jobs) == 1 and not window.calls
