"""Test admission distinguishes active, limited and unknown filesystem coverage."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from core.config import config
from services.resources import editor_state
from services.state import external_changes_scanner as scanner_module
from services.tools import preflight


@pytest.fixture
def local_scanner(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "http_remote_hosted", False)
    monkeypatch.setattr(scanner_module, "_in_pytest", lambda: False)
    monkeypatch.setattr(scanner_module, "_now_unix_ms", lambda: 1000)
    scanner = scanner_module.ExternalChangesScanner(max_scan_seconds=10, max_manifest_bytes=8)
    scanner.set_project_root("owned", str(tmp_path))
    (tmp_path / "Assets").mkdir()
    (tmp_path / "Packages").mkdir()
    yield scanner, tmp_path
    scanner.close()


@pytest.fixture
def admission(monkeypatch):
    clock = SimpleNamespace(now=0.0, observations=0, compiling=False, states=[])

    async def sleep(delay):
        clock.now += delay
        await asyncio.sleep(0)

    async def read(_ctx):
        clock.observations += 1
        assets = clock.states[0]()
        if len(clock.states) > 1:
            clock.states.pop(0)
        return {
            "success": True,
            "data": editor_state._enrich_advice_and_staleness(
                {
                    "compilation": {
                        "is_compiling": clock.compiling,
                        "is_domain_reload_pending": False,
                    },
                    "assets": assets,
                }
            ),
        }

    monkeypatch.setattr(preflight, "_in_pytest", lambda: False)
    monkeypatch.setattr(preflight, "time", SimpleNamespace(monotonic=lambda: clock.now))
    monkeypatch.setattr(preflight, "asyncio", SimpleNamespace(sleep=sleep))
    monkeypatch.setattr(editor_state, "get_editor_state_authoritative", read)
    return clock


async def admit():
    return await preflight.preflight(
        None, requires_no_tests=True, wait_for_no_compile=True, max_wait_s=3
    )


@pytest.mark.asyncio
async def test_finished_limited_scan_fails_closed_without_pending_retry(local_scanner, admission):
    # Given a completed filesystem pass with an unreadable-by-policy package manifest.
    scanner, root = local_scanner
    (root / "Packages/manifest.json").write_text('{"dependencies": {}}', encoding="utf-8")
    admission.states = [lambda: scanner.update_and_get("owned")]
    # When test admission observes the real scanner result.
    result = await admit()
    # Then waiting is not proposed as a remedy for finite coverage limitations.
    assert result.data["reason"] == "external_changes_scan_limited"
    assert result.data["retryable"] is False
    assert result.data["next_action"] == "inspect_scan_scope"
    assert result.data["scan"]["external_changes_scan_limited_reason"] == "manifest_byte_limit"
    assert result.data.get("retry_after_ms") is None
    assert admission.observations == 1
    assert admission.now == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("complete", [False, None])
async def test_idle_unknown_coverage_is_actionable_not_pending(admission, complete):
    # Given no verified coverage and no scan currently making progress.
    admission.states = [
        lambda: {
            "external_changes_scan_complete": complete,
            "external_changes_scan_in_progress": False,
        }
    ]
    # When admission checks the snapshot.
    result = await admit()
    # Then the caller must restore local scan availability before running tests.
    assert result.data["reason"] == "external_changes_scan_unknown"
    assert result.data["next_action"] == "inspect_scan_scope"
    assert result.data["retryable"] is False
    assert admission.observations == 1


@pytest.mark.asyncio
async def test_active_scan_progresses_to_stable_admission(local_scanner, admission):
    # Given a resumable filesystem scan with a deliberately small byte budget.
    scanner, root = local_scanner
    (root / "Assets/payload.bin").write_bytes(b"x" * 128)
    scanner._max_scan_bytes = 32
    admission.states = [lambda: scanner.update_and_get("owned")]
    # When subsequent readiness observations finish its bounded passes.
    result = await admit()
    # Then test admission succeeds only after verified coverage and stabilization.
    assert result is None
    assert admission.observations >= 5
    assert scanner.update_and_get("owned")["external_changes_scan_complete"] is True


@pytest.mark.asyncio
async def test_native_blocker_has_priority_over_limited_scan(local_scanner, admission):
    # Given a limited pass while Unity is compiling.
    scanner, root = local_scanner
    (root / "Packages/manifest.json").write_text('{"dependencies": {}}', encoding="utf-8")
    admission.states = [lambda: scanner.update_and_get("owned")]
    admission.compiling = True
    # When native readiness remains blocked throughout the bounded wait.
    result = await admit()
    # Then the active native blocker remains the immediate diagnostic.
    assert result.data["reason"] == "compiling"


@pytest.mark.asyncio
async def test_remote_unknown_coverage_keeps_existing_native_admission_policy(
    admission, monkeypatch
):
    # Given hosted mode deliberately disables local filesystem traversal.
    monkeypatch.setattr(config, "http_remote_hosted", True)
    monkeypatch.setattr(preflight, "remote_operation_allowed", AsyncMock(return_value=True))
    admission.states = [
        lambda: {"external_changes_scan_complete": None, "external_changes_scan_in_progress": None}
    ]
    # When native readiness is stable.
    result = await admit()
    # Then unavailable remote local-disk coverage does not invent a local scan requirement.
    assert result is None


@pytest.mark.parametrize(
    "progress,limited,expected",
    [
        (True, False, "wait_for_external_changes_scan"),
        (False, True, "inspect_scan_scope"),
        (False, False, "inspect_scan_scope"),
    ],
)
@pytest.mark.parametrize("compiling", [False, True])
def test_resource_scan_guidance_distinguishes_progress_and_native_priority(
    progress, limited, expected, compiling
):
    # Given independent native readiness and local coverage state.
    state = {
        "compilation": {"is_compiling": compiling},
        "assets": {
            "external_changes_scan_complete": False,
            "external_changes_scan_limited": limited,
            "external_changes_scan_in_progress": progress,
        },
    }
    # When canonical resource advice is derived.
    result = editor_state._enrich_advice_and_staleness(state)["advice"]
    # Then only active progress suggests polling; native blockers retain priority.
    assert result["recommended_next_action"] == ("retry_later" if compiling else expected)
    if not compiling and not progress:
        assert result["recommended_retry_after_ms"] is None
