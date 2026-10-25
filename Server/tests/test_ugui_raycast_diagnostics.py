"""UI09 closed-contract proposals. All checks remain UNRUN until slot dispatch."""

import copy

import pytest

from models.ugui_raycast_diagnostics import (
    LIMITATIONS,
    UnsupportedRaycastResponse,
    validate_ugui_raycast_response,
)


def sample():
    return {
        "success": True,
        "message": "raycast_evidence",
        "data": {
            "kind": "ugui_raycast_diagnostics",
            "schema_version": 1,
            "framework": "ugui",
            "mode": "play",
            "frame": 12,
            "target_id": 7,
            "screen_position": [100.0, 80.0],
            "coordinate_system": "current_screen_pixels_bottom_left",
            "display": 0,
            "status": "observed",
            "reason": None,
            "policy": {
                "allow_custom_filters": False,
                "custom_filters_present": False,
                "native_query_attempted": True,
                "user_code_may_have_run": False,
                "callback_invocations": "not_instrumented",
                "package_explicit_event_dispatch": False,
                "package_explicit_source_edits": False,
            },
            "input": {
                "event_system_id": 3,
                "current_module_type": None,
                "current_module_active": None,
                "delivery": "not_attempted",
            },
            "hits": [
                {
                    "rank": 0,
                    "game_object_id": 7,
                    "name": "Button",
                    "raycaster_id": 4,
                    "root_raycaster_id": 4,
                    "event_camera_id": None,
                    "sorting_layer_id": 0,
                    "sorting_layer_value": 0,
                    "sorting_order": 0,
                    "depth": 1,
                    "distance": 0.0,
                    "index": 0.0,
                    "display_index": 0,
                    "screen_position": [100.0, 80.0],
                }
            ],
            "hits_total": 1,
            "hits_truncated": False,
            "first_hit_targets_handler": True,
            "budget": {
                "max_nodes": 200,
                "max_depth": 10,
                "max_evaluations": 20000,
                "max_characters": 65536,
                "raycasters_inspected": 1,
                "graphics_inspected": 1,
                "evaluations": 100,
                "characters_reserved": 256,
            },
            "limitations": list(LIMITATIONS),
        },
    }


def refusal():
    reply = sample()
    data = reply["data"]
    data.update(
        status="unknown",
        reason="custom_filters_not_allowed",
        hits=[],
        hits_total=None,
        hits_truncated=False,
        first_hit_targets_handler=None,
    )
    data["policy"].update(custom_filters_present=True, native_query_attempted=False)
    return reply


def opted_in_failure():
    reply = refusal()
    reply.update(success=False, message="raycast_query_failed")
    reply["data"].update(status="failed", reason="native_query_threw")
    reply["data"]["policy"].update(
        allow_custom_filters=True, native_query_attempted=True, user_code_may_have_run=True
    )
    return reply


def test_hits_without_module_are_not_delivery_and_default_refusal_keeps_policy():
    # Given: distinct native query, configuration and refusal evidence.
    for reply in (sample(), refusal(), opted_in_failure()):
        # When: the actual closed decoder parses the receipt.
        decoded = validate_ugui_raycast_response(reply)
        # Then: no invented callback count or delivery claim replaces it.
        assert decoded == reply
        assert decoded["data"]["input"]["delivery"] == "not_attempted"


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("success",), False),
        (("data", "schema_version"), True),
        (("data", "target_id"), 0),
        (("data", "policy", "allow_custom_filters"), "yes"),
        (("data", "policy", "custom_filters_present"), True),
        (("data", "policy", "user_code_may_have_run"), True),
        (("data", "policy", "callback_invocations"), 0),
        (("data", "policy", "package_explicit_event_dispatch"), True),
        (("data", "policy", "package_explicit_source_edits"), True),
        (("data", "input", "delivery"), "guaranteed"),
        (("data", "input", "current_module_active"), True),
        (("data", "hits_total"), 0),
        (("data", "hits_truncated"), True),
        (("data", "budget", "evaluations"), 20001),
        (("data", "budget", "characters_reserved"), 0),
        (("data", "budget", "graphics_inspected"), 201),
        (("data", "status"), "unknown"),
    ],
)
def test_contradictory_policy_scope_and_bounds_are_refused_without_echo(path, value):
    # Given: a peer receipt contradicts its own observation or policy.
    reply = sample()
    cursor = reply
    for key in path[:-1]:
        cursor = cursor[key]
    cursor[path[-1]] = value
    # When/Then: reject with the fixed boundary error.
    with pytest.raises(UnsupportedRaycastResponse, match="^unsupported_raycast_response$"):
        validate_ugui_raycast_response(reply)


