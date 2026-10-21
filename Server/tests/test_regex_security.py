import asyncio
import importlib
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from services.tools import bounded_regex
from services.tools.script_apply_edits import _apply_edits_locally
from tests.regex_probe import run_probe


@pytest.mark.parametrize("operation", ["find", "anchor", "replace"])
def test_pathological_patterns_stop_within_budget(operation, request):
    result = run_probe(operation)
    assert result["outcome"] == "timed_out"
    assert result["requested_timeouts"]
    assert all(0 < value <= 0.5 for value in result["requested_timeouts"])
    # End-to-end timing is advisory; the child has a separate 30-second runaway guard.
    request.node.user_properties.append(("regex_elapsed_ms", result["elapsed_ms"]))


@pytest.mark.parametrize("operation", ["find", "search", "replace"])
@pytest.mark.parametrize("delay,expected", [(0.0, 0.5), (9.8, 0.2)])
def test_engine_receives_remaining_request_budget(monkeypatch, operation, delay, expected):
    clock = SimpleNamespace(now=100.0)
    monkeypatch.setattr(bounded_regex, "monotonic", lambda: clock.now)
    budget = bounded_regex.WorkBudget()
    assert budget.deadline == 110.0
    clock.now += delay
    observed = []

    def timed_out(_text, *, timeout):
        observed.append(timeout)
        raise TimeoutError("engine deadline")

    engine = SimpleNamespace(finditer=timed_out, search=timed_out)
    monkeypatch.setattr(bounded_regex, "_compile", lambda *_: engine)
    with pytest.raises(TimeoutError, match="engine deadline"):
        if operation == "find":
            bounded_regex.find_matches("x", "x", budget=budget)
        elif operation == "search":
            bounded_regex.search("x", "x", budget=budget)
        else:
            bounded_regex.substitute("x", "y", "x", 0, budget=budget)
    assert observed == [pytest.approx(expected)]


def test_delayed_scheduler_cannot_restart_expired_budget(monkeypatch):
    clock = SimpleNamespace(now=100.0)
    monkeypatch.setattr(bounded_regex, "monotonic", lambda: clock.now)
    budget = bounded_regex.WorkBudget()
    engine_calls = []
    monkeypatch.setattr(bounded_regex, "_compile", lambda *args: engine_calls.append(args))
    clock.now = 111.0
    with pytest.raises(TimeoutError, match="total work"):
        bounded_regex.find_matches("x", "x", budget=budget)
    assert engine_calls == []


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
    [
        ("x" * 2049, "x"),
        ("x", "x" * (bounded_regex.MAX_TEXT_CHARS + 1)),
        ("a{999999999}", "a"),
        ("a{1,999999999}", "a"),
    ],
    ids=["pattern-size", "text-size", "repetition-size", "repetition-upper-bound"],
)
def test_oversized_pattern_or_input_is_rejected(pattern, text):
    with pytest.raises(ValueError):
        bounded_regex.search(pattern, text)
