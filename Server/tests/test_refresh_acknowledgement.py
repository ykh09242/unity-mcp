"""Public refresh responses acknowledge only confirmed, unchanged external edits."""

import json
import os

from fastmcp import Client, FastMCP
from fastmcp.server.middleware import Middleware
import pytest

from services.state import external_changes_scanner as scanner_module
from services.tools import refresh_unity as refresh_module


@pytest.mark.asyncio
@pytest.mark.parametrize("protocol", ["2026-07-28", "legacy"])
@pytest.mark.parametrize("compile_mode", ["none", "request"])
@pytest.mark.parametrize(
    "case",
    [
        "rejected",
        "rejected_disconnect",
        "retry_rejected",
        "no_wait",
        "unchanged",
        "new_sampled",
        "new_unsampled",
        "lost_response",
        "lost_response_no_wait",
    ],
)
async def test_public_refresh_acknowledges_only_confirmed_snapshot(
    monkeypatch, tmp_path, protocol, compile_mode, case
):
    # Given: an actual dirty scanner baseline and a selected public MCP tool.
    monkeypatch.setattr(scanner_module, "_in_pytest", lambda: False)
    monkeypatch.setattr(refresh_module, "_in_pytest", lambda: False)
    scanner = scanner_module.ExternalChangesScanner(scan_interval_ms=0)
    instance = "RefreshAcknowledgement@fixture"
    root = tmp_path / "Project"
    (root / "Assets").mkdir(parents=True)
    target = root / "Assets" / "script.cs"
    target.write_text("// original", encoding="utf-8")
    os.utime(target, ns=(100_000_000_000, 100_000_000_000))
    scanner.set_project_root(instance, str(root))
    scanner.update_and_get(instance)
    target.write_text("// import this edit", encoding="utf-8")
    os.utime(target, ns=(200_000_000_000, 200_000_000_000))
    assert scanner.update_and_get(instance)["external_changes_dirty"] is True
    monkeypatch.setattr(refresh_module, "external_changes_scanner", scanner)
    requests = []
    polls = []

    async def transport(send, selected, command, params, **kwargs):
        requests.append(command)
        assert selected == instance and command == "refresh_unity"
        assert kwargs == {"retry_on_reload": False}
        if case in ("rejected", "rejected_disconnect", "retry_rejected"):
            return {
                "success": False,
                "error": "disconnected before dispatch"
                if case == "rejected_disconnect"
                else "Unity rejected before execution",
                "hint": "retry",
                "data": {"reason": "tests_running" if case == "retry_rejected" else "reloading"},
            }
        if case in ("lost_response", "lost_response_no_wait"):
            return {"success": False, "error": "disconnected", "hint": "retry"}
        return {"success": True, "data": {"refresh_triggered": True}}

    async def state(ctx):
        polls.append(True)
        if case in ("new_sampled", "new_unsampled"):
            target.write_text("// newer concurrent edit", encoding="utf-8")
            os.utime(target, ns=(300_000_000_000, 300_000_000_000))
            if case == "new_sampled":
                scanner.update_and_get(instance)
        return {"success": True, "data": {"advice": {"ready_for_tools": True}}}

    monkeypatch.setattr(refresh_module.unity_transport, "send_with_unity_instance", transport)
    monkeypatch.setattr(refresh_module.editor_state, "get_editor_state_authoritative", state)

    class Selected(Middleware):
        async def on_call_tool(self, context, call_next):
            await context.fastmcp_context.set_state("unity_instance", instance)
            return await call_next(context)

    app = FastMCP("refresh-acknowledgement")
    app.add_middleware(Selected())
    app.tool(name="refresh_unity")(refresh_module.refresh_unity)
    # When: the real public tool processes a refresh outcome.
    async with Client(app, mode=protocol) as client:
        result = await client.call_tool(
            "refresh_unity",
            {
                "compile": compile_mode,
                "wait_for_ready": case not in ("no_wait", "lost_response_no_wait"),
            },
        )
    response = json.loads(result.content[0].text)
    # Then: rejected/no-wait/newer changes are never acknowledged as imported.
    assert requests == ["refresh_unity"]
    if case in ("rejected", "rejected_disconnect", "retry_rejected", "lost_response_no_wait"):
        assert response["success"] is False and response["hint"] == "retry"
        assert polls == []
    else:
        assert response["success"] is True
        assert bool(polls) is (case != "no_wait")
    if case == "lost_response":
        assert response["data"]["recovered_from_disconnect"] is True
        assert "execution is unconfirmed" in response["message"]
        assert scanner.update_and_get(instance)["external_changes_dirty"] is True
        return
    if case == "unchanged":
        assert scanner.update_and_get(instance)["external_changes_dirty"] is False
        target.write_text("// edit after acknowledgement", encoding="utf-8")
        os.utime(target, ns=(300_000_000_000, 300_000_000_000))
    assert scanner.update_and_get(instance)["external_changes_dirty"] is True


def test_refresh_snapshot_cannot_clear_reassigned_project(monkeypatch, tmp_path):
    monkeypatch.setattr(scanner_module.config, "http_remote_hosted", False)
    scanner = scanner_module.ExternalChangesScanner()
    scanner.set_project_root("one", str(tmp_path / "Before"))
    snapshot = scanner.capture_dirty_state("one")
    scanner.set_project_root("one", str(tmp_path / "After"))
    scanner._states["one"].dirty = True
    assert scanner.clear_dirty("one", expected=snapshot) is False
    assert scanner._states["one"].dirty is True


@pytest.mark.asyncio
@pytest.mark.parametrize("protocol", ["2026-07-28", "legacy"])
@pytest.mark.parametrize("scope", ["scripts", "assets", "all"])
@pytest.mark.parametrize("compile_mode", ["none", "request"])
@pytest.mark.parametrize("refresh_triggered", [True, False, None])
async def test_refresh_acknowledgement_requires_asset_import(
    monkeypatch, protocol, scope, compile_mode, refresh_triggered
):
    scanner = scanner_module.ExternalChangesScanner()
    instance = "RefreshScope@fixture"
    scanner._states[instance] = scanner_module.ExternalChangesState(
        dirty=True, last_seen_mtime_ns=1
    )
    monkeypatch.setattr(refresh_module, "external_changes_scanner", scanner)
    requests = []

    async def transport(send, selected, command, params, **kwargs):
        requests.append(params)
        assert selected == instance and command == "refresh_unity"
        data = {"compile_requested": compile_mode == "request"}
        # Older native versions did not report refresh_triggered. Preserve their
        # successful asset/all contract; an explicit false is authoritative.
        if refresh_triggered is not None:
            data["refresh_triggered"] = refresh_triggered
        return {"success": True, "data": data}

    monkeypatch.setattr(refresh_module.unity_transport, "send_with_unity_instance", transport)

    class Selected(Middleware):
        async def on_call_tool(self, context, call_next):
            await context.fastmcp_context.set_state("unity_instance", instance)
            return await call_next(context)

    app = FastMCP("refresh-scope-acknowledgement")
    app.add_middleware(Selected())
    app.tool(name="refresh_unity")(refresh_module.refresh_unity)
    async with Client(app, mode=protocol) as client:
        result = await client.call_tool(
            "refresh_unity", {"scope": scope, "compile": compile_mode, "wait_for_ready": True}
        )
    response = json.loads(result.content[0].text)
    assert response["success"] is True and len(requests) == 1
    acknowledged = scope != "scripts" and refresh_triggered is not False
    assert scanner._states[instance].dirty is (not acknowledged)