@pytest.mark.parametrize("field", ["rank", "name", "distance", "game_object_id", "display_index"])
def test_hit_identity_metadata_and_numeric_boundaries(field):
    # Given: malformed actual-hit metadata.
    reply = sample()
    invalid = {
        "rank": 1,
        "name": "\U0001f600" * 200,
        "distance": float("nan"),
        "game_object_id": False,
        "display_index": 1,
    }
    reply["data"]["hits"][0][field] = invalid[field]
    # When/Then: no lossy normalization is admitted.
    with pytest.raises(UnsupportedRaycastResponse):
        validate_ugui_raycast_response(reply)


def test_thrown_callback_is_not_normalized_to_success_or_callback_free():
    # Given: a native failure after permission to evaluate user code.
    reply = opted_in_failure()
    assert validate_ugui_raycast_response(reply) == reply
    for key, value in (("native_query_attempted", False), ("user_code_may_have_run", False)):
        bad = copy.deepcopy(reply)
        bad["data"]["policy"][key] = value
        # When/Then: the attempted user-code boundary cannot be erased.
        with pytest.raises(UnsupportedRaycastResponse):
            validate_ugui_raycast_response(bad)


def test_tail_truncation_does_not_invent_missing_target_rank():
    # Given: a completed native order with a bounded returned prefix.
    reply = sample()
    hit = reply["data"]["hits"][0]
    reply["data"]["hits"] = [
        dict(copy.deepcopy(hit), rank=rank, index=float(rank), game_object_id=100 + rank)
        for rank in range(128)
    ]
    reply["data"].update(hits_total=200, hits_truncated=True)
    reply["data"]["budget"].update(
        graphics_inspected=200, evaluations=1000, characters_reserved=32768
    )
    # When/Then: preserve known first-hit evidence, without claiming the omitted tail.
    assert validate_ugui_raycast_response(reply) == reply


def test_closed_unknown_fields_do_not_publish_private_exception_text():
    reply = opted_in_failure()
    reply["data"]["exception"] = "PRIVATE_SENTINEL"
    with pytest.raises(UnsupportedRaycastResponse) as caught:
        validate_ugui_raycast_response(reply)
    assert "PRIVATE_SENTINEL" not in str(caught.value)


def test_unsupported_graphic_preserves_culprit_and_prior_input_observations():
    reply = sample()
    reply["data"].update(
        status="unknown",
        reason="unsupported_graphic",
        hits=[],
        hits_total=None,
        first_hit_targets_handler=None,
        unsupported_component={
            "instance_id": 11,
            "component_type": "Custom.Graphic",
            "path": "Canvas/Input/Caret",
            "path_truncated": False,
        },
    )
    reply["data"]["policy"].update(native_query_attempted=False, custom_filters_present=None)
    reply["data"]["budget"]["characters_reserved"] = 512
    result = validate_ugui_raycast_response(reply)
    assert result == reply
    assert result["data"]["input"]["event_system_id"] == 3
    assert result["data"]["first_hit_targets_handler"] is None
    reply["data"]["unsupported_component"]["path"] = "x" * 257
    with pytest.raises(UnsupportedRaycastResponse):
        validate_ugui_raycast_response(reply)
