from copy import deepcopy
import asyncio
from time import monotonic

import pytest

from models.verification import VerificationProfile
from services.verification.runner import VerificationRunner


def freshness(status="observed_current", token="a" * 64):
    return {
        "status": status,
        "source_token": token,
        "project_id": "p",
        "session_id": "s",
        "compile_id": "c" * 32,
        "coverage": {"complete": True},
    }


class IO:
    def __init__(self):
        self.calls = []
        self.observations = [freshness(), freshness(), freshness()]
        self.analysis = {
            "complete": True,
            "stale": False,
            "source_token": "a" * 64,
            "classification_complete": False,
            "rows": [],
            "next_cursor": None,
            "providers": {"Unity": "1"},
        }
        self.tests = {
            "job_id": "t" * 32,
            "status": "succeeded",
            "completion_kind": "completed",
            "native_control": {
                "version": 1,
                "job_id": "t" * 32,
                "released": True,
                "termination": "confirmed",
                "final_witness": True,
                "native_invoked": True,
                "native_guid": "6e764d23-6f6c-470a-abe9-1d2db576e3a4",
            },
            "result": {"summary": {"total": 1, "passed": 1, "failed": 0, "skipped": 0}},
        }
        self.deny = False
        self.test_timeout = False
        self.cancel_requested = False

    async def check(self):
        if self.deny:
            raise PermissionError("revoked")

    async def authorize_profile(self, profile):
        self.calls.append(("authorize", profile.test_mode))
        await self.check()

    async def freshness(self, policy, deadline):
        self.calls.append(("freshness", policy))
        return self.observations.pop(0)

    async def quality(self, action, options, deadline):
        self.calls.append(("quality", action))
        if action == "analyze":
            return {"success": True, "data": {"job_id": "q" * 32, "state": "queued"}}
        if action == "status":
            return {
                "success": True,
                "data": {
                    "job_id": "q" * 32,
                    "state": "completed",
                    "terminal": True,
                    "result": deepcopy(self.analysis),
                },
            }
        return {
            "success": True,
            "data": {"complete": True, "findings": [], "next_cursor": None, "coverage_gaps": []},
        }

    async def native(self, command, params, deadline):
        self.calls.append((command, deepcopy(params)))
        if command == "run_tests":
            if self.test_timeout:
                raise TimeoutError()
            return {"success": True, "data": {"job_id": "t" * 32, "status": "running"}}
        if command == "get_test_job":
            return {"success": True, "data": deepcopy(self.tests)}
        if command == "tail_console":
            return {
                "success": True,
                "data": {
                    "generation": "console",
                    "next_cursor": "cursor",
                    "items": [],
                    "loss_unknown": True,
                    "has_more": False,
                },
            }
        return {"success": True, "data": {}}


async def run(io, **profile):
    runner = VerificationRunner(io, VerificationProfile(**profile), {"paths": ["Assets/C.cs"]})
    return await runner.run()


@pytest.mark.asyncio
async def test_script_profile_keeps_source_provenance_without_formatting_or_test_execution():
    io = IO()
    result = await run(io)
    assert result["state"] == "passed"
    assert result["source_token"] == "a" * 64
    assert not any(name in {"run_tests", "format", "clear"} for name, _ in io.calls)


@pytest.mark.asyncio
async def test_compile_failure_still_analyzes_but_never_dispatches_tests():
    io = IO()
    io.observations = [freshness("compile_failed"), freshness("compile_failed")]
    result = await run(io, test_mode="PlayMode")
    assert ("quality", "analyze") in io.calls
    assert not any(name == "run_tests" for name, _ in io.calls)
    assert result["state"] == "failed"


@pytest.mark.asyncio
async def test_drift_at_pretest_observation_refuses_native_test_dispatch():
    io = IO()
    io.observations = [freshness(), freshness(token="b" * 64), freshness(token="b" * 64)]
    result = await run(io, test_mode="EditMode")
    assert result["state"] == "stale"
    assert not any(name == "run_tests" for name, _ in io.calls)


