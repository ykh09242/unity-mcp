"""Large input and output remain admitted under independent retained budgets."""

import pytest

from models.response_limits import response_size
from services.execution_receipt_identity import owned_json, ReceiptInvalid
from transport.plugin_hub import PluginHub


def test_large_numeric_result_is_not_rejected_by_incidental_node_limit():
    assert response_size({"data": list(range(120_000))}) is not None


def test_large_ascii_response_keeps_bounded_sizing_path():
    assert response_size({"data": "x" * (33 * 1024 * 1024)}) is not None


def test_command_bytes_and_retained_ownership_are_separate_limits():
    args = {"source": "x" * (5 * 1024 * 1024)}
    charge = PluginHub._command_payload_size("manage_script", args)
    assert charge is not None
    assert 5 * 1024 * 1024 < charge <= PluginHub.MAX_PENDING_PAYLOAD_BYTES_PER_SESSION
    owned = owned_json(args)
    assert owned == args and owned is not args


def test_large_graph_admission_and_finite_refusal():
    args = {"values": [None] * 120_000}
    assert PluginHub._command_payload_size("batch_execute", args) is not None
    assert owned_json(args) == args
    too_wide = {"values": [None] * 1_000_001}
    assert PluginHub._command_payload_size("batch_execute", too_wide) is None
    assert response_size(too_wide) is None
    with pytest.raises(ReceiptInvalid, match="graph_limit"):
        owned_json(too_wide)
