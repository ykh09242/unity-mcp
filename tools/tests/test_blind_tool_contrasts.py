"""Paired contrast cases require witnessed facts and exactly the requested fixture effects."""

import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.experiments import blind_tool_contrasts as cases


@pytest.mark.parametrize(
    ("task", "route", "args", "answer"),
    [
        ("compile-status", cases.EDITOR_STATE, {}, False),
        ("compile-start", "refresh_unity", {"compile": "request", "scope": "scripts"}, True),
        ("graph-saved", "inspect_shader_graph", {"path": cases.GRAPH_FILE}, 3),
        ("graph-open", "manage_shader.inspect_graph", {"path": cases.GRAPH_FILE}, None),
        ("ui-inspect", cases.RAYCAST, {"target": "Canvas", "screen_position": [320, 180]}, "Start"),
        ("ui-click", "manage_input.ui_click", {"target": cases.BUTTON}, True),
    ],
)
def test_each_contrast_requires_its_outcome(tmp_path, task, route, args, answer):
    db = cases.connect(tmp_path / "fixture.sqlite")
    try:
        assert cases.observe_fixture(db, route, args)["success"]
        rows = db.execute("SELECT * FROM calls").fetchall()
        assert cases.judge(task, rows, {"value": answer})["passed"]
        if task in {"graph-open", "ui-inspect", "compile-status"}:
            cases.observe_fixture(db, "refresh_unity", {"compile": "request"})
            assert not cases.judge(
                task, db.execute("SELECT * FROM calls").fetchall(), {"value": answer}
            )["passed"]
        if task == "graph-open":
            assert cases.judge(task, rows, {"value": 3})["unsupported_claim"]
        if task == "ui-click":
            cases.observe_fixture(db, route, args)
            assert not cases.judge(
                task, db.execute("SELECT * FROM calls").fetchall(), {"value": answer}
            )["passed"]
    finally:
        db.close()


@pytest.mark.asyncio
async def test_fixture_advertises_real_tools_and_never_forwards_native_commands(tmp_path):
    from fastmcp import Client
    from fastmcp.client.transports import StdioTransport

    journal = tmp_path / "fixture.sqlite"
    transport = StdioTransport(
        command=sys.executable,
        args=["-X", "utf8", str(Path(cases.__file__)), "serve", "--journal", str(journal)],
    )
    async with Client(transport) as client:
        assert {"manage_input", "refresh_unity", "manage_tools"} <= {
            tool.name for tool in await client.list_tools()
        }
        click = await client.call_tool(
            "manage_input", {"action": "ui_click", "target": cases.BUTTON}
        )
        assert click.structured_content["data"]["dispatched"]
        observe = await client.read_resource(cases.EDITOR_STATE)
        assert json.loads(observe[0].text)["data"]["compilation"]["is_compiling"] is False
        unrelated = await client.call_tool("manage_editor", {"action": "play"})
        assert unrelated.structured_content["success"] is False
    db = cases.connect(journal)
    try:
        assert db.execute("SELECT effect FROM calls WHERE effect IS NOT NULL").fetchall() == [
            ("click",)
        ]
    finally:
        db.close()


def test_unrelated_discovery_cannot_establish_graph_window_limitation():
    calls = [("manage_tools.search", None, "null", None)]
    assert not cases.judge("graph-open", calls, {"value": None})["passed"]
    assert not cases.judge("compile-start", [], {"value": False}, approval_blocks=1)[
        "unsupported_claim"
    ]
