"""Telemetry reports response outcomes without inspecting private response content."""

import asyncio
import json
from unittest.mock import Mock

import pytest

import core.telemetry as telemetry
import core.telemetry_decorator as decorators
from models.models import MCPResponse


SECRET = "private-key=fixture-secret C:/private/project.asset"


@pytest.fixture
def captured(monkeypatch):
    records, milestones = [], []
    monkeypatch.setattr(telemetry, "record_telemetry", lambda kind, data: records.append(data))
    monkeypatch.setattr(decorators, "record_milestone", milestones.append)
    return records, milestones


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("resource", [False, True])
@pytest.mark.parametrize(
    ("response", "outcome"),
    [
        ({"success": False, "error": SECRET}, "domain_error"),
        (MCPResponse(success=False, error=SECRET), "domain_error"),
        ({"success": True, "data": SECRET}, "success"),
        (MCPResponse(success=True, data=SECRET), "success"),
        (
            {"success": False, "data": {"reason": "outcome_unknown", "key": SECRET}},
            "outcome_unknown",
        ),
        (MCPResponse(success=False, data={"reason": "outcome_unknown"}), "outcome_unknown"),
        ({"success": False, "data": {"reason": "command_deadline_exceeded"}}, "timeout"),
        ({"success": False, "data": {"reason": SECRET}}, "domain_error"),
        ({"success": False, "error": "timeout " + SECRET}, "domain_error"),
        ({"success": False, "data": {"nested": {"reason": "outcome_unknown"}}}, "domain_error"),
        ({"success": "false", "data": SECRET}, "outcome_unknown"),
        (SECRET, "success"),
        ({"data": SECRET}, "success"),
        (None, "success"),
    ],
)
def test_return_outcomes(captured, asynchronous, resource, response, outcome):
    records, milestones = captured

    def sync_call():
        return response

    async def async_call():
        return response

    decorate = decorators.telemetry_resource if resource else decorators.telemetry_tool
    wrapped = decorate("manage_scene")(async_call if asynchronous else sync_call)
    result = asyncio.run(wrapped()) if asynchronous else wrapped()

    assert result is response
    assert len(records) == 1
    assert records[0]["success"] is (outcome == "success")
    assert records[0]["outcome"] == outcome
    assert SECRET not in json.dumps(records)
    assert bool(milestones) is (outcome == "success" and not resource)


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("resource", [False, True])
@pytest.mark.parametrize(
    ("exception", "outcome", "error"),
    [
        (ValueError(SECRET), "exception", "ValueError"),
        (TimeoutError(SECRET), "timeout", "TimeoutError"),
        (asyncio.CancelledError(SECRET), "canceled", None),
    ],
)
def test_exceptions_propagate_with_safe_outcome(
    captured, asynchronous, resource, exception, outcome, error
):
    records, milestones = captured

    def sync_call():
        raise exception

    async def async_call():
        raise exception

    decorate = decorators.telemetry_resource if resource else decorators.telemetry_tool
    wrapped = decorate("manage_scene")(async_call if asynchronous else sync_call)
    with pytest.raises(type(exception)) as raised:
        asyncio.run(wrapped()) if asynchronous else wrapped()

    assert raised.value is exception
    assert records[0]["success"] is False
    assert records[0]["outcome"] == outcome
    assert records[0].get("error") == error
    assert SECRET not in json.dumps(records)
    assert milestones == []


def test_arbitrary_object_is_not_serialized(captured):
    class PrivateResponse:
        @property
        def __class__(self):
            raise AssertionError("Arbitrary class properties must not be evaluated")

        @property
        def success(self):
            raise AssertionError("Arbitrary properties must not be evaluated")

        def __str__(self):
            raise AssertionError("Arbitrary values must not be formatted")

    response = PrivateResponse()
    assert decorators.telemetry_tool("manage_scene")(lambda: response)() is response
    assert captured[0][0]["outcome"] == "success"


def test_telemetry_failure_does_not_replace_response_or_cancellation(monkeypatch):
    monkeypatch.setattr(decorators, "record_tool_usage", Mock(side_effect=ValueError(SECRET)))
    monkeypatch.setattr(decorators, "record_milestone", Mock(side_effect=ValueError(SECRET)))
    response = {"success": False}
    assert decorators.telemetry_tool("manage_scene")(lambda: response)() is response

    async def canceled():
        raise asyncio.CancelledError(SECRET)

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(decorators.telemetry_tool("manage_scene")(canceled)())


