"""Tail opt-in must not inherit legacy defaults, retries or wire semantics."""

import importlib
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest


@pytest.fixture
def console(monkeypatch):
    module = importlib.import_module("services.tools.read_console")
    lookup = AsyncMock(return_value="Synthetic@owned")
    send = AsyncMock(return_value={"success": False, "error": "unknown_command"})
    monkeypatch.setattr(module, "get_unity_instance_from_context", lookup)
    monkeypatch.setattr(module, "send_with_unity_instance", send)
    return module, lookup, send


@pytest.mark.asyncio
async def test_tail_begin_uses_distinct_nonreplayed_route_with_json_default(console):
    # Given omitted legacy count/format and an old native peer.
    module, lookup, send = console
    # When a client opts into a tail.
    result = await module.read_console(SimpleNamespace(), tail_cursor="begin")
    # Then exactly one distinct dispatch occurs, without defaults or fallback.
    assert result["success"] is False
    lookup.assert_awaited_once()
    send.assert_awaited_once()
    assert send.call_args.args[1:3] == ("Synthetic@owned", "tail_console")
    assert send.call_args.kwargs == {"retry_on_reload": False}
    assert send.call_args.args[3] == {
        "tail_cursor": "begin",
        "types": ["error", "warning", "log"],
        "format": "json",
        "pageSize": 50,
        "includeStacktrace": False,
        "includeMcpLogs": False,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "options",
    [
        {"count": 10},
        {"count": "all"},
        {"count": "bad"},
        {"cursor": 0},
        {"cursor": "bad"},
        {"format": "plain"},
        {"format": "detailed"},
        {"action": "clear"},
        {"page_size": 0},
        {"page_size": 201},
        {"page_size": True},
        {"page_size": "1.5"},
        {"filter_text": "x" * 257},
    ],
)
async def test_tail_conflicts_reject_raw_values_before_lookup(console, options):
    # Given an explicitly incompatible tail input.
    module, lookup, send = console
    # When validation runs before legacy coercion/defaults.
    result = await module.read_console(SimpleNamespace(), tail_cursor="begin", **options)
    # Then there is no instance lookup or native call.
    assert result["success"] is False
    lookup.assert_not_awaited()
    send.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("token", ["", "0", 0, True, "x" * 97, "ct1.bad.bad"])
async def test_malformed_tail_cursor_rejects_before_lookup(console, token):
    # Given an invalid opaque token rather than a numeric offset.
    module, lookup, send = console
    # When tail validation runs.
    result = await module.read_console(SimpleNamespace(), tail_cursor=token)
    # Then invalid input has no native side effect.
    assert result["success"] is False
    lookup.assert_not_awaited()
    send.assert_not_awaited()


@pytest.mark.asyncio
async def test_omitted_tail_preserves_plain_ten_with_bounded_recovery(console):
    # Given an existing caller.
    module, _, send = console
    # When the caller omits all new options.
    await module.read_console(SimpleNamespace())
    # Then the exact existing request stays intact.
    assert send.call_args.args[2:] == (
        "read_console",
        {
            "action": "get",
            "types": ["error", "warning", "log"],
            "count": 10,
            "format": "plain",
            "includeStacktrace": False,
        },
    )
    assert send.call_args.kwargs == {"retry_on_reload": False}


def page(module, *, cursor="begin", items=None, **changes):
    params = {
        "types": ["error", "warning", "log"],
        "includeMcpLogs": False,
        "includeStacktrace": False,
    }
    generation = "a" * 32 if cursor == "begin" else cursor.split(".")[1]
    entries = items or []
    return {
        "success": True,
        "data": {
            "version": 1,
            "generation": generation,
            "filter_identity": module._tail_identity(params),
            "items": entries,
            "next_cursor": f"ct1.{generation}.{'b' * 32}",
            "snapshot_rows": len(entries),
            "scanned_rows": len(entries),
            "filtered_rows": 0,
            "native_entry_reads": len(entries),
            "has_more": False,
            "stop_reason": "snapshot_end",
            "loss_unknown": True,
            "continuity": "bounded_anchors",
            **changes,
        },
    }


