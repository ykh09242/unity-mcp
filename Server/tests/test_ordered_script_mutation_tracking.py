"""Ordered edits publish one durable graph receipt for their sole native commit."""

import asyncio
import copy
from unittest.mock import AsyncMock

import pytest

from services.reference_graph.mutation_tracking import target_paths
from tests.test_reference_graph_mutations import service_graph
from tests.test_script_preparation import OrderedWire, ordered_edits, tools


@pytest.fixture
def ordered_graph_wire(service_graph, monkeypatch):
    service, _, ctx, entry = service_graph
    native = OrderedWire()
    events = []
    behavior = {"commit_response": None, "preparation_delay": 0, "finish_delay": 0}
    execute = service.execute
    resolve = service.resolve

    async def graph_resolve(context, action, *args, **kwargs):
        if action == "finish_mutation" and behavior["finish_delay"]:
            await asyncio.sleep(behavior["finish_delay"])
        return await resolve(context, action, *args, **kwargs)

    async def graph_execute(context, action, options):
        if action == "begin_mutation":
            events.append(("begin", copy.deepcopy(options)))
            if behavior["preparation_delay"]:
                await asyncio.sleep(behavior["preparation_delay"])
        return await execute(context, action, options)

    async def send(sender, instance, command, params, **kwargs):
        assert instance == ctx.instance
        response = await native.send(sender, "Unity@abc", command, params, **kwargs)
        events.append((params["action"], None))
        if params["action"] == "apply_prepared_script":
            assert kwargs.get("retry_on_reload") is False
            if behavior["commit_response"] is not None:
                return copy.deepcopy(behavior["commit_response"])
        return response

    monkeypatch.setattr(service, "execute", graph_execute)
    monkeypatch.setattr(service, "resolve", graph_resolve)
    monkeypatch.setattr(
        tools, "get_unity_instance_from_context", AsyncMock(return_value=ctx.instance)
    )
    monkeypatch.setattr(tools, "send_with_unity_instance", send)
    return ctx, entry, native, events, behavior


async def invoke(wire, edits, options):
    ctx, _, _, _, _ = wire
    return await tools.script_apply_edits(ctx, "Foo", "Assets", edits, options)


async def preview(wire, edits=None):
    return await invoke(
        wire,
        ordered_edits() if edits is None else edits,
        {"mixed_order": "request", "preview": True},
    )


def apply_options(proposal):
    return {
        "mixed_order": "request",
        "precondition_bytes_sha256": proposal["data"]["original_bytes_sha256"],
        "candidate_bytes_sha256": proposal["data"]["candidate_bytes_sha256"],
    }


def test_prepared_script_action_has_automatic_target():
    assert target_paths(
        "manage_script", {"action": "apply_prepared_script", "name": "Foo", "path": "Assets"}
    ) == ["Assets/Foo.cs"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response,expected",
    [
        (None, "accepted"),
        ({"success": True}, "unknown"),
        ({"success": False, "data": {"applied": False}}, "failed"),
    ],
)
async def test_ordered_commit_records_real_receipt_once(ordered_graph_wire, response, expected):
    _, entry, native, events, behavior = ordered_graph_wire
    proposal = await preview(ordered_graph_wire)
    assert proposal["success"] is True, proposal
    assert not any(event[0] == "begin" for event in events)
    events.clear()
    behavior["commit_response"] = response
    result = await invoke(ordered_graph_wire, ordered_edits(), apply_options(proposal))
    diagnostic = result["data"]["reference_graph"]
    receipt = entry.workspace.mutations().read(diagnostic["id"])
    assert receipt["outcome"] == diagnostic["outcome"] == expected
    assert receipt["state"] == "pending" and receipt["targets"] == 1
    assert len(native.commits) == 1 and not native.legacy_writes
    assert [event for event in events if event[0] == "begin"] == [
        ("begin", {"paths": ["Assets/Foo.cs"]})
    ]
    assert [event[0] for event in events][-2:] == ["begin", "apply_prepared_script"]
    if expected == "unknown":
        assert result["success"] is False and result["data"]["outcome"] == "unknown"


@pytest.mark.asyncio
async def test_ordered_preview_and_noop_do_not_prepare_receipt(ordered_graph_wire):
    edits = [
        {"op": "replace_method", "methodName": "Existing", "replacement": "void Existing() {}"},
        {"op": "regex_replace", "pattern": "absent", "replacement": "unused"},
    ]
    proposal = await preview(ordered_graph_wire, edits)
    assert proposal["success"] is True and proposal["data"]["no_op"] is True
    result = await invoke(ordered_graph_wire, edits, apply_options(proposal))
    assert result["success"] is True and result["data"]["applied"] is False
    assert "reference_graph" not in result["data"]
    _, _, native, events, _ = ordered_graph_wire
    assert not native.commits and not any(event[0] == "begin" for event in events)


@pytest.mark.asyncio
async def test_graph_preparation_consumes_original_ordered_deadline(
    ordered_graph_wire, monkeypatch
):
    proposal = await preview(ordered_graph_wire)
    assert proposal["success"] is True, proposal
    _, _, native, _, behavior = ordered_graph_wire
    behavior["preparation_delay"] = 1
    monkeypatch.setattr(tools.config, "command_total_timeout", 0.05)
    result = await invoke(ordered_graph_wire, ordered_edits(), apply_options(proposal))
    assert result["success"] is False and result["code"] == "deadline_exceeded", result
    assert not native.commits


@pytest.mark.asyncio
async def test_known_commit_survives_deadline_during_receipt_finish(
    ordered_graph_wire, monkeypatch
):
    proposal = await preview(ordered_graph_wire)
    assert proposal["success"] is True, proposal
    _, _, native, _, behavior = ordered_graph_wire
    behavior["finish_delay"] = 2
    monkeypatch.setattr(tools.config, "command_total_timeout", 1)
    result = await invoke(ordered_graph_wire, ordered_edits(), apply_options(proposal))
    assert result["success"] is True and result["data"]["applied"] is True, result
    assert result["data"]["bytes_sha256"] == proposal["data"]["candidate_bytes_sha256"]
    assert len(native.commits) == 1


@pytest.mark.asyncio
async def test_known_refusal_survives_deadline_during_receipt_finish(
    ordered_graph_wire, monkeypatch
):
    proposal = await preview(ordered_graph_wire)
    assert proposal["success"] is True, proposal
    _, _, native, _, behavior = ordered_graph_wire
    refusal = {
        "success": False,
        "code": "stale_file",
        "message": "Original raw bytes no longer match",
        "data": {"applied": False, "source_saved": False, "retry_safe": True},
    }
    behavior["commit_response"] = refusal
    behavior["finish_delay"] = 2
    monkeypatch.setattr(tools.config, "command_total_timeout", 1)
    result = await invoke(ordered_graph_wire, ordered_edits(), apply_options(proposal))
    assert result == refusal
    assert len(native.commits) == 1
