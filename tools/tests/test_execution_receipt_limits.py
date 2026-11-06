"""Operator-facing receipt limits must match the running implementation."""

from pathlib import Path

from models.response_limits import MAX_RESPONSE_RETAINED_BYTES
from services.execution_receipt_identity import (
    MAX_ARGUMENT_BYTES,
    MAX_ARGUMENT_DEPTH,
    MAX_ARGUMENT_NODES,
)
from transport.execution_receipts import EXECUTION_SECONDS, RESULT_BYTES


ROOT = Path(__file__).resolve().parents[2]


def test_documented_receipt_limit_table_matches_runtime_constants():
    text = (ROOT / "docs/execution-receipts-ledger.md").read_text(encoding="utf-8")
    for name, value in {
        "EXECUTION_SECONDS": EXECUTION_SECONDS,
        "MAX_ARGUMENT_BYTES": MAX_ARGUMENT_BYTES,
        "MAX_ARGUMENT_NODES": MAX_ARGUMENT_NODES,
        "MAX_ARGUMENT_DEPTH": MAX_ARGUMENT_DEPTH,
        "MAX_RESPONSE_RETAINED_BYTES": MAX_RESPONSE_RETAINED_BYTES,
        "RESULT_BYTES": RESULT_BYTES,
    }.items():
        assert f"| `{name}` | {value:,} |" in text, name
