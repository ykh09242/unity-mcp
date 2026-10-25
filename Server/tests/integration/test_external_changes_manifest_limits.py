"""A bounded manifest scan must expose incomplete local dependency coverage."""

import asyncio
import json

import pytest

from services.state import external_changes_scanner as module
from tests.timing import watchdog_timeout


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure,reason",
    [
        ("bytes", "manifest_byte_limit"),
        ("roots", "local_package_root_limit"),
        ("invalid", "manifest_unreadable_or_invalid"),
    ],
)
async def test_limited_manifest_stops_continuation_and_recovers_after_scope_repair(
    tmp_path, monkeypatch, failure, reason
):
    monkeypatch.setattr(module.config, "http_remote_hosted", False)
    monkeypatch.setattr(module, "_in_pytest", lambda: False)
    (tmp_path / "Assets").mkdir()
    (tmp_path / "Assets/asset.bin").write_bytes(b"x" * 128)
    for name in ("LocalA", "LocalB"):
        (tmp_path / name).mkdir()
        (tmp_path / name / "file.bin").write_bytes(b"local")
    manifest = tmp_path / "Packages/manifest.json"
    manifest.parent.mkdir()
    manifest.write_text(
        json.dumps({"dependencies": {"a": "file:../LocalA", "b": "file:../LocalB"}}),
        encoding="utf-8",
    )
    scanner = module.ExternalChangesScanner(scan_interval_ms=0, max_scan_bytes=32)
    scanner.set_project_root("selected", str(tmp_path))
    if failure == "bytes":
        scanner._max_manifest_bytes = 8
    elif failure == "roots":
        scanner._max_extra_roots = 1
    else:
        manifest.write_text("{broken", encoding="utf-8")
    try:
        first = await scanner.update_and_get_async("selected")
        assert first["external_changes_scan_complete"] is False
        runner = scanner._progress_task
        assert runner is not None
        await asyncio.wait_for(asyncio.shield(runner), watchdog_timeout(2))
        state = scanner._states["selected"]
        assert state.scan_complete is False
        assert state.scan_limited is True
        assert state.scan_limited_reason == reason
        assert state.cursor is None
        assert state.fingerprint is None
        future = scanner._worker_future
        await asyncio.sleep(0.025)
        assert scanner._worker_future is future
        assert scanner._progress_task is None

        manifest.write_text("{}", encoding="utf-8")
        scanner._max_manifest_bytes = 1024
        await scanner.update_and_get_async("selected")
        runner = scanner._progress_task
        if runner is not None:
            await asyncio.wait_for(asyncio.shield(runner), watchdog_timeout(2))
        assert state.scan_complete is True
        assert state.scan_limited is False
        assert state.scan_limited_reason is None
    finally:
        scanner.close()
