"""Shared immutable storage stays charged through the last detached delivery."""

import asyncio
import importlib

import pytest

from models.response_limits import ResponseOwner, response_owner, response_size
from services.tools.shared_read_budget import SharedReadBudget
from services.tools.shared_tool_reads import SharedToolReads


def large_response():
    return {"rows": [{"id": index, "label": "payload-" * 128} for index in range(256)]}


async def owned_fetch(read, factory, owner):
    token = response_owner.set(owner)
    try:
        return await read.fetch(factory)
    finally:
        response_owner.reset(token)


@pytest.mark.asyncio
async def test_shared_scalar_storage_allows_two_copies_and_outlives_source(monkeypatch):
    value = large_response()
    charge = response_size(value)
    budget = SharedReadBudget(max_bytes=int(charge * 2.8))
    monkeypatch.setattr(
        importlib.import_module("services.tools.shared_tool_reads"), "shared_read_budget", budget
    )
    owners = [ResponseOwner(), ResponseOwner()]
    calls = 0

    async def factory():
        nonlocal calls
        calls += 1
        await asyncio.sleep(0)
        return value

    try:
        async with SharedToolReads(freshness_s=1).session("large") as read:
            outputs = await asyncio.gather(
                *(owned_fetch(read, factory, owner) for owner in owners), return_exceptions=True
            )
        assert outputs == [value, value]
        assert calls == 1
        outputs[0]["rows"][0]["label"] = "changed"
        assert outputs[1] == value
        # The cache is closed, but scalar storage is still retained by deliveries.
        assert budget.retained_bytes >= charge
        owners[0].release()
        assert budget.retained_bytes >= charge
        owners[1].release()
        assert budget.retained_bytes == 0 and budget.entries == {}
    finally:
        for owner in owners:
            owner.release()


@pytest.mark.asyncio
async def test_copy_of_detached_delivery_keeps_shared_storage_until_final_release(monkeypatch):
    value = large_response()
    charge = response_size(value)
    budget = SharedReadBudget(max_bytes=int(charge * 2.8))
    monkeypatch.setattr(
        importlib.import_module("services.tools.shared_tool_reads"), "shared_read_budget", budget
    )
    owner = ResponseOwner()
    descendant = None

    async def factory():
        return value

    try:
        async with SharedToolReads(freshness_s=1).session("large") as read:
            assert await owned_fetch(read, factory, owner) == value
        descendant = owner.reserve_copy()
        assert descendant is not None
        owner.release()
        assert budget.retained_bytes >= charge
    finally:
        owner.release()
        if descendant is not None:
            descendant.release()
    assert budget.retained_bytes == 0 and budget.entries == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("shape", ["alias", "tuple", "custom_preparer"])
async def test_unsupported_copy_shapes_keep_full_admission(monkeypatch, shape):
    value = large_response()
    if shape == "alias":
        value["again"] = value["rows"]
    elif shape == "tuple":
        value["tuple"] = (1, 2)
    charge = response_size(value)
    budget = SharedReadBudget(max_bytes=int(charge * 2.8))
    monkeypatch.setattr(
        importlib.import_module("services.tools.shared_tool_reads"), "shared_read_budget", budget
    )
    owners = [ResponseOwner(), ResponseOwner()]
    calls = 0

    async def factory():
        nonlocal calls
        calls += 1
        await asyncio.sleep(0)
        return value

    def unsupported_preparer(_):
        raise AssertionError("Sparse requests must retain their existing copy path")

    reads = SharedToolReads(
        freshness_s=1, copy_preparer=unsupported_preparer if shape == "custom_preparer" else None
    )
    try:
        async with reads.session("unsupported") as read:
            outputs = await asyncio.gather(
                *(owned_fetch(read, factory, owner) for owner in owners), return_exceptions=True
            )
        assert (
            sum(
                isinstance(item, RuntimeError) and str(item) == "result_capacity"
                for item in outputs
            )
            == 1
        )
        assert sum(item == value for item in outputs) == 1 and calls == 1
    finally:
        for owner in owners:
            owner.release()
    assert budget.retained_bytes == 0 and budget.entries == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["copy", "released_consumer"])
async def test_discounted_copy_failure_releases_the_pin_and_reservation(monkeypatch, failure):
    value = large_response()
    budget = SharedReadBudget()
    module = importlib.import_module("services.tools.shared_tool_reads")
    monkeypatch.setattr(module, "shared_read_budget", budget)
    owner = ResponseOwner(released=failure == "released_consumer")

    async def factory():
        return value

    def fail_copy(_):
        raise RuntimeError("owned copy failure")

    if failure == "copy":
        monkeypatch.setattr(module, "deepcopy", fail_copy)
    try:
        async with SharedToolReads(freshness_s=1).session("failure") as read:
            with pytest.raises(RuntimeError, match="owned copy failure|result_capacity"):
                await owned_fetch(read, factory, owner)
    finally:
        owner.release()
    assert budget.retained_bytes == 0 and budget.entries == {}


def test_transformed_value_cannot_discount_a_smaller_source_reservation():
    from services.tools.shared_read_accounting import detached_copy_charge

    # A factory may add fields after its transport receives the original value.
    # Its smaller reservation is not evidence that the larger graph was sized.
    assert detached_copy_charge(large_response(), 256 * 1024) is None
