"""Configured-host trials retain competing capabilities and observed source identity."""

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
import sys
from unittest.mock import AsyncMock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.experiments import blind_tool_host as host


def test_console_parent_starts_with_requested_settings_and_observes_owned_model(
    tmp_path, monkeypatch
):
    actor = "aabbccdd-1234-1234-1234-123456789012"
    options = args(tmp_path, installed=True, timeout=5)
    client = SimpleNamespace(
        read_messages=host.anyio.sleep_forever,
        send=AsyncMock(),
        request=AsyncMock(
            side_effect=[
                {},
                {"thread": {"id": actor}, "reasoningEffort": "medium"},
                {"thread": {"id": actor, "model": "model"}},
            ]
        ),
    )
    monkeypatch.setattr(host, "AppServer", lambda process: client)
    monkeypatch.setattr(host.anyio, "open_process", AsyncMock())
    monkeypatch.setattr(host, "close_process", AsyncMock())
    monkeypatch.setattr(host, "observe_host", AsyncMock(return_value={"status": "observed"}))
    monkeypatch.setattr(host, "_project_verified", AsyncMock(return_value=True))
    asyncio.run(host._configured_session(options, tmp_path, "", {"task": "console-errors"}))
    calls = client.request.await_args_list
    assert [call.args[0] for call in calls] == ["initialize", "thread/start", "thread/read"]
    settings = calls[1].args[1]
    assert settings["config"] == {"model_reasoning_effort": "medium"}
    assert settings["allowProviderModelFallback"] is False
    assert calls[2].args[1]["threadId"] == actor
    surface = json.loads((tmp_path / "installed-host.json").read_text())
    assert (surface["model"], surface["effort"], surface["project_verified"]) == (
        "model",
        "medium",
        True,
    )


def test_console_retries_only_incomplete_actor_observations_with_bounded_delays(
    tmp_path, monkeypatch
):
    options = args(tmp_path)
    catalog = AsyncMock(side_effect=[{"status": "incomplete"}, {"status": "observed"}])
    root = AsyncMock(side_effect=[host.InteropFailure("starting"), False, True])
    sleep = AsyncMock()
    monkeypatch.setattr(host, "observe_host", catalog)
    monkeypatch.setattr(host, "_project_verified", root)
    monkeypatch.setattr(host.anyio, "sleep", sleep)
    result = asyncio.run(
        host.observe_ready_surface(
            None, "parent", "unityMCP", options, installed=False, check_project=True, retry=True
        )
    )
    assert result == {"status": "observed", "project_verified": True}
    assert catalog.await_count == 2 and root.await_count == 3
    assert [call.args[0] for call in sleep.await_args_list] == [1, 3]
    root = AsyncMock(side_effect=host.InteropFailure("still unavailable"))
    monkeypatch.setattr(host, "_project_verified", root)
    previous = {"status": "observed"}
    result = asyncio.run(
        host.observe_ready_surface(
            None,
            "child",
            "unityMCP",
            options,
            initial=previous,
            installed=False,
            check_project=True,
            retry=True,
        )
    )
    assert result["project_verified"] is False and root.await_count == 3
    assert catalog.await_count == 2 and all(
        call.args[1] == "child" for call in root.await_args_list
    )


def args(tmp_path, **options):
    return SimpleNamespace(
        **{
            "codex": "codex",
            "project": tmp_path,
            "relay": tmp_path / "bridge.py",
            "endpoint": None,
            "instance": "Game@123",
            "graph_asset": "",
            "model": "model",
            "effort": "medium",
            "approval": "never",
            "delegation": "v2",
            "installed": False,
            "unity_server": "unityMCP",
            **options,
        }
    )


def test_configured_control_leaves_unity_transport_to_the_installed_launcher(tmp_path):
    command, name = host.launch_settings(args(tmp_path, installed=True, relay=None), tmp_path)
    assert name == "unityMCP"
    assert "--ignore-user-config" not in command
    assert not any("mcp_servers." in flag for flag in command)


def test_candidate_disables_only_configured_unity_and_uses_a_separate_recorded_service(tmp_path):
    command, name = host.launch_settings(args(tmp_path), tmp_path)
    assert name != "unityMCP"
    assert "mcp_servers.unityMCP.enabled=false" in command
    assert any(f"mcp_servers.{name}.command=" in flag for flag in command)
    assert not any(flag.startswith("mcp_servers.unityMCP.command") for flag in command)


class Catalog:
    def __init__(self, pages, receipt=None):
        self.pages = iter(pages)
        self.receipt = receipt
        self.calls = []

    async def request(self, method, params):
        self.calls.append((method, params))
        if method == "mcpServerStatus/list":
            return next(self.pages)
        return {"contents": [{"text": json.dumps(self.receipt)}]}


def server(name, tools, *, resources=()):
    return {
        "name": name,
        "tools": {t: {"description": "private"} for t in tools},
        "toolsError": None,
        "resources": [{"uri": uri} for uri in resources],
        "resourceTemplates": [],
        "runtimeStatus": "connected",
    }


