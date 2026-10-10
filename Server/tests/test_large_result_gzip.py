"""Production C# incremental encoding and strict bounded gzip interoperability."""

import gzip
import importlib.util
import json
from pathlib import Path
import random
import subprocess
import tracemalloc

import pytest

from transport.large_result_assembler import (
    CHUNK_PAYLOAD_BYTES,
    COMPRESSION_THRESHOLD_BYTES,
    MAX_RESULT_BYTES,
    MAGIC,
    LargeResultAssembler,
    LargeResultProtocolError,
)
from transport.result_gzip import GZIP_WORKING_BYTES

CID = "01234567-89ab-cdef-0123-456789abcdef"


@pytest.fixture(scope="module")
def harness(tmp_path_factory):
    root = Path(__file__).resolve().parents[2]
    path = root / "tools/tests/fixtures/large_result_codec/measure.py"
    spec = importlib.util.spec_from_file_location("codec_measure", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module, module.build(tmp_path_factory.mktemp("gzip-codec"))


def emit(harness, tmp_path, payload, mode):
    module, executable = harness
    path = tmp_path / "owned.json"
    trace = tmp_path / "owned.frames"
    path.write_bytes(payload)
    subprocess.run(
        [executable[0], str(executable[1]), str(path), str(trace), mode],
        check=True,
        capture_output=True,
        text=True,
        timeout=20,
    )
    return list(module.read_frames(trace))


def assembly(compressed_budget=True, maximum=64 * 1024 * 1024):
    ledger = {}
    pending = {("owned", CID)}

    def reserve(owner, cid, size):
        ledger[owner, cid] = size
        return True

    def reserve_gzip(owner, cid, decoded, working):
        ledger[owner, cid] = decoded + working
        return True

    assembler = LargeResultAssembler(
        lambda owner, cid: (owner, cid) in pending,
        reserve,
        lambda owner, cid: ledger.pop((owner, cid)),
        max_total_bytes=maximum,
        reserve_compressed=reserve_gzip if compressed_budget else None,
    )
    return assembler, ledger, pending


def binary_frames(data):
    return [
        MAGIC
        + CID.encode()
        + offset.to_bytes(4, "big")
        + data[offset : offset + CHUNK_PAYLOAD_BYTES]
        for offset in range(0, len(data), CHUNK_PAYLOAD_BYTES)
    ]


def consume(records):
    assembler, ledger, _ = assembly()
    start = json.loads(records[0][1])
    assembler.begin(
        "owned",
        CID,
        start["total_bytes"],
        start["chunk_count"],
        encoding=start.get("encoding", "identity"),
        decoded_bytes=start.get("decoded_bytes"),
    )
    result = None
    for _, frame in records[1:]:
        result = assembler.feed("owned", frame)
    assembler.discard("owned", CID)
    assert ledger == {}
    return result.payload


def test_actual_csharp_gzip_reconstructs_identical_json_and_numeric_unicode_values(
    harness, tmp_path
):
    payload = json.dumps(
        {
            "type": "command_result",
            "id": CID,
            "result": {
                "large_integer": 9007199254740993,
                "negative_zero": -0.0,
                "unicode": "\ud55c\uae00😀",
                "escaped_surrogate": "\ud800",
                "body": "repeated-json" * 100000,
            },
        },
        ensure_ascii=True,
    ).encode()
    records = emit(harness, tmp_path, payload, "gzip_json")
    start = json.loads(records[0][1])
    assert start["encoding"] == "gzip"
    assert start["decoded_bytes"] == len(payload)
    restored = consume(records)
    assert restored == payload
    decoded = json.loads(restored)
    assert decoded["result"]["large_integer"] == 9007199254740993
    assert decoded["result"]["unicode"] == "\ud55c\uae00😀"
    assert decoded["result"]["escaped_surrogate"] == "\ud800"


@pytest.mark.parametrize("mode", ["gzip_negotiated_only", "gzip_policy_only", "string_json"])
def test_missing_negotiation_or_policy_keeps_uncompressed_identical_frames(harness, tmp_path, mode):
    payload = json.dumps({"body": "x" * COMPRESSION_THRESHOLD_BYTES}).encode()
    baseline = emit(harness, tmp_path, payload, "bytes_json")
    assert emit(harness, tmp_path, payload, mode) == baseline


def test_legacy_peer_keeps_single_original_text_bytes(harness, tmp_path):
    payload = json.dumps({"body": "\ud55c\uae00" * 200000}, ensure_ascii=False).encode()
    assert emit(harness, tmp_path, payload, "legacy") == [(0, payload)]


def test_incremental_utf8_matches_native_encoder_at_surrogate_and_frame_boundaries(
    harness, tmp_path
):
    # The C# fixture creates paired and standalone UTF-16 surrogates exactly at its encoding block boundary.
    assert emit(harness, tmp_path, b"{}", "edges_string") == emit(
        harness, tmp_path, b"{}", "edges_bytes"
    )


def test_small_or_low_gain_payload_does_not_use_gzip(harness, tmp_path):
    small = json.dumps({"body": "x" * (COMPRESSION_THRESHOLD_BYTES - 100)}).encode()
    records = emit(harness, tmp_path, small, "gzip_json")
    assert "encoding" not in json.loads(records[0][1])
    # Owned random bytes exercise low-gain rejection without any application data.
    rng = random.Random(708)
    import base64

    low_gain = json.dumps(
        {"body": base64.b64encode(rng.randbytes(COMPRESSION_THRESHOLD_BYTES)).decode()}
    ).encode()
    assert consume(emit(harness, tmp_path, low_gain, "gzip_json")) == low_gain


@pytest.mark.parametrize(
    "mutation", ["truncated", "crc", "trailing", "second_member", "bomb", "decoded_short"]
)
def test_malformed_gzip_or_advertised_size_releases_reservation(mutation):
    raw = b"x" * COMPRESSION_THRESHOLD_BYTES
    encoded = gzip.compress(raw, mtime=0)
    if mutation == "truncated":
        encoded = encoded[:-1]
    elif mutation == "crc":
        encoded = encoded[:-8] + bytes([encoded[-8] ^ 1]) + encoded[-7:]
    elif mutation == "trailing":
        encoded += b"trailing"
    elif mutation == "second_member":
        encoded += gzip.compress(b"extra", mtime=0)
    elif mutation == "bomb":
        encoded = gzip.compress(raw + b"x", mtime=0)
    declared = len(raw) + (1 if mutation == "decoded_short" else 0)
    assembler, ledger, _ = assembly()
    chunks = binary_frames(encoded)
    assembler.begin(
        "owned", CID, len(encoded), len(chunks), encoding="gzip", decoded_bytes=declared
    )
    with pytest.raises(LargeResultProtocolError, match="compression"):
        for frame in chunks:
            assembler.feed("owned", frame)
    assert ledger == {}
    assert assembler.retained_bytes == 0


def test_gzip_cannot_allocate_without_explicit_peak_reservation():
    assembler, ledger, _ = assembly(compressed_budget=False)
    with pytest.raises(LargeResultProtocolError, match="capacity"):
        assembler.begin(
            "owned", CID, 100, 1, encoding="gzip", decoded_bytes=COMPRESSION_THRESHOLD_BYTES
        )
    assert ledger == {}
    assert assembler.retained_bytes == 0


@pytest.mark.parametrize(
    "encoding,decoded",
    [
        ("br", COMPRESSION_THRESHOLD_BYTES),
        ("gzip", MAX_RESULT_BYTES + 1),
        ("gzip", True),
        ("gzip", None),
        ("identity", COMPRESSION_THRESHOLD_BYTES),
    ],
)
def test_invalid_compression_metadata_is_rejected_before_reservation(encoding, decoded):
    assembler, ledger, _ = assembly()
    with pytest.raises(LargeResultProtocolError):
        assembler.begin(
            "owned", CID, COMPRESSION_THRESHOLD_BYTES, 17, encoding=encoding, decoded_bytes=decoded
        )
    assert ledger == {}


def test_decompression_bomb_has_bounded_peak_beyond_admitted_buffer():
    raw = b"x" * (COMPRESSION_THRESHOLD_BYTES * 8)
    encoded = gzip.compress(raw, mtime=0)
    assembler, ledger, _ = assembly()
    chunks = binary_frames(encoded)
    tracemalloc.start()
    try:
        assembler.begin(
            "owned",
            CID,
            len(encoded),
            len(chunks),
            encoding="gzip",
            decoded_bytes=COMPRESSION_THRESHOLD_BYTES,
        )
        with pytest.raises(LargeResultProtocolError):
            for frame in chunks:
                assembler.feed("owned", frame)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert peak < COMPRESSION_THRESHOLD_BYTES + GZIP_WORKING_BYTES
    assert ledger == {}


def test_gzip_owner_invalidation_discards_partial_decoded_allocation():
    import os

    raw = os.urandom(COMPRESSION_THRESHOLD_BYTES)
    encoded = gzip.compress(raw, mtime=0)
    assembler, ledger, pending = assembly()
    chunks = binary_frames(encoded)
    assembler.begin(
        "owned", CID, len(encoded), len(chunks), encoding="gzip", decoded_bytes=len(raw)
    )
    assert assembler.feed("owned", chunks[0]) is None
    pending.clear()
    assert assembler.feed("owned", chunks[1]) is None
    assert ledger == {}
