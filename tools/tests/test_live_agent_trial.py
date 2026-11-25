"""The unhinted trial preserves original delivery, case pairing and actor evidence."""

from io import StringIO
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.experiments import live_agent_trial as trial
from tools.experiments.live_agent_wire import Journal


def test_profile_pairs_have_identical_originals_and_counterbalanced_order():
    pairs = trial.pairs()
    assert len(pairs) == len({pair[0]["query"] for pair in pairs}) == 71
    assert len({row["id"] for pair in pairs for row in pair}) == 142
    assert sum(pair[0]["panel"] == "new" for pair in pairs) == 12
    for first, second in pairs:
        assert first["query"] == second["query"] and first["seed"] == second["seed"]
        assert {first["profile"], second["profile"]} == {"core", "full"}
        assert first["id"] != second["id"]
    assert 35 <= sum(pair[0]["profile"] == "core" for pair in pairs) <= 36


def test_actor_command_keeps_query_and_oracle_off_command_line(tmp_path):
    row = {"profile": "core", "task": "secret-scoring-task", "query": "各地的會議？"}
    cmd = trial.actor_command(
        "codex",
        tmp_path,
        tmp_path,
        Path("server"),
        "World@12345678",
        row,
        status_dir=tmp_path / "status",
    )
    assert cmd[-1] == "-" and "--ephemeral" in cmd and "--ignore-user-config" in cmd
    assert "--approve-for-me" in cmd and "never" not in cmd
    assert row["query"] not in str(cmd) and row["task"] not in str(cmd)
    assert not any("selection=agent" in arg for arg in cmd)


def test_journal_distinguishes_open_calls_errors_and_actual_resource_evidence(tmp_path):
    stream = StringIO()
    journal = Journal(stream)
    messages = [
        (True, {"id": 1, "method": "resources/read", "params": {"uri": "mcpforunity://example"}}),
        (
            True,
            {"id": 2, "method": "tools/call", "params": {"name": "subject", "arguments": {"a": 7}}},
        ),
        (False, {"id": 2, "result": {"content": [{"text": '{"success":false}'}]}}),
        (
            False,
            {"id": 1, "result": {"contents": [{"text": '{"value":32,"access_token":"private"}'}]}},
        ),
        (True, {"id": 3, "method": "tools/call", "params": {"name": "open"}}),
    ]
    for direction, msg in messages:
        journal.observe(json.dumps(msg).encode(), from_client=direction)
    (tmp_path / "wire-example.jsonl").write_text(stream.getvalue(), encoding="utf-8")
    calls, _ = trial.read_wire(tmp_path)
    assert [(c["name"], c["success"]) for c in calls] == [
        ("mcpforunity://example", True),
        ("subject", False),
        ("open", None),
    ]
    assert calls[0]["result"] == {"value": 32}
    assert calls[1]["arguments"] == {"a": 7}