def test_direct_outcome_input_is_allowlisted(captured):
    telemetry.record_tool_usage("fixture", False, 1, outcome=SECRET)
    telemetry.record_resource_usage("fixture", False, 1, outcome=SECRET)
    assert all(item["outcome"] == "outcome_unknown" for item in captured[0])
    assert SECRET not in json.dumps(captured[0])


def test_malformed_controls_do_not_invoke_caller_hooks(captured):
    class PrivateLabel(str):
        def __eq__(self, other):
            raise AssertionError("Caller equality must not run")

        def __bool__(self):
            raise AssertionError("Caller truthiness must not run")

    class PrivateDict(dict):
        def __contains__(self, key):
            raise AssertionError("Caller mapping hooks must not run")

    responses = [
        ({"success": PrivateLabel("true")}, "outcome_unknown"),
        ({"success": 1}, "outcome_unknown"),
        ({"success": 0}, "outcome_unknown"),
        ({"success": False, "data": {"reason": PrivateLabel("outcome_unknown")}}, "domain_error"),
        (PrivateDict(success=False), "success"),
    ]
    for response, outcome in responses:
        assert (
            decorators.telemetry_tool("fixture")(lambda response=response: response)() is response
        )
        assert captured[0][-1]["outcome"] == outcome
    telemetry.record_tool_usage("fixture", False, 1, outcome=PrivateLabel(SECRET))
    assert captured[0][-1]["outcome"] == "outcome_unknown"


def test_disabled_collector_drops_classified_events(monkeypatch):
    from types import SimpleNamespace

    monkeypatch.setattr(telemetry, "TelemetryConfig", lambda: SimpleNamespace(enabled=False))
    collector = telemetry.TelemetryCollector()
    monkeypatch.setattr(telemetry, "get_telemetry", lambda: collector)
    result = {"success": False, "data": {"reason": "outcome_unknown"}}
    assert decorators.telemetry_tool("manage_scene")(lambda: result)() is result
    assert collector._queue.empty()
    assert collector._worker is None
    assert collector._milestones == {}


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("resource", [False, True])
def test_repository_response_subclass_false(captured, asynchronous, resource):
    from services.resources.tags import TagsResponse

    response = TagsResponse(success=False, error=SECRET)

    def sync_call():
        return response

    async def async_call():
        return response

    decorate = decorators.telemetry_resource if resource else decorators.telemetry_tool
    wrapped = decorate("manage_scene")(async_call if asynchronous else sync_call)
    assert (asyncio.run(wrapped()) if asynchronous else wrapped()) is response
    records, milestones = captured
    assert records[0]["success"] is False
    assert records[0]["outcome"] == "domain_error"
    assert SECRET not in json.dumps(records)
    assert milestones == []


@pytest.mark.parametrize("reason", ["outcome_unknown", "command_deadline_exceeded"])
def test_derived_response_uses_stored_controls_without_attribute_hooks(captured, reason):
    class StoredResponse(MCPResponse):
        def __getattribute__(self, name):
            if name in ("success", "data", "model_dump", "model_dump_json", "__dict__"):
                raise AssertionError("Response attribute hooks must not run")
            return super().__getattribute__(name)

    response = StoredResponse(success=False, data={"reason": reason, "key": SECRET})
    assert decorators.telemetry_tool("manage_scene")(lambda: response)() is response
    records, milestones = captured
    assert records[0]["outcome"] == ("timeout" if reason == "command_deadline_exceeded" else reason)
    assert SECRET not in json.dumps(records)
    assert milestones == []


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("resource", [False, True])
def test_classifier_observation_failure_preserves_result(captured, asynchronous, resource):
    class PrivateKey:
        def __hash__(self):
            return hash("success")

        def __eq__(self, other):
            raise ValueError(SECRET)

    response = {PrivateKey(): SECRET}

    def sync_call():
        return response

    async def async_call():
        return response

    decorate = decorators.telemetry_resource if resource else decorators.telemetry_tool
    wrapped = decorate("manage_scene")(async_call if asynchronous else sync_call)
    assert (asyncio.run(wrapped()) if asynchronous else wrapped()) is response
    records, milestones = captured
    assert records[0]["outcome"] == "outcome_unknown"
    assert SECRET not in json.dumps(records)
    assert milestones == []
