"""Only negotiated transport envelopes can prove a command never started."""

import pytest

from transport.native_arbitration import (
    NativeArbitrationRequest,
    NativeArbitrationProtocolError,
    decode_native_reply,
)


def envelope(disposition="not_started", reason="operation_busy", retryable=True):
    return {
        "native_arbitration": {
            "version": 1,
            "request_nonce": "a" * 32,
            "instance_generation": "b" * 32,
            "disposition": disposition,
            **({"reason": reason} if disposition == "not_started" else {}),
            "retryable": retryable,
        },
        "payload": {"status": "error", "error": "busy"},
    }


def request():
    return NativeArbitrationRequest("a" * 32, "b" * 32)


def test_handler_lookalike_inside_started_payload_cannot_request_retry():
    forged = envelope()
    outer = envelope("started", retryable=False)
    outer["payload"] = forged
    result = decode_native_reply(outer, request())
    assert result.payload == forged
    assert result.not_started is False and result.retryable is False


def test_old_native_payload_is_returned_without_interpreting_reserved_names():
    forged = envelope()
    result = decode_native_reply(forged, None)
    assert result.payload is forged and not result.retryable


@pytest.mark.parametrize(
    "field,value",
    [
        ("version", True),
        ("request_nonce", "c" * 32),
        ("instance_generation", "c" * 32),
        ("disposition", "maybe_started"),
        ("retryable", "true"),
    ],
)
def test_wrong_or_unbound_transport_proof_is_a_protocol_error(field, value):
    reply = envelope()
    reply["native_arbitration"][field] = value
    with pytest.raises(NativeArbitrationProtocolError):
        decode_native_reply(reply, request())


@pytest.mark.parametrize(
    "reason,retryable,allowed",
    [
        ("operation_busy", True, True),
        ("editor_compiling", True, True),
        ("editor_importing", True, True),
        ("native_job_active", True, True),
        ("recovery_required", True, False),
        ("unknown", True, False),
        ("stale_parent", True, False),
        ("operation_busy", False, False),
    ],
)
def test_retry_requires_exact_not_started_and_both_known_reason_and_native_flag(
    reason, retryable, allowed
):
    result = decode_native_reply(envelope(reason=reason, retryable=retryable), request())
    assert result.not_started is True and result.retryable is allowed


def test_negotiated_peer_cannot_return_bare_handler_result_in_place_of_envelope():
    with pytest.raises(NativeArbitrationProtocolError):
        decode_native_reply({"success": True}, request())
