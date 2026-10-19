"""Validation reports retain the Editor's actual engine and context limits."""

import importlib
from unittest.mock import AsyncMock

import pytest


scripts = importlib.import_module("services.tools.manage_script")


@pytest.mark.asyncio
@pytest.mark.parametrize("include_diagnostics", [False, True])
async def test_validation_preserves_engine_and_context_report(monkeypatch, include_diagnostics):
    # Given: a successful Editor validation that explicitly lacks project semantics.
    validation = {
        "requestedLevel": "standard",
        "effectiveLevel": "standard_heuristic",
        "engine": "heuristic",
        "capabilities": {"syntax": False, "projectCompilation": False},
        "context": {"scope": "single_source", "defines": False, "partialTypes": False},
        "limitations": ["Roslyn is not enabled."],
    }
    diagnostics = [{"line": 4, "col": 9, "severity": "warning", "message": "Expensive call"}]
    monkeypatch.setattr(scripts, "get_unity_instance_from_context", AsyncMock(return_value=None))
    monkeypatch.setattr(
        scripts,
        "send_with_unity_instance",
        AsyncMock(
            return_value={
                "success": True,
                "data": {"diagnostics": diagnostics, "validation": validation},
            }
        ),
    )
    # When: the public wrapper summarizes or includes the diagnostics.
    result = await scripts.validate_script(
        AsyncMock(), "Assets/Fixture.cs", "standard", include_diagnostics
    )
    # Then: callers can distinguish heuristic checks from compiler validation.
    assert result["data"]["validation"] == validation
    counts = result["data"]["summary"] if include_diagnostics else result["data"]
    assert counts["warnings"] == 1
    assert counts["errors"] == 0
    if include_diagnostics:
        assert result["data"]["diagnostics"] == diagnostics


@pytest.mark.asyncio
async def test_validation_legacy_editor_reports_unknown_engine(monkeypatch):
    # Given: an older Editor only reports diagnostics.
    monkeypatch.setattr(scripts, "get_unity_instance_from_context", AsyncMock(return_value=None))
    monkeypatch.setattr(
        scripts,
        "send_with_unity_instance",
        AsyncMock(return_value={"success": True, "data": {"diagnostics": []}}),
    )
    # When: the wrapper returns a compact validation summary.
    result = await scripts.validate_script(AsyncMock(), "Assets/Fixture.cs", "basic")
    # Then: a successful response does not assert that a compiler ran.
    assert result["data"]["validation"]["requestedLevel"] == "basic"
    assert result["data"]["validation"]["engine"] == "unknown"
    assert result["data"]["validation"]["effectiveLevel"] == "unknown"


@pytest.mark.asyncio
async def test_validation_failure_retains_precise_diagnostic_and_scope(monkeypatch):
    # Given: a failed native validation with a precise compiler location.
    failure = {
        "success": False,
        "message": "Validation failed.",
        "data": {
            "diagnostics": [
                {
                    "line": 3,
                    "col": 17,
                    "severity": "error",
                    "id": "CS0246",
                    "message": "Missing type",
                }
            ],
            "validation": {"engine": "roslyn", "context": {"scope": "single_source"}},
        },
    }
    monkeypatch.setattr(scripts, "get_unity_instance_from_context", AsyncMock(return_value=None))
    monkeypatch.setattr(scripts, "send_with_unity_instance", AsyncMock(return_value=failure))
    # When: validation fails through the public wrapper.
    result = await scripts.validate_script(AsyncMock(), "Assets/Fixture.cs", "standard", True)
    # Then: the diagnostic location and narrower context are available for repair.
    assert result == failure
