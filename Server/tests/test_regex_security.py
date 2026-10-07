import asyncio
import importlib
import time
from unittest.mock import AsyncMock

import pytest

from services.tools import bounded_regex
from services.tools.script_apply_edits import _apply_edits_locally, _find_best_anchor_match


@pytest.mark.parametrize("operation", ["find", "anchor", "replace"])
def test_pathological_patterns_stop_within_budget(operation):
    text = "a" * 30_000 + "!"
    started = time.monotonic()
    with pytest.raises(TimeoutError):
        if operation == "find":
            bounded_regex.find_matches(r"(a+)+$", text)
        elif operation == "anchor":
            _find_best_anchor_match(r"(a+)+$", text, 0)
        else:
            bounded_regex.substitute(r"(a+)+$", "x", text, 0)
    assert time.monotonic() - started < 2


def test_empty_pattern_does_not_materialize_unbounded_matches():
    with pytest.raises(ValueError, match="match limit"):
        bounded_regex.find_matches("", "x" * 100_000)


@pytest.mark.asyncio
async def test_local_replacement_preserves_backreferences():
    assert (
        await _apply_edits_locally(
            "name=abc",
            [{"op": "regex_replace", "pattern": r"name=(\w+)", "replacement": "$1=value"}],
        )
        == "abc=value"
    )


@pytest.mark.asyncio
async def test_search_timeout_returns_error_and_event_loop_stays_responsive(monkeypatch):
    module = importlib.import_module("services.tools.find_in_file")
    monkeypatch.setattr(module, "get_unity_instance_from_context", AsyncMock(return_value=None))
    monkeypatch.setattr(
        module,
        "send_with_unity_instance",
        AsyncMock(return_value={"success": True, "data": {"contents": "a" * 30_000 + "!"}}),
    )
    ticks = 0

    async def heartbeat():
        nonlocal ticks
        for _ in range(5):
            await asyncio.sleep(0.005)
            ticks += 1

    pulse = asyncio.create_task(heartbeat())
    response = await module.find_in_file(AsyncMock(), "Assets/Test.cs", r"(a+)+$")
    assert ticks > 0, "Regex matching must not block the event loop"
    await pulse
    assert response["success"] is False
    assert "timed out" in response["message"]
    assert ticks == 5


@pytest.mark.parametrize(
    "pattern,text",
    [("x" * 2049, "x"), ("x", "x" * 2_000_001), ("a{999999999}", "a"), ("a{1,999999999}", "a")],
    ids=["pattern-size", "text-size", "repetition-size", "repetition-upper-bound"],
)
def test_oversized_pattern_or_input_is_rejected(pattern, text):
    with pytest.raises(ValueError):
        bounded_regex.search(pattern, text)
