"""The unhinted trial preserves original delivery, case pairing and actor evidence."""

from io import StringIO
import json
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.experiments import live_agent_trial as trial
from tools.experiments.live_agent_wire import Journal
from tools.experiments.live_editor_fixture.preflight import originals


@pytest.mark.asyncio
async def test_native_project_probe_reports_backpressure_with_its_own_deadline(
    tmp_path, monkeypatch
):
    def resource(payload):
        return SimpleNamespace(contents=[SimpleNamespace(text=json.dumps(payload))])

    client = AsyncMock()
    client.read_resource_mcp.side_effect = [
        resource({"success": True, "instances": [{"id": "owned"}]}),
        resource(
            {
                "success": False,
                "code": "command_resume_capacity",
                "hint": "retry_after_journal_expiry",
                "data": {"execution": "not_started", "capacity": {"retry_after_ms": 4700}},
            }
        ),
    ]
    transport = MagicMock()
    client_context = MagicMock()
    client_context.__aenter__.return_value = client
    monkeypatch.setattr(trial, "StdioTransport", transport)
    monkeypatch.setattr(trial, "Client", lambda *args, **kwargs: client_context)
    with pytest.raises(
        RuntimeError, match="command_resume_capacity.*not_started.*retry_after_journal_expiry.*4700"
    ):
        await trial.preflight("server", tmp_path, "owned", "core", timeout=12)
    assert transport.call_args.kwargs["env"]["UNITY_MCP_COMMAND_TOTAL_TIMEOUT"] == "12"
    assert "UNITY_MCP_COMMAND_TOTAL_TIMEOUT" not in trial.wire.server_environment(
        tmp_path / "status"
    )


def test_lane_preserves_failed_commissioning_and_stops_before_the_next_original(
    tmp_path, monkeypatch
):
    from tools.experiments.live_editor_fixture import lane

    project = tmp_path / "project"
    project.mkdir()

    def request(path, operation, *args):
        assert path == tmp_path
        return {"ok": True, "instance": "owned", "project_root": str(project), "state": {}}

    async def preflight(server, path, instance, profile):
        return {"tools": ["one"] if profile == "core" else ["one", "two"]}

    def refuse(*args, **kwargs):
        raise RuntimeError("Editor unavailable before dispatch")

    monkeypatch.setattr(lane, "request", request)
    monkeypatch.setattr(trial, "preflight", preflight)
    monkeypatch.setattr(trial, "actor_command", refuse)
    monkeypatch.setattr(
        trial, "run_actor", lambda *args: pytest.fail("Unexpected actor submission")
    )
    row = {
        "id": "first",
        "task": "edit",
        "profile": "core",
        "language": "en",
        "query": "Inspect",
        "seed": 1,
    }
    with pytest.raises(RuntimeError, match="before the next submission"):
        trial.run_lane(tmp_path, [row, {**row, "id": "second"}], tmp_path, "client", "server")
    failed = json.loads((tmp_path / "first/row.json").read_bytes())
    assert failed["commissioning_failed"] and not failed["delivered"] and not failed["calls"]
    assert (tmp_path / "first/attempt-1/row.json").is_file()
    assert not (tmp_path / "second").exists()


def test_profile_pairs_have_identical_originals_and_counterbalanced_order():
    pairs = trial.pairs()
    assert len(pairs) == len({pair[0]["query"] for pair in pairs}) == 71
    assert len({row["id"] for pair in pairs for row in pair}) == 142
    assert sum(pair[0]["panel"] == "new" for pair in pairs) == 12
    assert set(originals()) == {(p[0]["task"], p[0]["language"]) for p in pairs}
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
