"""Role pilots must prove ownership and the controls used to compare actors."""

import asyncio
from copy import deepcopy
import json
from pathlib import Path
import sys
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.experiments.blind_tool_host import observed_permissions, scoped_children
from tools.experiments.blind_tool_preflight import pair_controls, pilot_reasons
from tools.experiments.blind_tool_trace import wire_checkpoint, wire_trace


def test_evaluator_and_actor_can_read_the_same_resource(tmp_path):
    wire = tmp_path / "mcp-01.jsonl"

    def read(number):
        with wire.open("a", encoding="utf-8") as stream:
            for item in (
                {"event": "request", "route": "mcpforunity://project/info", "channel": "unity"},
                {
                    "event": "response",
                    "outcome": "returned",
                    "observation": {"project_fields": True},
                },
            ):
                stream.write(json.dumps({**item, "sequence": number}) + "\n")

    read(1)
    start = wire_checkpoint(tmp_path)
    read(2)
    end = wire_checkpoint(tmp_path)
    read(3)
    assert [
        call.order for call in wire_trace(wire, after=start[wire.name], through=end[wire.name])
    ] == [2]


def test_fallback_only_queries_and_accepts_exact_direct_children():
    parent, child, other = (str(uuid4()) for _ in range(3))

    class Host:
        def __init__(self, rows, cursor=None):
            self.rows = rows
            self.cursor = cursor

        async def request(self, method, parameters):
            assert method == "thread/list"
            assert parameters == {
                "parentThreadId": parent,
                "sourceKinds": ["subAgentThreadSpawn"],
                "limit": 4,
                "useStateDbOnly": True,
            }
            return {"data": self.rows, "nextCursor": self.cursor}

    linked = {"id": child, "parentThreadId": parent}
    assert asyncio.run(scoped_children(Host([linked]), parent)) == ({child}, "parent_linked")
    assert asyncio.run(scoped_children(Host([]), parent)) == (set(), "no_link")
    assert asyncio.run(scoped_children(Host([linked, {"id": other}]), parent))[0] == set()
    assert asyncio.run(scoped_children(Host([linked], "more"), parent))[0] == set()
    assert asyncio.run(scoped_children(Host([]), ""))[1] == "invalid_parent"


def test_pilot_grades_capture_independently_of_tool_choice_and_rejects_parent_or_drift():
    permissions = observed_permissions({"approvalPolicy": "never", "sandbox": {"type": "readOnly"}})
    left = {
        "role": "subagent",
        "role_status": "confirmed",
        "turn_completed": True,
        "runner_status": "finished",
        "first_business_call": "unrelated.tool",
        "actor_receipt": {"children": ["abc"]},
        "approval_policy": "never",
        "editor_start_state": {"scene": "A"},
        "editor_end_state": {"scene": "A"},
        "actor_conditions": {
            "host_binary_digest": "executable",
            "namespace": "trial",
            "permissions": permissions,
            "server_commit": "a",
            "tool_schema_digest": "s",
            "competitor_digest": "same",
            "project_verified": True,
            "inventory_status": "observed",
        },
    }
    right = deepcopy(left)
    right["actor_conditions"]["server_commit"] = "b"
    assert not pilot_reasons(left)
    assert not pair_controls(left, right)
    right["actor_receipt"]["children"] = []
    right["actor_conditions"]["permissions"] = None
    right["editor_start_state"] = {"scene": "B"}
    assert set(pilot_reasons(right)) >= {
        "child_link_not_verified",
        "actor_permissions_not_verified",
    }
    assert set(pair_controls(left, right)) >= {
        "unverified:actor_permissions",
        "different:editor_start_state",
    }
    assert observed_permissions({"approvalPolicy": "never"}) is None
