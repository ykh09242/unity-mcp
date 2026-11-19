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


def test_relative_read_in_a_different_project_cannot_supply_asset_evidence():
    assert shell_purpose("cat " + ASSET, ASSET, "E:/Game", "E:/Other") == "other_file_read"
    assert (
        shell_purpose("cat A.shadergraph", ASSET, "E:/Game", "E:/Game/Assets") == "named_asset_read"
    )


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


@pytest.mark.parametrize("projected", [True, False])
@pytest.mark.parametrize(
    ("cwd", "operand", "complete"),
    [
        ("E:/Game/Assets", "A.shadergraph", 1),
        ("E:/Other", ASSET, 0),
        (None, ASSET, 0),
    ],
)
def test_host_and_raw_traces_agree_on_resolved_file_identity(
    tmp_path, projected, cwd, operand, complete
):
    output = "\n".join(
        map(
            json.dumps,
            [
                {"m_Nodes": [{"m_Id": "a"}, {"m_Id": "b"}], "m_Edges": []},
                {"m_ObjectId": "a", "m_Type": "First"},
                {"m_ObjectId": "b", "m_Type": "Second"},
            ],
        )
    )
    command = "cat " + operand
    item = (
        project_item(
            {
                "id": "x",
                "type": "commandExecution",
                "command": command,
                "cwd": cwd,
                "exitCode": 0,
                "aggregatedOutput": output,
            },
            "trial",
            ASSET,
            "E:/Game",
        )
        if projected
        else {
            "id": "x",
            "type": "command_execution",
            "command": command,
            "cwd": cwd,
            "exit_code": 0,
            "aggregated_output": output,
        }
    )
    path = tmp_path / "events.jsonl"
    path.write_text(json.dumps({"type": "item.completed", "item": item}))
    result = client_trace(path, ASSET, endpoint=False, project="E:/Game")
    assert result.saved_reads == complete
    assert len(result.saved_observations) == complete
