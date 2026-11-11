"""Existing native CLI test starts must carry current-source admission evidence."""

from unittest.mock import AsyncMock
import time

import pytest

from cli.utils.config import CLIConfig
from cli.utils.connection import UnityCommandError
from tests.test_source_freshness_contracts import current


@pytest.mark.asyncio
async def test_cli_start_imports_compiles_and_keeps_exact_target_and_project_guard():
    from cli.utils.source_freshness import prepare_test_start

    config = CLIConfig(unity_instance="Fixture@" + "a" * 16, expected_project_root="/Owned")
    stale = {"success": True, "source_freshness": {**current(), "status": "changed"}}
    send = AsyncMock(
        side_effect=[
            stale,
            {"success": True},
            stale,
            {"success": True},
            {"success": True, "source_freshness": current()},
        ]
    )
    inventory = AsyncMock()
    params, selected, remaining = await prepare_test_start(
        {"mode": "EditMode"},
        config,
        deadline=time.monotonic() + 2,
        send=send,
        inventory=inventory,
    )
    assert params["source_token"] == current()["source_token"]
    assert params["require_source_freshness"] is True and remaining > 0
    assert selected is config
    inventory.assert_not_awaited()
    assert [c.args[0] for c in send.await_args_list] == [
        "get_compile_job",
        "refresh_unity",
        "get_compile_job",
        "refresh_unity",
        "get_compile_job",
    ]
    assert all(c.args[2].expected_project_root == "/Owned" for c in send.await_args_list)
    assert all(c.args[2].unity_instance == config.unity_instance for c in send.await_args_list)


@pytest.mark.asyncio
async def test_cli_auto_target_is_frozen_before_source_observation():
    from cli.utils.source_freshness import prepare_test_start

    inventory = AsyncMock(return_value={"instances": [{"project": "Fixture", "hash": "a" * 16}]})
    send = AsyncMock(return_value={"success": True, "source_freshness": current()})
    _, selected, _ = await prepare_test_start(
        {},
        CLIConfig(),
        deadline=time.monotonic() + 1,
        send=send,
        inventory=inventory,
    )
    assert selected.unity_instance == "Fixture@" + "a" * 16
    assert send.await_args.args[2] is selected


@pytest.mark.asyncio
async def test_cli_target_deadline_is_a_structured_refusal_before_dispatch():
    from cli.utils.source_freshness import prepare_test_start

    send = AsyncMock()
    with pytest.raises(UnityCommandError) as error:
        await prepare_test_start(
            {},
            CLIConfig(),
            deadline=time.monotonic() + 1,
            send=send,
            inventory=AsyncMock(side_effect=TimeoutError()),
        )
    assert error.value.response["code"] == "editor_selection_unavailable"
    send.assert_not_awaited()


@pytest.mark.asyncio
async def test_cli_ambiguous_target_and_uncertain_refresh_never_start_tests():
    from cli.utils.source_freshness import prepare_test_start

    inventory = AsyncMock(
        return_value={
            "instances": [{"project": "A", "hash": "a" * 16}, {"project": "B", "hash": "b" * 16}]
        }
    )
    send = AsyncMock()
    with pytest.raises(UnityCommandError):
        await prepare_test_start(
            {}, CLIConfig(), deadline=time.monotonic() + 1, send=send, inventory=inventory
        )
    send.assert_not_awaited()
    send.side_effect = [
        {"success": True, "source_freshness": {**current(), "status": "changed"}},
        TimeoutError(),
    ]
    with pytest.raises(UnityCommandError) as error:
        await prepare_test_start(
            {},
            CLIConfig(unity_instance="A@" + "a" * 16),
            deadline=time.monotonic() + 1,
            send=send,
            inventory=inventory,
        )
    assert error.value.response["data"]["source_freshness"]["reason"] == "refresh_outcome_unknown"
    assert send.await_count == 2


@pytest.mark.asyncio
async def test_native_cli_wire_attaches_token_without_downgrading_guard(monkeypatch):
    import httpx
    import cli.utils.connection as connection

    commands = []

    def handler(request):
        import json

        payload = json.loads(request.content)
        commands.append(payload)
        assert request.url.path == "/api/context-command"
        assert payload["expected_project"] == {"root": "/Owned"}
        if payload["type"] == "get_compile_job":
            return httpx.Response(200, json={"success": True, "source_freshness": current()})
        assert payload["type"] == "run_tests"
        assert payload["params"]["source_token"] == current()["source_token"]
        return httpx.Response(200, json={"success": True, "data": {"job_id": "test"}})

    monkeypatch.setattr(connection, "_auth_headers", lambda _cfg: {})
    monkeypatch.setattr(
        connection,
        "_http_client",
        lambda _cfg: httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    result = await connection.send_command(
        "run_tests",
        {"mode": "EditMode"},
        CLIConfig(unity_instance="Fixture@" + "a" * 16, expected_project_root="/Owned"),
    )
    assert result["success"]
    assert [c["type"] for c in commands] == ["get_compile_job", "run_tests"]
