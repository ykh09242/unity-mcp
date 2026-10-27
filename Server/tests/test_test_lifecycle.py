"""Negative controls for finite fixture progress and producer-aware predicates."""

import asyncio

import pytest


@pytest.mark.parametrize("continuation", ["same cursor", 4])
def test_fixture_progress_rejects_repeated_continuation(continuation):
    from tests.lifecycle import FixtureProgress

    progress = FixtureProgress(20, "fixture pages")
    progress.step(continuation)
    with pytest.raises(AssertionError, match="repeated continuation"):
        progress.step(continuation)


def test_fixture_progress_bounds_changing_cursors_and_cleanup_without_cursor():
    from tests.lifecycle import FixtureProgress

    for tokens in (["one", "two", "three"], [None, None, None]):
        progress = FixtureProgress(2, "fixture cleanup")
        progress.step(tokens[0])
        progress.step(tokens[1])
        with pytest.raises(AssertionError, match="exceeded 2 steps"):
            progress.step(tokens[2])


@pytest.mark.asyncio
async def test_predicate_observation_surfaces_failed_producer_and_drains_own_task():
    from tests.lifecycle import await_condition

    async def fail():
        raise RuntimeError("producer failed before condition")

    task = asyncio.create_task(fail())
    try:
        before = asyncio.all_tasks()
        with pytest.raises(RuntimeError, match="producer failed"):
            await await_condition(lambda: False, "fixture ready", producers=(task,))
        assert asyncio.all_tasks() == before - {task}
    finally:
        await asyncio.gather(task, return_exceptions=True)