def test_same_thread_inventory_is_paged_and_only_safe_identifiers_are_retained():
    client = Catalog(
        [
            {"data": [server("codegraph", ["explore"])], "nextCursor": "page2"},
            {
                "data": [
                    server("unityMCP", ["get_game_view_size"], resources=["mcpforunity://doctor"])
                ],
                "nextCursor": None,
            },
        ],
        {
            "success": True,
            "data": {
                "server": {"source_commit": "a" * 40},
                "target": {"instance_id": "Game@123"},
                "package": {"effective_server_commit": "b" * 40},
            },
        },
    )
    result = asyncio.run(
        host.observe_host(client, "thread-1", "unityMCP", "Game@123", installed=True)
    )
    assert result["status"] == "observed"
    assert result["unity"]["tools"] == ["get_game_view_size"]
    assert result["competing_servers"] == {"codegraph": ["explore"]}
    assert result["source"]["server_commit"] == "a" * 40
    assert result["source"]["package_commit"] == "b" * 40
    assert "private" not in json.dumps(result)
    assert all(params["threadId"] == "thread-1" for _, params in client.calls)


def test_incomplete_inventory_and_source_are_unknown():
    client = Catalog(
        [
            {"data": [server("unityMCP", [])], "nextCursor": "again"},
            {"data": [], "nextCursor": "again"},
        ]
    )
    result = asyncio.run(
        host.observe_host(client, "thread", "unityMCP", "Game@123", installed=False)
    )
    assert result["status"] == "incomplete"
    assert not result["competitor_digest"] and not result["source"]["server_commit"]


def test_host_records_discovery_contract_from_this_threads_schema_only():
    unity = server("unityMCP", ["manage_tools", "search_unity_tools"])
    schema = {"type": "object", "properties": {"action": {}, "query": {"description": "private"}}}
    unity["tools"]["manage_tools"]["inputSchema"] = schema
    receipt = asyncio.run(
        host.observe_host(Catalog([{"data": [unity]}]), "thread", "unityMCP", None, installed=False)
    )
    assert receipt["unity"]["discovery_inputs"] == {
        "manage_tools": {
            "parameters": ["action", "query"],
            "input_schema_sha256": host._digest(schema),
        }
    }
    assert "private" not in json.dumps(receipt)


def test_archive_source_is_attested_for_recorded_treatment():
    client = Catalog(
        [{"data": [server("trial", ["viewer"], resources=[host.TRIAL_SOURCE])]}],
        {"kind": "archived_server", "source_commit": "f" * 40, "archive_sha256": "a" * 64},
    )
    receipt = asyncio.run(host.observe_host(client, "thread", "trial", None, installed=False))
    assert receipt["source"] == {
        "kind": "same_thread_archive",
        "server_commit": "f" * 40,
        "archive_sha256": "a" * 64,
        "package_commit": None,
    }


def test_lazily_advertised_names_remain_distinct_from_a_live_connection():
    competitor = {**server("codegraph", ["explore"]), "runtimeStatus": "notStarted"}
    for errored, expected in ((None, "observed"), ("failed", "incomplete")):
        client = Catalog(
            [{"data": [server("unityMCP", ["viewer"]), {**competitor, "toolsError": errored}]}]
        )
        result = asyncio.run(host.observe_host(client, "thread", "unityMCP", None, installed=False))
        assert result["status"] == expected
        assert result["service_runtime_status"]["codegraph"] == "notStarted"


def test_guarded_installed_turn_requires_matching_target_catalog_and_source():
    normal = {
        "status": "observed",
        "project_verified": True,
        "unity": {"tools": ["view"]},
        "competitor_digest": "catalog",
        "source": {"server_commit": "a" * 40},
    }
    assert host.same_installation(normal, dict(normal))
    for key, bad in (
        ("project_verified", False),
        ("status", "incomplete"),
        ("source", {}),
        ("unity", {"tools": []}),
    ):
        assert not host.same_installation(normal, {**normal, key: bad})


def test_installed_host_scores_result_and_retains_competing_first_call(tmp_path):
    from tools.experiments.blind_tool_trace import score_case

    (tmp_path / "case.json").write_text(
        json.dumps(
            {
                "task": "game-view",
                "graph_asset": "",
                "role": "main",
                "backend": "installed-host",
                "host_mode": "configured",
                "unity_server": "unityMCP",
            }
        )
    )
    external = host.project_item(
        {
            "id": "1",
            "type": "mcpToolCall",
            "server": "another",
            "tool": "inspect",
            "arguments": {"private": "secret"},
            "result": {"content": "secret"},
            "status": "completed",
        },
        "unityMCP",
        "",
    )
    view = host.project_item(
        {
            "id": "2",
            "type": "mcpToolCall",
            "server": "unityMCP",
            "tool": "get_game_view_size",
            "status": "completed",
            "arguments": {},
            "result": {
                "structuredContent": {
                    "success": True,
                    "data": {
                        "render_size": {"width": 800, "height": 600},
                        "current_size": {"label": "Standard"},
                    },
                }
            },
        },
        "unityMCP",
        "",
    )
    assert "secret" not in json.dumps(external)
    events = [{"type": "thread.started", "thread_id": "aabbccdd-1234-1234-1234-123456789012"}]
    events.extend({"type": "item.completed", "item": item} for item in (external, view))
    events.append({"type": "turn.completed"})
    (tmp_path / "events.jsonl").write_text("\n".join(map(json.dumps, events)), encoding="utf-8")
    score = score_case(tmp_path)
    assert (
        score["first_business_call"] == "another.inspect"
        and score["calls_before_first_suitable"] == 1
    )
    assert score["live_evidence"] == "complete" and score["wire_evidence"] == "none"
    assert score["evidence_transport"] == "configured-host"
