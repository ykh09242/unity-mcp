"""Continuation receipts cannot turn handler-controlled data into replay authority."""

import time

import pytest

from transport.command_resume import CommandResumeInvocation, CommandResumeProtocolError
from transport.native_arbitration import NativeArbitrationRequest, NativeReply, decode_native_reply
from tests.test_http_command_outcome import command_hub, dispatch  # noqa: F401
from transport.plugin_hub import PluginHub
from services.operation_permissions import OperationPermissionError


def envelope(invocation, state, payload):
    return {
        "command_resume": {
            "version": 1,
            "invocation_id": invocation.invocation_id,
            "editor_session_id": invocation.editor_session_id,
            "state": state,
        },
        "payload": payload,
    }


def test_wire_attempts_retain_original_identity_payload_and_deadline():
    # Given an original command whose caller still owns its deadline.
    invocation = CommandResumeInvocation.create("a" * 32, time.monotonic() + 60)
    original = invocation.to_wire()
    invocation.dispatch()
    # When the transport is interrupted and prepares a continuation.
    invocation.interrupted()
    continued = invocation.to_wire()
    # Then only the transport action changes.
    assert continued == {**original, "action": "resume"}


@pytest.mark.parametrize("field", ["version", "invocation_id", "editor_session_id", "state"])
def test_unbound_resume_proof_cannot_authorize_another_attempt(field):
    # Given a negotiated invocation and an unrelated receipt.
    invocation = CommandResumeInvocation.create("a" * 32, time.monotonic() + 60)
    value = envelope(invocation, "queued", {})
    value["command_resume"][field] = "unbound"
    # When the receipt is decoded, then the binding fails closed.
    with pytest.raises(CommandResumeProtocolError):
        invocation.unwrap(value)


def test_completed_business_payload_is_unwrapped_without_interpreting_lookalikes():
    # Given a completed receipt wrapping the current native proof.
    invocation = CommandResumeInvocation.create("a" * 32, time.monotonic() + 60)
    arbitration = NativeArbitrationRequest.create("b" * 32)
    business = {"command_resume": {"state": "queued"}, "native_arbitration": {"retryable": True}}
    native = {
        "native_arbitration": {
            **arbitration.to_wire(),
            "disposition": "started",
            "retryable": False,
        },
        "payload": business,
    }
    # When both negotiated transport layers are decoded.
    reply = decode_native_reply(
        invocation.unwrap(envelope(invocation, "completed", native)), arbitration
    )
    invocation.validate_reply(reply)
    # Then the exact business payload survives and grants no retry authority.
    assert reply.payload == business
    assert not reply.not_started


def test_cancel_interrupts_all_future_admission_actions():
    # Given an original queued invocation.
    invocation = CommandResumeInvocation.create("a" * 32, time.monotonic() + 60)
    # When its caller explicitly cancels.
    invocation.cancel()
    invocation.interrupted()
    # Then the retained worker can only deliver cancellation.
    assert invocation.cancelled.is_set()
    assert invocation.to_wire()["action"] == "cancel"


@pytest.mark.asyncio
async def test_permission_revocation_after_dispatch_cannot_become_reload_retry(command_hub):
    # Given a negotiated command whose authorization ends after dispatch begins.
    socket = command_hub.websocket
    socket.state.plugin_features = frozenset({"native_arbitration_v1", "command_resume_v1"})
    socket.state.plugin_resume_generation = "e" * 32
    socket.state.plugin_arbitration_generation = "a" * 32

    async def revoked(message):
        command_hub.sent.append(message)
        raise OperationPermissionError()

    socket.send_json = revoked
    # When the transport observes that permission failure.
    result = await dispatch()
    # Then it preserves execution uncertainty and does not readmit the command.
    assert result["data"]["reason"] == "outcome_unknown"
    assert result["data"]["cause"] == "permission_ended"
    assert len(command_hub.sent) == 1
    assert not PluginHub._pending


@pytest.mark.parametrize(
    "state,code",
    [("unknown", "command_outcome_unknown"), ("unavailable", "completed_result_not_retained")],
)
def test_native_terminal_uncertainty_keeps_structured_outcome(state, code):
    # Given the native fallback error shape rather than an MCP-shaped handler response.
    invocation = CommandResumeInvocation.create("a" * 32, time.monotonic() + 60)
    native = NativeReply({"status": "error", "code": code, "error": "Original result unavailable"})
    invocation.state = state
    # When the negotiated terminal receipt crosses a transport adapter.
    result = invocation.normalize_reply("manage_scene", native).payload
    # Then callers can distinguish uncertainty and completed-but-uncached execution.
    assert result["data"]["reason"] == "outcome_unknown"
    assert result["hint"] == "inspect_state_before_retry"
    assert result["code"] == code
    if state == "unavailable":
        assert result["data"]["execution"] == "completed"
