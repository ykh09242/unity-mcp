"""Substantial-workflow capacities remain fixed at admission, across both ledgers."""

import pytest

from models.response_limits import MAX_RESPONSE_BYTES, ResponseOwner
from transport.charge_ledger import ChargeLedger
from transport.large_result_assembler import (
    CHUNK_PAYLOAD_BYTES,
    MAX_RESULT_BYTES,
    LargeResultAssembler,
    LargeResultProtocolError,
)
from transport.plugin_hub import PluginHub


@pytest.mark.parametrize("scope", ["session", "user", "global"])
def test_actual_combined_raw_and_retained_boundary_reopens_after_owner_release(monkeypatch, scope):
    """Use numeric ledger charges to test GiB limits without allocating GiB payloads."""
    monkeypatch.setattr(PluginHub, "_retained_results", ChargeLedger())
    monkeypatch.setattr(PluginHub, "_raw_results", ChargeLedger())
    mib = 1024 * 1024
    target = {"user_id": "alice", "session_id": "target"}
    if scope == "session":
        retained = {"bytes": 512 * mib, **target}
        raw = {"bytes": 512 * mib - 1, **target}
    elif scope == "user":
        retained = {"bytes": 1024 * mib, "user_id": "alice", "session_id": "other"}
        raw = {"bytes": 512 * mib - 1, **target}
    else:
        retained = {"bytes": 1024 * mib, "user_id": "bob", "session_id": "other"}
        raw = {"bytes": 1024 * mib - 1, "user_id": "carol", "session_id": "another"}
    PluginHub._retained_results["source"] = retained
    PluginHub._raw_results["transfer"] = raw
    owner = ResponseOwner(entries=[(PluginHub._retained_results, "source")])
    assert PluginHub._has_result_capacity(target, 1)
    assert not PluginHub._has_result_capacity(target, 2)
    owner.release()
    assert PluginHub._has_result_capacity(target, 2)
    assert PluginHub._retained_results.total_bytes == 0
    assert PluginHub._raw_results.total_bytes == raw["bytes"]


@pytest.mark.parametrize("compressed", [False, True])
def test_client_declared_size_cannot_raise_result_admission(compressed):
    reservations = []
    assembler = LargeResultAssembler(
        lambda *_: True,
        lambda *args: reservations.append(args) or True,
        lambda *_: None,
        reserve_compressed=lambda *args: reservations.append(args) or True,
    )
    size = MAX_RESULT_BYTES + 1
    wire_size = 1024 if compressed else size
    with pytest.raises(LargeResultProtocolError, match="invalid_.*result_size"):
        assembler.begin(
            "owner",
            "11111111-2222-4333-8444-555555555555",
            wire_size,
            (wire_size + CHUNK_PAYLOAD_BYTES - 1) // CHUNK_PAYLOAD_BYTES,
            encoding="gzip" if compressed else "identity",
            decoded_bytes=size if compressed else None,
        )
    assert reservations == []
    assert assembler.retained_bytes == 0
    assert MAX_RESULT_BYTES == MAX_RESPONSE_BYTES == PluginHub.MAX_RESULT_BYTES


def test_maximum_transfer_reserves_working_bytes_against_combined_capacity(monkeypatch):
    command_id = "11111111-2222-4333-8444-555555555555"
    entry = {"user_id": "alice", "session_id": "target"}
    monkeypatch.setattr(PluginHub, "_pending", {command_id: entry})
    monkeypatch.setattr(PluginHub, "_retained_results", ChargeLedger())
    monkeypatch.setattr(PluginHub, "_raw_results", ChargeLedger())
    working = 1024 * 1024
    charge = 5 * MAX_RESPONSE_BYTES + 4096 + working
    # The existing result owner leaves exactly the transfer's estimate available.
    PluginHub._retained_results["owner"] = {
        **entry,
        "bytes": PluginHub.MAX_RETAINED_RESULT_BYTES_PER_SESSION - charge,
    }
    assert PluginHub._reserve_compressed_transfer(
        "generation", command_id, MAX_RESPONSE_BYTES, working
    )
    assert PluginHub._raw_results.total_bytes == charge
    assert not PluginHub._has_result_capacity(entry, 1)
    PluginHub._release_transfer("generation", command_id)
    assert PluginHub._has_result_capacity(entry, charge)
    assert PluginHub._raw_results.total_bytes == 0
