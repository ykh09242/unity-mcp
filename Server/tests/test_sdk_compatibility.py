"""The shipped SDK set and optional adapters must describe the same runtime."""

import json
from importlib.metadata import version
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastmcp import Client, FastMCP


def test_release_dependencies_are_exactly_the_reviewed_sdk_set():
    import tomllib

    root = Path(__file__).resolve().parents[2]
    manifest = json.loads((root / "Server/src/core/sdk_compatibility.json").read_text())
    project = tomllib.loads((root / "Server/pyproject.toml").read_text())
    locked = tomllib.loads((root / "Server/uv.lock").read_text())
    for name, expected in manifest["packages"].items():
        assert f"{name}=={expected}" in project["project"]["dependencies"]
        assert [p["version"] for p in locked["package"] if p["name"] == name] == [expected]


@pytest.mark.parametrize("package", ["fastmcp", "mcp", "mcp-types"])
def test_both_optional_adapter_gates_refuse_an_unreviewed_dependency(monkeypatch, package):
    import transport.execution_receipts as receipts
    import transport.test_wait_binding as waits
    from services.execution_receipt_identity import ReceiptUnavailable

    def changed(name):
        return "999.0.0" if name.replace("_", "-") == package else version(name)

    monkeypatch.setattr(receipts, "version", changed)
    monkeypatch.setattr(waits, "version", changed)
    with pytest.raises(ReceiptUnavailable, match="execution_receipt_sdk_unsupported"):
        receipts.require_supported_sdk()
    with pytest.raises(RuntimeError, match="Unreviewed Tasks SDK"):
        waits._SdkResultKernel(FastMCP("unsupported"))


def test_sdk_report_distinguishes_reviewed_dependencies_from_enabled_features():
    from core.sdk_compatibility import runtime_sdk_report

    server = SimpleNamespace()
    disabled = runtime_sdk_report(server)
    assert disabled["sdk"]["supported"] is True
    assert all(item["state"] == "disabled" for item in disabled["features"].values())
    server._test_wait_tasks_requested = True
    server._test_job_tasks = SimpleNamespace(_closed=True)
    server._execution_receipts = SimpleNamespace(root=object(), store=None, closed=False)
    inactive = runtime_sdk_report(server)
    assert all(item["state"] == "inactive" for item in inactive["features"].values())
    server._test_job_tasks._closed = False
    server._execution_receipts.store = SimpleNamespace(available=True)
    active = runtime_sdk_report(server)
    assert all(item["state"] == "active" for item in active["features"].values())
    assert len(json.dumps(active)) < 4096
    server._execution_receipts.store.available = False
    assert runtime_sdk_report(server)["features"]["execution_receipts"]["state"] == "unavailable"


def test_sdk_report_sanitizes_metadata_and_never_reports_unsupported_features_active():
    from core.sdk_compatibility import runtime_sdk_report

    server = SimpleNamespace(
        _test_wait_tasks_requested=True, _test_job_tasks=SimpleNamespace(_closed=False)
    )
    report = runtime_sdk_report(server, version_reader=lambda _name: "/private/path\ncredential")
    assert set(report["sdk"]["resolved"].values()) == {None}
    assert report["sdk"]["supported"] is False
    assert report["sdk"]["next_action"] == "install_reviewed_sdk"
    assert report["features"]["wait_tasks"]["state"] == "sdk_unsupported"
    assert "private" not in json.dumps(report)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["2026-07-28", "legacy"])
async def test_runtime_doctor_is_available_without_selecting_or_contacting_unity(monkeypatch, mode):
    from core.config import config
    from services.resources import _serialize_pydantic
    from services.resources.doctor import get_runtime_doctor
    from transport.unity_instance_middleware import UnityInstanceMiddleware

    monkeypatch.setattr(config, "http_remote_hosted", False)
    server = FastMCP("runtime-doctor")
    middleware = UnityInstanceMiddleware()
    inject = AsyncMock(side_effect=AssertionError("runtime diagnostics contacted Unity"))
    monkeypatch.setattr(middleware, "_inject_unity_instance", inject)
    server.add_middleware(middleware)
    server.resource("mcpforunity://doctor/runtime")(_serialize_pydantic(get_runtime_doctor))
    async with Client(server, mode=mode, cache=False) as client:
        blocks = await client.read_resource("mcpforunity://doctor/runtime")
    report = json.loads(blocks[0].text)
    assert report["schema_version"] == "unity-mcp/runtime@1"
    assert report["sdk"]["supported"] is True
    assert report["features"]["wait_tasks"]["state"] == "disabled"
    inject.assert_not_called()


@pytest.mark.parametrize(
    "grants,uri,allowed",
    [
        (["read"], "mcpforunity://doctor/runtime", True),
        ([], "mcpforunity://doctor/runtime", False),
        (["edit"], "mcpforunity://doctor/runtime", False),
        (["read"], "mcpforunity://doctor/runtime/unknown", False),
    ],
)
def test_runtime_doctor_retains_exact_scoped_read_authority(grants, uri, allowed):
    from services.operation_permissions import (
        authorize_resource,
        parse_permissions,
        OperationPermissionError,
    )

    authority = parse_permissions({"unity_mcp_permissions": grants}, strict=False)
    if allowed:
        authorize_resource(authority, uri)
    else:
        with pytest.raises(OperationPermissionError):
            authorize_resource(authority, uri)
