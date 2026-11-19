import json

import pytest

from tools.experiments.blind_tool_host import project_item
from tools.experiments.blind_tool_shell import shell_purpose
from tools.experiments.blind_tool_trace import client_trace


ASSET = "Assets/A.shadergraph"


@pytest.mark.parametrize(
    ("command", "purpose"),
    [
        ("Get-Content -Raw -LiteralPath AGENTS.md", "instruction_read"),
        ("bash -lc 'cat guides/SKILL.md'", "instruction_read"),
        ("pwd", "working_directory"),
        (r"Get-Content -LiteralPath E:\Game\Assets\A.shadergraph", "named_asset_read"),
        ("cat Assets/B.shadergraph", "other_file_read"),
        ("cat Assets/../AGENTS.md", "unknown"),
        ("Get-Content AGENTS.md | Select-Object -First 1", "unknown"),
        ("cat AGENTS.md; cat Assets/A.shadergraph", "unknown"),
        ("cat AGENTS.md Assets/A.shadergraph", "unknown"),
        ("Get-Content -Tail 3 Assets/A.shadergraph", "unknown"),
        ("python read.py Assets/A.shadergraph", "unknown"),
    ],
)
def test_classify_only_an_unambiguous_literal_command(command, purpose):
    assert shell_purpose(command, ASSET, "E:/Game") == purpose


def test_project_only_returned_fields_and_bind_to_named_asset_and_actor(tmp_path):
    root = json.dumps({"m_Nodes": [{"m_Id": "One"}], "m_Edges": []})
    item = project_item(
        {
            "id": "r",
            "type": "commandExecution",
            "status": "completed",
            "exitCode": 0,
            "command": "cat " + ASSET,
            "aggregatedOutput": root + "\ntruncated stdout SECRET",
        },
        "unityMCP",
        ASSET,
    )
    assert "SECRET" not in json.dumps(item) and ASSET not in json.dumps(item)
    path = tmp_path / "events.jsonl"
    path.write_text(json.dumps({"type": "item.completed", "host_actor": "child", "item": item}))
    read = client_trace(path, ASSET, endpoint=False, expected_actor="child")
    assert read.saved_reads == 0 and read.saved_observations[0]["nodes"] == 1
    assert not client_trace(path, ASSET, endpoint=False, expected_actor="parent").saved_observations
    assert not client_trace(
        path, "Assets/B.shadergraph", endpoint=False, expected_actor="child"
    ).saved_observations
