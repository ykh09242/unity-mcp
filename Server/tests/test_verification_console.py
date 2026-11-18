from copy import deepcopy

import pytest

from services.verification.profiles import builtins
from services.verification.runner import VerificationRunner
from .test_verification_runner import IO


class WindowIO(IO):
    def __init__(self):
        super().__init__()
        self.receipt = {"window_id": "a" * 32, "generation": "b" * 32}
        self.observed = {
            **self.receipt,
            "job_id": self.tests["job_id"],
            "end": "settled",
            "observed_error_count": 0,
            "findings": [],
            "messages_truncated": False,
        }

    async def native(self, command, params, deadline):
        if command != "tail_console":
            return await super().native(command, params, deadline)
        self.calls.append((command, deepcopy(params)))
        data = self.receipt if params["verification_window"] == "begin" else self.observed
        return {"success": True, "data": deepcopy(data)}


async def profile_run(io, profile="editmode-check"):
    runner = VerificationRunner(io, builtins()[profile], {"paths": ["Assets/C.cs"]})
    return await runner.run()


@pytest.mark.asyncio
@pytest.mark.parametrize("profile", ["editmode-check", "playmode-check"])
async def test_clean_owned_interval_passes_both_builtin_test_profiles(profile):
    io = WindowIO()
    report = await profile_run(io, profile)
    native = [
        (name, params)
        for name, params in io.calls
        if name in {"tail_console", "run_tests", "get_test_job"}
    ]
    assert [name for name, _ in native] == [
        "tail_console",
        "run_tests",
        "get_test_job",
        "tail_console",
    ]
    assert native[0][1]["verification_window"] == "begin"
    assert native[1][1]["console_window_id"] == io.receipt["window_id"]
    assert native[3][1] == {
        "verification_window": "read",
        "window_id": io.receipt["window_id"],
        "job_id": io.tests["job_id"],
    }
    console = next(stage for stage in report["stages"] if stage["name"] == "console")
    assert console["evidence"]["coverage"] == "complete"
    assert report["state"] == "passed"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change",
    [
        {"generation": "c" * 32},
        {"window_id": "c" * 32},
        {"job_id": "other"},
        {"end": "expired"},
        {"end": "running"},
        {"end": "generation_changed"},
        {"observed_error_count": False},
        {"findings": None},
        {"observed_error_count": 1},
    ],
)
async def test_incomplete_or_foreign_proof_cannot_pass(change):
    io = WindowIO()
    io.observed.update(change)
    report = await profile_run(io)
    assert report["state"] == "incomplete"
    assert io.calls[-2] == (
        "tail_console",
        {"verification_window": "discard", "window_id": io.receipt["window_id"]},
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("count,end", [(1, "settled"), (1, "expired"), (25, "settled")])
async def test_observed_errors_fail_even_with_partial_coverage(count, end):
    io = WindowIO()
    io.observed.update(
        end=end,
        observed_error_count=count,
        findings=[{"type": "Exception", "message": "observed error"}] * min(count, 12),
    )
    report = await profile_run(io)
    console = next(stage for stage in report["stages"] if stage["name"] == "console")
    assert report["state"] == "failed"
    assert console["evidence"]["observed_error_count"] == count
    assert console["evidence"]["coverage"] == (
        "complete" if count <= 12 and end == "settled" else "unknown"
    )


@pytest.mark.asyncio
async def test_dispatch_timeout_releases_console_subscription_without_replay():
    io = WindowIO()
    io.test_timeout = True
    report = await profile_run(io)
    assert report["native_settled"] is False
    assert sum(name == "run_tests" for name, _ in io.calls) == 1
    assert io.calls[-1][1]["verification_window"] == "discard"


@pytest.mark.asyncio
async def test_cancel_between_checkpoint_and_dispatch_releases_the_listener():
    io = WindowIO()
    runner = VerificationRunner(io, builtins()["playmode-check"], {"paths": ["Assets/C.cs"]})
    native = io.native

    async def cancel_at_checkpoint(command, params, deadline):
        response = await native(command, params, deadline)
        if params.get("verification_window") == "begin":
            runner.cancel_requested = True
        return response

    io.native = cancel_at_checkpoint
    report = await runner.run()
    assert report["state"] == "canceled"
    assert not any(name == "run_tests" for name, _ in io.calls)
    assert io.calls[-1][1]["verification_window"] == "discard"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "items,state",
    [([], "incomplete"), ([{"entry": {"type": "Error", "message": "failed"}}], "failed")],
)
async def test_older_peer_errors_still_fail_and_an_empty_retained_tail_is_incomplete(items, state):
    io = IO()
    original = io.native

    async def legacy(command, params, deadline):
        reply = await original(command, params, deadline)
        if command == "tail_console" and params["tail_cursor"] != "begin":
            reply["data"]["items"] = items
        return reply

    io.native = legacy
    report = await profile_run(io)
    assert report["state"] == state
    assert "console_window_id" not in next(p for name, p in io.calls if name == "run_tests")