@pytest.mark.asyncio
async def test_console_source_diagnostics_survive_public_tail_validation(console):
    module, _, send = console
    token = f"ct1.{'a' * 32}.{'d' * 32}"
    entry = {
        "type": "Error",
        "message": "domain backup",
        "file": "",
        "line": 0,
        "stackTrace": None,
        "diagnostics": {
            "source": "unity_console",
            "location": "not_provided_by_unity",
            "stack_trace": "not_requested",
            "stack_trace_source": "unity_utf16_boundary",
        },
    }
    send.return_value = page(module, cursor=token, items=[{"sequence": 1, "entry": entry}])
    result = await module.read_console(SimpleNamespace(), tail_cursor=token)
    assert result == {**send.return_value, "observation": {"status": "available"}}
    assert result["data"]["items"][0]["entry"]["diagnostics"] == entry["diagnostics"]


@pytest.mark.parametrize(
    "field,value",
    [
        ("source", "made_up"),
        ("location", "x" * 1000),
        ("stack_trace", "invented"),
        ("extra", "bad"),
    ],
)
def test_console_source_diagnostics_are_bounded_and_known(console, field, value):
    from pydantic import ValidationError

    module, _, _ = console
    data = {
        "source": "unity_console",
        "location": "partial",
        "stack_trace": "unavailable",
        "stack_trace_source": "frame_heuristic",
        field: value,
    }
    with pytest.raises(ValidationError):
        module.ConsoleSourceDiagnostics.model_validate(data)


@pytest.mark.asyncio
async def test_begin_accepts_only_empty_watermark_and_preserves_loss_disclosure(console):
    # Given a bounded native begin watermark.
    module, _, send = console
    send.return_value = page(module, snapshot_rows=20000, native_entry_reads=2)
    # When the response crosses the Python contract boundary.
    result = await module.read_console(SimpleNamespace(), tail_cursor="begin")
    # Then zero history is claimed and native evidence remains intact.
    assert result == {**send.return_value, "observation": {"status": "available"}}
    assert result["data"]["loss_unknown"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change",
    [
        {"loss_unknown": False},
        {"loss_unknown": 1},
        {"generation": "bad"},
        {"filter_identity": "c" * 64},
        {"next_cursor": "0"},
        {"native_entry_reads": 4100},
        {"scanned_rows": 4097},
        {"version": True},
        {"continuity": "loss_free"},
        {"items": [{"sequence": 1, "entry": {"type": "Log", "message": "Unexpected history"}}]},
        {"has_more": True},
        {"extra": "unrecognized"},
    ],
)
async def test_untrusted_begin_page_cannot_certify_tail_semantics(console, change):
    # Given native data that violates the new opt-in contract.
    module, _, send = console
    send.return_value = page(module, **change)
    # When the public tool validates the purported tail response.
    result = await module.read_console(SimpleNamespace(), tail_cursor="begin")
    # Then legacy/lookalike/overstated native data cannot pass as a tail.
    assert result["success"] is False
    assert result["data"]["loss_unknown"] is True
    assert result["data"]["reason"] == "invalid_tail_response"


@pytest.mark.asyncio
@pytest.mark.parametrize("sequences", [[1, 1], [2, 1], [0], [4]])
async def test_continuation_rejects_duplicate_unordered_or_out_of_snapshot_sequences(
    console, sequences
):
    # Given an opaque cursor and contradictory native sequence evidence.
    module, _, send = console
    token = f"ct1.{'a' * 32}.{'d' * 32}"
    items = [{"sequence": n, "entry": {"type": "Log", "message": "Synthetic"}} for n in sequences]
    send.return_value = page(module, cursor=token, items=items)
    # When continuation is validated.
    result = await module.read_console(SimpleNamespace(), tail_cursor=token)
    # Then no invalid item page is exposed as success.
    assert result["success"] is False


