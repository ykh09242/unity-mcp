"""Native dispatcher envelopes retain business values and response controls."""

import pytest

from models.unity_response import extract_unity_result, normalize_unity_response
from models.response_limits import MAX_RESPONSE_DEPTH, response_limit_error


@pytest.mark.parametrize(
    "payload",
    [
        [1, {"enabled": False}, None],
        [],
        "owned-result",
        "",
        42,
        0,
        1.5,
        True,
        False,
        None,
        {},
        {"data": [1, 2], "label": "owned"},
        {"message": "business", "code": 7, "label": "owned", "data": None},
        {"status": "business", "code": 7},
        {"_mcp_status": ["business"], "data": [], "label": "owned"},
    ],
)
def test_native_success_envelope_preserves_arbitrary_business_data(payload):
    response = normalize_unity_response({"status": "success", "result": payload})

    assert response["success"] is True
    assert response["data"] == payload
    assert type(response["data"]) is type(payload)
    assert normalize_unity_response(response) == response


@pytest.mark.parametrize(
    "payload",
    [
        {"success": True, "message": "Done", "data": False},
        {"success": False, "error": "Rejected", "code": 0, "hint": "retry", "data": []},
        {"_mcp_status": "pending", "_mcp_poll_interval": 0.1, "data": {"job_id": "owned"}},
        {"_mcp_status": "complete", "data": [1, 2]},
        {"_mcp_status": "error", "success": True, "error": "Rejected", "data": {}},
    ],
)
def test_native_envelope_retains_response_and_polling_controls(payload):
    assert normalize_unity_response({"status": "success", "result": payload}) == payload


def test_native_dispatch_failure_retains_metadata_and_diagnostics():
    response = normalize_unity_response(
        {"status": "error", "error": "Dispatch rejected", "code": "not_dispatched"}
    )

    assert response == {
        "success": False,
        "message": None,
        "error": "Dispatch rejected",
        "code": "not_dispatched",
        "data": None,
    }


@pytest.mark.parametrize("payload", [False, 0, "", [], {}, None])
def test_legacy_response_data_values_are_not_reinterpreted(payload):
    response = normalize_unity_response(
        {"status": "success", "result": {"message": "Done", "data": payload}}
    )

    assert response["message"] == "Done"
    assert response["data"] == payload
    assert type(response["data"]) is type(payload)


@pytest.mark.parametrize(
    "payload",
    [
        {"message": "business"},
        {"data": {"x": 1}},
        {"error": "business"},
        {"status": "business"},
        {"_mcp_status": "pending", "data": {"job_id": "owned"}},
        False,
        None,
        [],
    ],
)
def test_native_extraction_leaves_adaptation_to_custom_caller(payload):
    assert extract_unity_result({"status": "success", "result": payload}) == payload


def test_native_extraction_retains_dispatch_failures():
    response = extract_unity_result({"status": "error", "error": "Dispatch rejected", "code": 0})

    assert response["success"] is False
    assert response["error"] == "Dispatch rejected"
    assert response["code"] == 0


@pytest.mark.parametrize("adapt", [normalize_unity_response, extract_unity_result])
def test_native_envelope_bounds_apply_before_result_extraction(adapt):
    payload = []
    for _ in range(MAX_RESPONSE_DEPTH + 1):
        payload = [payload]

    assert adapt({"status": "success", "result": payload}) == response_limit_error()
