"""Public contracts for hosted requests crossing host-local boundaries."""
import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest


def _install_baseline_overlay():
    """Optional read-only baseline snapshots for identical before/after scenarios."""
    baseline = os.environ.get("MCP_HOST_BOUNDARY_BASELINE")
    if not baseline:
        return
    import importlib.util
    modules = {"services.resources.editor_state": "editor_state.py",
               "services.state.external_changes_scanner": "external_changes_scanner.py",
               "services.tools.run_tests": "run_tests.py", "utils.focus_nudge": "focus_nudge.py"}
    class BaselineFinder:
        def find_spec(self, fullname, path=None, target=None):
            if fullname in modules:
                return importlib.util.spec_from_file_location(fullname, Path(baseline) / modules[fullname])
            return None
    sys.meta_path.insert(0, BaselineFinder())


def _run_sdk_child(request, tmp_path):
    """Keep real SDK cases independent of legacy integration collection stubs."""
    if os.environ.get("MCP_HOST_BOUNDARY_CHILD") == "1":
        return False
    node_id = str(Path(__file__).resolve()) + "::" + request.node.nodeid.split("::", 1)[1]
    result = subprocess.run([sys.executable, "-B", __file__, node_id, str(tmp_path / "sdk-child")],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    return True


@pytest.mark.asyncio
@pytest.mark.parametrize("wait", [None, 1])
@pytest.mark.parametrize("protocol", ["2026-07-28", "legacy"])
async def test_remote_editor_and_jobs_never_touch_host(monkeypatch, tmp_path, wait, protocol, request):
    if _run_sdk_child(request, tmp_path):
        return
    from fastmcp import Client, FastMCP
    from fastmcp.server.middleware import Middleware
    from core.config import config
    from services.resources import editor_state, _serialize_pydantic
    from services.tools import run_tests
    from transport.plugin_hub import PluginHub

    monkeypatch.setattr(config, "transport_mode", "http")
    monkeypatch.setattr(config, "http_remote_hosted", True)
    counts = {"project": 0, "scan": 0, "root": 0, "focus": 0}

    class Selected(Middleware):
        async def on_read_resource(self, context, call_next):
            await context.fastmcp_context.set_state("unity_instance", "Selected@fixture")
            return await call_next(context)

        async def on_call_tool(self, context, call_next):
            await context.fastmcp_context.set_state("unity_instance", "Selected@fixture")
            return await call_next(context)

    async def send(instance, command, params, **kwargs):
        if command == "get_project_info":
            counts["project"] += 1
            return {"success": True, "data": {"projectRoot": str(tmp_path)}}
        if command == "get_editor_state":
            return {"success": True, "data": {"unity": {"instance_id": "Tenant@varied"}, "assets": {"external_changes_dirty": True}}}
        assert command == "get_test_job"
        return {"success": True, "data": {"job_id": "fixture-job", "status": "running", "last_update_unix_ms": 1, "progress": {"editor_is_focused": False}}}

    def scan(instance):
        counts["scan"] += 1
        return {"external_changes_dirty": False}

    def root(*args):
        counts["root"] += 1

    async def focus(**kwargs):
        counts["focus"] += 1
        return False

    monkeypatch.setattr(PluginHub, "send_command_for_instance", send)
    async def authenticated():
        return "fixture-user"
    monkeypatch.setattr(editor_state.unity_transport, "_resolve_user_id_from_request", authenticated)
    monkeypatch.setattr(editor_state.external_changes_scanner, "set_project_root", root)
    monkeypatch.setattr(editor_state.external_changes_scanner, "update_and_get", scan)
    monkeypatch.setattr(run_tests, "nudge_unity_focus", focus)
    app = FastMCP("host-boundary-contract")
    app.add_middleware(Selected())
    app.resource("mcpforunity://editor/state")(_serialize_pydantic(editor_state.get_editor_state))
    app.tool(name="get_test_job")(run_tests.get_test_job)
    async with Client(app, mode=protocol) as client:
        result = await client.read_resource("mcpforunity://editor/state")
        response = json.loads(result[0].text)
        assert response["success"] is True, response
        native_dirty = response["data"]["assets"]["external_changes_dirty"]
        result = await client.call_tool("get_test_job", {"job_id": "fixture-job", "wait_timeout": wait})
        assert json.loads(result.content[0].text)["success"] is True
    if run_tests._background_tasks:
        await asyncio.gather(*run_tests._background_tasks)
    assert counts == {"project": 0, "scan": 0, "root": 0, "focus": 0}
    assert native_dirty is True


@pytest.mark.asyncio
async def test_remote_focus_guard_precedes_all_host_operations(monkeypatch):
    from core.config import config
    from utils import focus_nudge

    monkeypatch.setattr(config, "http_remote_hosted", True)
    calls = []
    monkeypatch.setattr(focus_nudge, "_is_available", lambda: calls.append("available") or True)
    monkeypatch.setattr(focus_nudge, "_get_frontmost_app", lambda: calls.append("frontmost"))
    monkeypatch.setattr(focus_nudge, "_focus_app", lambda *args: calls.append("focus"))
    assert await focus_nudge.nudge_unity_focus(force=True, unity_project_path="C:/fixture") is False
    assert calls == []


@pytest.mark.asyncio
async def test_local_resource_uses_selected_root_and_key(monkeypatch, tmp_path, request):
    if _run_sdk_child(request, tmp_path):
        return
    import os
    from fastmcp import Client, FastMCP
    from fastmcp.server.middleware import Middleware
    from core.config import config
    from services.resources import editor_state, _serialize_pydantic
    from services.state.external_changes_scanner import ExternalChangesScanner
    from transport.plugin_hub import PluginHub

    monkeypatch.setattr(config, "http_remote_hosted", False)
    monkeypatch.setattr(config, "transport_mode", "http")
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    root = tmp_path / "Selected"
    (root / "Assets").mkdir(parents=True)
    (root / "Packages").mkdir()
    target = root / "Assets" / "ordinary.txt"
    target.write_text("ordinary", encoding="utf-8")
    os.utime(target, ns=(1_000_000_000, 1_000_000_000))
    scanner = ExternalChangesScanner(scan_interval_ms=0)
    monkeypatch.setattr(editor_state, "external_changes_scanner", scanner)
    selected = []
    async def session_id(project_hash):
        selected.append(project_hash)
        return "fixture-session"
    async def session(session_id):
        return SimpleNamespace(project_path=str(root))
    monkeypatch.setattr(PluginHub, "_registry", SimpleNamespace(get_session_id_by_hash=session_id, get_session=session))
    async def send(instance, command, params, **kwargs):
        assert command == "get_editor_state"
        return {"success": True, "data": {"unity": {"instance_id": "Forged@" + str(len(selected))}}}
    monkeypatch.setattr(PluginHub, "send_command_for_instance", send)
    class Selected(Middleware):
        async def on_read_resource(self, context, call_next):
            await context.fastmcp_context.set_state("unity_instance", "Selected@fixture")
            return await call_next(context)
    app = FastMCP("selected-local-contract")
    app.add_middleware(Selected())
    app.resource("mcpforunity://editor/state")(_serialize_pydantic(editor_state.get_editor_state))
    async with Client(app) as client:
        first = json.loads((await client.read_resource("mcpforunity://editor/state"))[0].text)
        assert first["data"]["assets"]["external_changes_dirty"] is False
        os.utime(target, ns=(2_000_000_000, 2_000_000_000))
        second = json.loads((await client.read_resource("mcpforunity://editor/state"))[0].text)
        assert second["data"]["assets"]["external_changes_dirty"] is True
    assert selected == ["fixture", "fixture"]
    assert list(scanner._states) == ["Selected@fixture"]
    assert scanner._states["Selected@fixture"].project_root == str(root)


def test_scanner_lru_and_ttl_are_bounded(monkeypatch, tmp_path):
    from core.config import config
    from services.state import external_changes_scanner as module
    monkeypatch.setattr(config, "http_remote_hosted", False)
    now = [1000]
    monkeypatch.setattr(module, "_now_unix_ms", lambda: now[0])
    scanner = module.ExternalChangesScanner(max_states=2, state_ttl_ms=100)
    for key in ("one", "two", "one", "three"):
        scanner.set_project_root(key, str(tmp_path))
    assert list(scanner._states) == ["one", "three"]
    now[0] = 1100
    scanner.clear_dirty("new")
    assert list(scanner._states) == ["new"]


def test_scanner_enumeration_budget_counts_hidden_entries(monkeypatch, tmp_path):
    from core.config import config
    from services.state import external_changes_scanner as module
    monkeypatch.setattr(config, "http_remote_hosted", False)
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    (tmp_path / "Assets").mkdir()
    for index in range(12):
        (tmp_path / "Assets" / (".hidden" + str(index))).write_text("x")
    scanner = module.ExternalChangesScanner(scan_interval_ms=0, max_entries=5)
    scanner.set_project_root("selected", str(tmp_path))
    seen = []
    original = module.os.scandir
    class Counted:
        def __init__(self, path):
            self.source = original(path)
        def __enter__(self):
            return self
        def __exit__(self, *args):
            self.source.close()
        def __iter__(self):
            for entry in self.source:
                seen.append(entry.name)
                yield entry
    monkeypatch.setattr(module.os, "scandir", Counted)
    scanner.update_and_get("selected")
    assert 0 < len(seen) <= 5


@pytest.mark.asyncio
async def test_scanner_cancellation_stops_worker_without_overlap(monkeypatch, tmp_path):
    import threading
    from core.config import config
    from services.state.external_changes_scanner import ExternalChangesScanner
    monkeypatch.setattr(config, "http_remote_hosted", False)
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    scanner = ExternalChangesScanner(scan_interval_ms=0)
    scanner.set_project_root("selected", str(tmp_path))
    started, exited = threading.Event(), threading.Event()
    def scan(roots):
        started.set()
        assert scanner._active_stop.wait(timeout=2)
        exited.set()
        return None
    monkeypatch.setattr(scanner, "_scan_paths_max_mtime_ns", scan)
    task = asyncio.create_task(scanner.update_and_get_async("selected"))
    assert await asyncio.to_thread(started.wait, 2)
    assert (await scanner.update_and_get_async("other"))["external_changes_dirty"] is False
    assert "other" not in scanner._states
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert await asyncio.to_thread(exited.wait, 2)


def test_manifest_read_and_retained_package_roots_are_bounded(monkeypatch, tmp_path):
    from core.config import config
    from services.state.external_changes_scanner import ExternalChangesScanner
    monkeypatch.setattr(config, "http_remote_hosted", False)
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    (tmp_path / "Packages").mkdir()
    manifest = tmp_path / "Packages" / "manifest.json"
    manifest.write_text(json.dumps({"dependencies": {str(index): "file:../Package" + str(index) for index in range(4)}}))
    scanner = ExternalChangesScanner(scan_interval_ms=0, max_extra_roots=2)
    scanner.set_project_root("selected", str(tmp_path))
    scanner.update_and_get("selected")
    assert len(scanner._states["selected"].extra_roots) == 2
    smaller = ExternalChangesScanner(scan_interval_ms=0, max_manifest_bytes=8)
    smaller.set_project_root("selected", str(tmp_path))
    original_open = type(manifest).open
    reads = []
    def counted(path, *args, **kwargs):
        if path == manifest:
            reads.append(path)
        return original_open(path, *args, **kwargs)
    monkeypatch.setattr(type(manifest), "open", counted)
    smaller.update_and_get("selected")
    assert reads == []
    assert smaller._states["selected"].extra_roots == []


@pytest.mark.asyncio
async def test_local_public_job_still_schedules_nudge(monkeypatch, tmp_path, request):
    if _run_sdk_child(request, tmp_path):
        return
    from fastmcp import Client, FastMCP
    from unittest.mock import AsyncMock
    from core.config import config
    from services.tools import run_tests
    from transport.plugin_hub import PluginHub
    monkeypatch.setattr(config, "http_remote_hosted", False)
    monkeypatch.setattr(config, "transport_mode", "http")
    async def send(instance, command, params, **kwargs):
        return {"success": True, "data": {"job_id": "fixture-job", "status": "running", "last_update_unix_ms": 1, "progress": {"editor_is_focused": False}}}
    async def project(instance, user_id=None):
        return str(tmp_path)
    nudges = []
    release = asyncio.Event()
    async def focus(**kwargs):
        nudges.append(kwargs)
        await release.wait()
        return True
    monkeypatch.setattr(PluginHub, "send_command_for_instance", send)
    monkeypatch.setattr(run_tests, "_get_unity_project_path", project)
    monkeypatch.setattr(run_tests, "get_unity_instance_from_context", AsyncMock(return_value="Selected@fixture"))
    monkeypatch.setattr(run_tests, "nudge_unity_focus", focus)
    app = FastMCP("local-focus-contract")
    app.tool(name="get_test_job")(run_tests.get_test_job)
    async with Client(app) as client:
        result = await client.call_tool("get_test_job", {"job_id": "fixture-job"})
        assert json.loads(result.content[0].text)["success"] is True
        result = await client.call_tool("get_test_job", {"job_id": "fixture-job"})
        assert json.loads(result.content[0].text)["success"] is True
    release.set()
    if run_tests._background_tasks:
        await asyncio.gather(*run_tests._background_tasks)
    assert nudges == [{"unity_project_path": str(tmp_path), "force": True,
                       "focus_duration_s": run_tests.focus_nudge._DEFAULT_FOCUS_DURATION_S}]


def test_remote_scanner_guard_precedes_state_and_filesystem(monkeypatch):
    from core.config import config
    from services.state import external_changes_scanner as module

    monkeypatch.setattr(config, "http_remote_hosted", True)
    scanner = module.ExternalChangesScanner(scan_interval_ms=0)
    calls = []
    monkeypatch.setattr(scanner, "_get_state", lambda *args: calls.append("state") or module.ExternalChangesState())
    scanner.set_project_root("Tenant@fixture", "C:/fixture")
    scanner.clear_dirty("Tenant@fixture")
    assert scanner.update_and_get("Tenant@fixture")["external_changes_dirty"] is False
    assert scanner._states == {}
    assert calls == []


if __name__ == "__main__":
    import socket
    owned = Path(sys.argv[2]).resolve()
    owned.mkdir(parents=True, exist_ok=True)
    for key in list(os.environ):
        if key.startswith("UNITY_MCP_"):
            os.environ.pop(key)
    for key in ("HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "XDG_DATA_HOME", "XDG_CONFIG_HOME", "XDG_CACHE_HOME", "TEMP", "TMP", "UNITY_MCP_LOG_DIR", "UNITY_MCP_STATUS_DIR"):
        os.environ[key] = str(owned)
    os.environ["DISABLE_TELEMETRY"] = "1"
    os.environ["UNITY_MCP_SKIP_STARTUP_CONNECT"] = "1"
    os.environ["MCP_HOST_BOUNDARY_CHILD"] = "1"
    Path.home = classmethod(lambda cls: owned)
    def denied(*args, **kwargs):
        raise AssertionError("Application networking prohibited")
    def socketpair_only(original):
        def guarded(*args, **kwargs):
            caller = sys._getframe(1)
            if caller.f_code.co_name in ("_socketpair", "_fallback_socketpair", "socketpair") and Path(caller.f_code.co_filename) == Path(socket.__file__):
                return original(*args, **kwargs)
            return denied(*args, **kwargs)
        return guarded
    for name in ("connect", "connect_ex", "bind"):
        setattr(socket.socket, name, socketpair_only(getattr(socket.socket, name)))
    for name in ("create_connection", "getaddrinfo", "gethostbyname", "gethostbyname_ex", "gethostbyaddr"):
        setattr(socket, name, denied)
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
    _install_baseline_overlay()
    raise SystemExit(pytest.main(["-q", "-p", "no:cacheprovider", "--basetemp", str(owned / "pytest"), sys.argv[1]]))