def test_real_sdk_modes_keep_tail_private_and_refuse_old_peer_without_fallback(tmp_path):
    # Given a task-isolated child with asserted active imports and the real registry/SDK/transport.
    import os
    from pathlib import Path
    import subprocess
    import sys
    import textwrap

    root = Path(__file__).resolve().parents[2]
    source = root / "Server" / "src"
    program = r"""
import importlib, pathlib, sys
import anyio
from unittest.mock import AsyncMock
from fastmcp import Client, FastMCP
from core.config import config
from services.registry import get_registered_tools
from transport.plugin_hub import PluginHub
console = importlib.import_module("services.tools.read_console")
assert pathlib.Path(console.__file__).resolve() == pathlib.Path(sys.argv[1]).resolve()
config.transport_mode = "http"
config.http_remote_hosted = False
console.get_unity_instance_from_context = AsyncMock(return_value="Synthetic@owned")
requests = []
async def old_peer(instance, command, params, **kwargs):
    requests.append((instance, command, params.copy(), kwargs.copy()))
    return {"success": False, "error": "unknown_command", "message": "Synthetic old peer"}
PluginHub.send_command_for_instance = old_peer
server = FastMCP("owned-console-tail")
server.tool(name="read_console")(console.read_console)
async def scenario():
    for mode in ("2026-07-28", "legacy"):
        async with Client(server, mode=mode) as client:
            tools = await client.list_tools()
            assert [t.name for t in tools] == ["read_console"]
            schema = tools[0].inputSchema["properties"]
            assert "tail_cursor" in schema
            start = len(requests)
            result = await client.call_tool("read_console", {"tail_cursor": "begin"})
            assert result.structured_content["success"] is False
            assert len(requests) == start + 1
            sent = requests[-1]
            assert sent[1] == "tail_console" and sent[2]["format"] == "json"
            assert "count" not in sent[2] and "cursor" not in sent[2]
            assert sent[3]["retry_on_reload"] is False
            start = len(requests)
            for invalid in ({"format": "plain"}, {"count": 10}, {"cursor": 0}):
                result = await client.call_tool("read_console", {"tail_cursor": "begin", **invalid})
                assert result.structured_content["success"] is False
            assert len(requests) == start
            result = await client.call_tool("read_console", {})
            assert requests[-1][1] == "read_console"
            assert requests[-1][2]["format"] == "plain" and requests[-1][2]["count"] == 10
    assert not any(t["name"] == "tail_console" for t in get_registered_tools())
anyio.run(scenario)
print("PLAY04_SDK_MODES=2 OLD_PEER_CALLS=2 NO_FALLBACK=TRUE")
"""
    env = {
        **os.environ,
        "PYTHONPATH": str(source),
        "PYTHONDONTWRITEBYTECODE": "1",
        "UNITY_MCP_DISABLE_TELEMETRY": "true",
    }
    for name in (
        "HOME",
        "USERPROFILE",
        "APPDATA",
        "LOCALAPPDATA",
        "TEMP",
        "TMP",
        "XDG_DATA_HOME",
        "XDG_CACHE_HOME",
    ):
        env[name] = str(tmp_path)
    env.pop("PYTEST_CURRENT_TEST", None)
    # When both SDK modes call an old peer through actual central HTTP transport.
    result = subprocess.run(
        [
            sys.executable,
            "-B",
            "-c",
            textwrap.dedent(program),
            str(source / "services/tools/read_console.py"),
        ],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=40,
    )
    # Then the exact no-fallback and no-extra-public-tool assertions pass.
    assert result.returncode == 0, result.stdout + result.stderr
    assert "PLAY04_SDK_MODES=2" in result.stdout
