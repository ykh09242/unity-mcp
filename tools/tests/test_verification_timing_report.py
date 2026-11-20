import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tools.verification_timing_report import summarize_trials


def write_trial(root, number, duration, *, source="same"):
    identity, correlation = f"{number:032x}", f"{number + 100:032x}"
    report = {
        "schema": "verification_report@1",
        "run_id": identity,
        "source_token": source,
        "state": "passed",
        "project_scope": "project",
        "selection": {"paths": ["Assets/A.cs"]},
        "timing": {
            "schema": "verification_timing@1",
            "entry": "direct",
            "omitted_commands": 0,
            "phases_ms": {"scheduler_queue": duration},
            "players_ms": {},
            "commands": [
                {
                    "command": "run_tests",
                    "correlation": correlation,
                    "native_requested": True,
                    "outcome": "returned",
                    "total_ms": duration,
                }
            ],
        },
    }
    path = root / f"report-{number}.json"
    path.write_text(json.dumps({"success": True, "data": report}))
    return path, {
        "event": "phase_timing",
        "correlation_source": "server",
        "correlation": correlation,
        "delivery": "sent",
        "execution_settled": True,
        "execution_settled_after_delivery": False,
        "queue_ms": duration,
        "handler_ms": duration + 2,
        "total_ms": duration + 9,
    }


def test_same_input_condition_and_setup_per_operation_and_real_percentiles(tmp_path):
    a, first = write_trial(tmp_path, 1, 4)
    b, second = write_trial(tmp_path, 2, 8)
    c, _ = write_trial(tmp_path, 3, 10)
    d, _ = write_trial(tmp_path, 4, 20, source="changed")
    e, _ = write_trial(tmp_path, 5, 30)
    log = tmp_path / "local.jsonl"
    log.write_text("\n".join(json.dumps(row) for row in (first, second)))
    trials = [
        ("warm", "setupA", a),
        ("warm", "setupA", b),
        ("cold", "setupA", c),
        ("warm", "setupA", d),
        ("warm", "setupB", e),
    ]
    result = summarize_trials(trials, [log], operations=True)
    assert len(result["groups"]) == 4
    combined = next(group for group in result["groups"] if group["trials"] == 2)
    assert combined["matched_commands"] == 2
    assert combined["operations"][0]["commands"][0]["editor_ms"]["handler"] == 6
    for name in ("server.scheduler_queue", "run_tests.editor.queue"):
        assert combined["phase_ms"][name] == {"samples": 2, "median": 6, "p95": pytest.approx(7.8)}
    assert combined["phase_ms"]["run_tests.editor.import"] == {
        "samples": 0,
        "median": None,
        "p95": None,
    }


def test_duplicate_native_is_unknown_and_duplicate_run_refused(tmp_path):
    path, row = write_trial(tmp_path, 1, 4)
    log = tmp_path / "local.jsonl"
    log.write_text(json.dumps(row) + "\n" + json.dumps(row))
    entry = ("cold", "image-a", path)
    (group,) = summarize_trials([entry], [log])["groups"]
    assert group["ambiguous_commands"] == 1
    assert group["phase_ms"]["run_tests.editor.queue"]["median"] is None
    with pytest.raises(ValueError, match="duplicate run ID"):
        summarize_trials([entry, entry], [])
