"""Ephemeral actor correlation must never accept a peer, a grandchild or a recycled ID."""

import asyncio
from pathlib import Path
import sys
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.experiments.blind_tool_child import child_permissions, verify_live_child


def test_live_child_reads_only_the_observed_actor_and_returns_its_settings():
    parent, session, actor = (str(uuid4()) for _ in range(3))
    row = {
        "id": actor,
        "parentThreadId": parent,
        "sessionId": session,
        "model": "gpt-6.2-sol",
        "reasoningEffort": "medium",
    }
    client = AsyncMock()
    client.request.return_value = {"thread": row}
    assert asyncio.run(verify_live_child(client, parent, session, actor)) == {
        "model": "gpt-6.2-sol",
        "effort": "medium",
    }
    client.request.assert_awaited_once_with(
        "thread/read", {"threadId": actor, "includeTurns": False}
    )


@pytest.mark.parametrize("field", ["id", "parentThreadId", "sessionId", "source"])
def test_missing_or_conflicting_identity_stays_unknown(field):
    parent, session, actor, other = (str(uuid4()) for _ in range(4))
    row = {"id": actor, "parentThreadId": parent, "sessionId": session}
    row[field] = (
        {"subAgent": {"thread_spawn": {"parent_thread_id": other}}} if field == "source" else other
    )
    client = AsyncMock()
    client.request.return_value = {"thread": row}
    assert asyncio.run(verify_live_child(client, parent, session, actor)) is None


def test_parent_and_opaque_digest_never_trigger_an_rpc():
    parent = str(uuid4())
    client = AsyncMock()
    for actor in (parent, "f" * 64):
        assert asyncio.run(verify_live_child(client, parent, parent, actor)) is None
    client.request.assert_not_awaited()


def test_only_the_delivered_developer_permission_block_supplies_a_child_grant():
    block = (
        "<permissions instructions>The `sandbox_mode` is `read-only`. "
        "Approval policy is currently never.</permissions instructions>"
    )
    item = {
        "type": "message",
        "role": "developer",
        "content": [{"type": "input_text", "text": block}],
    }
    evidence = child_permissions(item)
    assert evidence["approval"] == "never" and evidence["sandbox"] == "readOnly"
    assert evidence["source"] == "host_delivered_permissions_instructions"
    assert len(evidence["digest"]) == 64
    assert child_permissions({**item, "role": "assistant"}) is None
    assert child_permissions({**item, "role": "user"}) is None
    assert child_permissions({**item, "content": item["content"] * 2}) is None
    assert (
        child_permissions(
            {
                **item,
                "content": [
                    {"type": "input_text", "text": block.replace("read-only", "workspace-write")}
                ],
            }
        )
        is None
    )
