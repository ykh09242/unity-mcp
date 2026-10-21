from pathlib import Path
import json
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tools.phase_timing_report import summarize


def test_join_uses_only_generated_token_and_omits_sensitive_or_missing_fields(tmp_path):
    rows = [
        {
            "event": "server_phase_timing",
            "correlation": "a" * 32,
            "native_requested": True,
            "outcome": "canceled",
            "total_ms": 30,
            "preflight_ms": 2,
            "secret": "private/path",
        },
        {
            "event": "phase_timing",
            "correlation": "a" * 32,
            "correlation_source": "server",
            "delivery": "canceled",
            "execution_settled": True,
            "execution_settled_after_delivery": True,
            "total_ms": 29,
            "handler_ms": 60,
            "import_ms": 12,
        },
        {
            "event": "server_phase_timing",
            "correlation": "b" * 32,
            "native_requested": False,
            "outcome": "returned",
            "total_ms": 4,
        },
    ]
    path = tmp_path / "local.log"
    path.write_text("\n".join("INFO phase_timing " + json.dumps(row) for row in rows))
    report = summarize([path])
    assert report["paired"] == 1 and report["server_only"] == 1
    assert report["late_execution"] == 1
    assert report["phase_ms"]["native.import_ms"]["p50"] == 12
    assert "native.queue_ms" not in report["phase_ms"]
    assert "private" not in str(report) and "a" * 32 not in str(report)


def test_ambiguous_overlong_invalid_and_bounded_inputs_are_observable(tmp_path):
    path = tmp_path / "local.log"
    valid = {
        "event": "server_phase_timing",
        "correlation": "a" * 32,
        "native_requested": False,
        "outcome": "returned",
        "total_ms": 5,
    }
    path.write_text(
        "x" * 20000
        + "\n"
        + "\n".join(
            json.dumps(row)
            for row in [
                valid,
                valid,
                dict(valid, correlation="b" * 32),
                dict(valid, total_ms=float("nan")),
            ]
        )
    )
    result = summarize([path], max_traces=1)
    assert result["ambiguous_traces"] == 1 and result["duplicate_records"] == 1
    assert result["overflow_records"] == 1 and result["ignored_lines"] == 2
    assert result["phase_ms"] == {}


def test_malformed_scalar_types_are_ignored(tmp_path):
    row = {
        "event": "server_phase_timing",
        "correlation": "c" * 32,
        "native_requested": False,
        "outcome": "returned",
        "total_ms": 1,
    }
    path = tmp_path / "bad.log"
    path.write_text(
        "\n".join(
            json.dumps(bad)
            for bad in [
                dict(row, outcome={}),
                dict(row, total_ms=10**500),
                dict(row, total_ms=True),
            ]
        )
    )
    report = summarize([path])
    assert report["ignored_lines"] == 3 and report["phase_ms"] == {}
