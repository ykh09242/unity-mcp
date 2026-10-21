"""Capture checkpoints describe loss and retained-file coverage without exposing IDs."""

import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tools.phase_timing_report import summarize


def checkpoint(session="a" * 32, **changes):
    return {
        "event": "phase_capture_coverage",
        "schema_version": 1,
        "session": session,
        "checkpoint": 2,
        "elapsed_ms": 30000,
        "sample_every": 64,
        "offered": 3,
        "written": 2,
        "write_failed": 0,
        "dropped": 1,
        "pending": 0,
        "drop_queue": 1,
        "drop_startup": 0,
        "drop_closed": 0,
        "drop_shutdown": 0,
        "checkpoint_write_failed": 0,
        "closed": True,
        **changes,
    }


def sample(sequence, session="a" * 32, **changes):
    return {
        "event": "server_phase_timing",
        "correlation": f"{sequence:032x}",
        "outcome": "returned",
        "native_requested": False,
        "total_ms": sequence,
        "capture_session": session,
        "capture_sequence": sequence,
        **changes,
    }


def report(tmp_path, rows, **options):
    path = tmp_path / "phase.log"
    path.write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")
    return summarize([path], **options)


def test_loss_counts_and_selected_file_coverage_are_separate(tmp_path):
    result = report(tmp_path, [sample(1), checkpoint(secret="private-path")])
    capture = result["capture_coverage"]
    assert capture["sessions"] == 1
    assert capture["offered"] == 3 and capture["written"] == 2 and capture["dropped"] == 1
    assert capture["capture_fraction"] == 2 / 3
    assert capture["selected_log_fraction"] == 1 / 2
    assert not capture["complete"]
    assert "a" * 32 not in str(result) and "private-path" not in str(result)
    assert result["phase_ms"]["server.total_ms"]["p50"] == 1


def test_unordered_rotations_repeated_checkpoints_and_complete_closed_window(tmp_path):
    latest = checkpoint(offered=2, dropped=0, drop_queue=0)
    old = checkpoint(
        checkpoint=1, elapsed_ms=1, offered=1, written=1, dropped=0, drop_queue=0, closed=False
    )
    result = report(tmp_path, [latest, sample(1), old, sample(2), latest])
    capture = result["capture_coverage"]
    assert capture["offered"] == 2 and capture["written"] == 2
    assert capture["capture_fraction"] == capture["selected_log_fraction"] == 1
    assert capture["complete"]


def test_missing_checkpoint_and_records_after_last_checkpoint_remain_unknown(tmp_path):
    result = report(
        tmp_path,
        [
            sample(1),
            checkpoint(offered=1, written=1, dropped=0, drop_queue=0, closed=False),
            sample(2),
            sample(3, session="b" * 32),
        ],
    )
    capture = result["capture_coverage"]
    assert capture["samples_after_checkpoint"] == 1
    assert capture["samples_without_checkpoint"] == 1
    assert not capture["complete"]
    legacy = report(tmp_path, [sample(1, capture_session=None, capture_sequence=None)])
    assert legacy["capture_coverage"]["sessions"] == 0
    assert legacy["capture_coverage"]["offered"] is None
    assert legacy["capture_coverage"]["capture_fraction"] is None
    assert not legacy["capture_coverage"]["complete"]


def test_counter_reset_is_a_new_session_and_retention_scans_are_not_summed(tmp_path):
    retention = {
        "scan_complete": True,
        "entries_scanned": 3,
        "owned_sessions": 2,
        "active_sessions": 1,
        "retained_bytes": 1200,
        "oldest_retained_age_seconds": 100,
        "deleted_sessions": 1,
        "cleanup_errors": 0,
    }
    first = checkpoint(offered=1, written=1, dropped=0, drop_queue=0, retention=retention)
    second = checkpoint(
        "b" * 32, offered=1, written=1, dropped=0, drop_queue=0, retention=retention
    )
    result = report(tmp_path, [sample(1), first, sample(1, "b" * 32, correlation="c" * 32), second])
    capture = result["capture_coverage"]
    assert capture["sessions"] == 2 and capture["offered"] == 2
    assert [window["retention"]["retained_bytes"] for window in capture["windows"]] == [1200, 1200]
    assert capture["complete"]


def test_conflicting_or_regressing_session_checkpoints_are_ambiguous(tmp_path):
    first = checkpoint()
    conflict = checkpoint(written=1, write_failed=1)
    result = report(tmp_path, [first, conflict, sample(1)])
    assert result["capture_coverage"]["ambiguous_sessions"] == 1
    assert not result["capture_coverage"]["complete"]
    regressed = report(
        tmp_path, [first, checkpoint(checkpoint=3, offered=1, written=1, dropped=0, drop_queue=0)]
    )
    assert regressed["capture_coverage"]["ambiguous_sessions"] == 1


def test_malformed_counters_and_capture_session_budget_are_explicit(tmp_path):
    result = report(
        tmp_path,
        [
            checkpoint(offered=True),
            checkpoint(pending=-1),
            checkpoint(written=5),
            checkpoint(),
            checkpoint("b" * 32),
        ],
        max_traces=1,
    )
    assert result["ignored_lines"] == 3
    assert result["capture_coverage"]["checkpoint_overflow"] == 1
    assert not result["capture_coverage"]["complete"]


def test_duplicate_capture_sequences_cannot_claim_complete_coverage(tmp_path):
    result = report(
        tmp_path,
        [
            checkpoint(offered=2, dropped=0, drop_queue=0),
            sample(1),
            sample(1, correlation="c" * 32),
        ],
    )
    capture = result["capture_coverage"]
    assert capture["duplicate_sample_sequences"] == 1
    assert not capture["complete"]


def test_old_checkpoint_regressions_and_conflicts_are_rejected_in_every_file_order(tmp_path):
    from itertools import permutations

    first = checkpoint(
        checkpoint=1, offered=2, written=2, dropped=0, drop_queue=0, elapsed_ms=10, closed=False
    )
    regressed = checkpoint(
        checkpoint=2, offered=1, written=1, dropped=0, drop_queue=0, elapsed_ms=20, closed=False
    )
    latest = checkpoint(checkpoint=3, offered=3, written=3, dropped=0, drop_queue=0, elapsed_ms=30)
    conflict = dict(first, offered=1, written=1)
    for points in ((first, regressed, latest), (first, conflict, latest)):
        for ordered in permutations(points):
            result = report(tmp_path, [*ordered, sample(1), sample(2), sample(3)])
            assert result["capture_coverage"]["ambiguous_sessions"] == 1, ordered
            assert not result["capture_coverage"]["complete"]


def test_later_drained_sample_is_not_a_checkpointed_write(tmp_path):
    point = checkpoint(offered=2, written=1, pending=1, dropped=0, drop_queue=0, closed=False)
    capture = report(tmp_path, [point, sample(2)])["capture_coverage"]
    assert capture["selected_samples_in_window"] == 0
    assert capture["samples_after_checkpoint"] == 1
    assert capture["selected_log_fraction"] == 0
    assert not capture["complete"]


def test_single_session_checkpoint_validation_budget_cannot_hide_an_unknown_tail(tmp_path):
    first = checkpoint(checkpoint=1)
    second = checkpoint(checkpoint=2)
    third = checkpoint(checkpoint=3)
    result = report(tmp_path, [first, second, third], max_traces=2)
    assert result["capture_coverage"]["checkpoint_overflow"] == 1
    assert not result["capture_coverage"]["complete"]