@pytest.mark.asyncio
async def test_source_change_during_passing_tests_makes_overall_run_stale():
    io = IO()
    io.observations[-1] = freshness(token="b" * 64)
    result = await run(io, test_mode="EditMode")
    assert result["state"] == "stale"
    assert (
        next(stage for stage in result["stages"] if stage["name"] == "tests")["state"] == "passed"
    )
    assert (
        next(params for name, params in io.calls if name == "run_tests")["source_token"] == "a" * 64
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change", [{"complete": False}, {"stale": True}, {"classification_complete": False}]
)
async def test_partial_or_unclassified_analysis_cannot_pass_required_policy(change):
    io = IO()
    io.analysis.update(change)
    result = await run(io, diagnostic_scope="introduced")
    assert result["state"] in {"incomplete", "stale"}


@pytest.mark.asyncio
async def test_zero_tests_and_unknown_console_are_incomplete():
    io = IO()
    io.tests["result"]["summary"].update(total=0, passed=0)
    result = await run(io, test_mode="EditMode")
    assert result["state"] == "incomplete"
    assert (
        next(stage for stage in result["stages"] if stage["name"] == "console")["state"]
        == "incomplete"
    )


@pytest.mark.asyncio
async def test_dispatch_timeout_is_never_replayed_or_claimed_settled():
    io = IO()
    io.test_timeout = True
    result = await run(io, test_mode="PlayMode")
    assert result["state"] == "incomplete"
    assert result["native_settled"] is False
    assert sum(name == "run_tests" for name, _ in io.calls) == 1


@pytest.mark.asyncio
async def test_permission_revocation_before_analysis_blocks_all_dependent_dispatch():
    io = IO()
    original = io.freshness

    async def revoke(policy, deadline):
        value = await original(policy, deadline)
        io.deny = True
        return value

    io.freshness = revoke
    result = await run(io, test_mode="EditMode")
    assert result["state"] == "incomplete"
    assert not any(name in {"quality", "run_tests"} for name, _ in io.calls)


@pytest.mark.asyncio
async def test_missing_analyzer_is_incomplete_without_a_phantom_native_owner():
    io = IO()
    original = io.quality

    async def unavailable(action, options, deadline):
        if action == "analyze":
            return {"success": False, "settled": True, "error": "analyzer_unavailable"}
        return await original(action, options, deadline)

    io.quality = unavailable
    result = await run(io)
    assert result["state"] == "incomplete" and result["native_settled"]


@pytest.mark.asyncio
async def test_uncertain_refresh_remains_owned_even_when_diagnostics_finish():
    io = IO()
    io.observations[0] = {
        **freshness("unavailable"),
        "refresh_dispatches": [{"compile_id": "d" * 32, "settled": False}],
    }
    result = await run(io)
    assert result["native_settled"] is False
    assert any(
        child["kind"] == "compile" and child["job_id"] == "d" * 32 for child in result["children"]
    )


@pytest.mark.asyncio
async def test_resolved_and_baseline_only_rows_are_never_active_policy_failures():
    io = IO()
    io.analysis["rows"] = [
        {"scope": "selected", "classification": "resolved", "diagnostic": {"severity": "error"}},
        {
            "scope": "selected",
            "classification": "unclassified",
            "observation": "baseline",
            "diagnostic": {"severity": "error"},
        },
        {"scope": "context", "classification": "context", "diagnostic": {"severity": "warning"}},
    ]
    assert (await run(io, fail_on=["error", "warning"]))["state"] == "passed"


@pytest.mark.asyncio
async def test_authority_observation_cannot_extend_the_overall_deadline():
    io = IO()

    async def wait_forever(profile):
        await asyncio.Event().wait()

    io.authorize_profile = wait_forever
    runner = VerificationRunner(io, VerificationProfile(), {"paths": ["Assets/C.cs"]})
    runner.deadline = monotonic() + 0.05
    result = await asyncio.wait_for(runner.run(), 1)
    assert result["state"] == "incomplete"
