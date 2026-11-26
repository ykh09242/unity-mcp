from pathlib import Path
from zipfile import ZipFile

import pytest

from tools.experiments.verify_agent_outcome_holdout import summary
from tools.experiments.verify_agent_outcome_holdout_confirmation import STRICT_ZERO
from tools.experiments.verify_compact_edit import host_error_records, read, verify_host_receipts
from tools.experiments.verify_compact_edit_confirmation import host_tool_calls, verified_archives
from tools.experiments.verify_compact_edit_followup import perfect


def test_extracted_product_must_match_frozen_archive(tmp_path):
    pins = {"package": "a" * 40, "runtime": "b" * 40}
    for key, dirname, filename in (
        ("package", "dependencies", "mcp.zip"),
        ("runtime", "product", "source.zip"),
    ):
        directory = tmp_path / dirname
        directory.mkdir()
        (directory / "fixture").write_bytes(b"frozen")
        with ZipFile(directory / filename, "w") as archive:
            archive.comment = pins[key].encode()
            archive.writestr("fixture", b"frozen")
    assert verified_archives(tmp_path, pins)["package"]["files"] == 1
    (tmp_path / "dependencies" / "fixture").write_bytes(b"changed")
    with pytest.raises(ValueError, match="Extracted product differs"):
        verified_archives(tmp_path, pins)


def test_host_tool_denominator_counts_recovered_failures():
    raw = {"host_calls": [{"status": "failed"}, {"status": "completed"}]}
    assert host_tool_calls(raw) == {"failed": 1, "completed": 1}


def test_recovered_rejection_fails_perfection_gate():
    scores = dict.fromkeys(STRICT_ZERO, 0) | {"completed": 6, "scheduled": 6}
    totals = {
        panel: {profile: scores.copy() for profile in ("core", "full")} for panel in ("old", "new")
    }
    assert perfect(totals)
    totals["new"]["core"]["unsuccessful_public_replies"] = 1
    assert not perfect(totals)


@pytest.mark.parametrize("omitted_stream", ["events", "stderr"])
def test_host_error_verifier_rejects_omissions_even_when_error_totals_agree(
    tmp_path, omitted_stream
):
    attempt = tmp_path / "attempt-1"
    attempt.mkdir()
    (attempt / "host.events.jsonl").write_bytes(
        b'{"type":"item.completed","item":{"type":"mcp_tool_call","status":"failed"}}\n'
    )
    (attempt / "host.stderr").write_bytes(
        b"INFO starting\nERROR failed to parse function arguments\n"
    )
    evidence = host_error_records(attempt)
    assert [(item["stream"], item["class"], item["index"]) for item in evidence] == [
        ("events", "item.completed", 0),
        ("stderr", "host_router_without_public_request", 1),
    ]
    report = (
        Path(__file__).resolve().parents[1] / "experiments/compact_edit_confirmation_results.json"
    )
    panel = read(report)["runs"]["followup"]["panels"]["old"]
    row = next(
        item for item in panel["cases"] if item["task"] == "r03" and item["profile"] == "full"
    )
    row["host_errors"] = [item for item in evidence if item["stream"] != omitted_stream]
    panel["summary"] = summary(panel["cases"])
    with pytest.raises(ValueError, match="Host errors differ"):
        verify_host_receipts(row, tmp_path, tmp_path)


@pytest.mark.parametrize("present", ["host.events.jsonl", "host.stderr"])
def test_delivered_actor_requires_both_host_streams_even_without_errors(tmp_path, present):
    attempt = tmp_path / "attempt-1"
    attempt.mkdir()
    (attempt / present).write_bytes(b"")
    row = {"prompt_reached_model": True, "model_turns_started": 1, "host_errors": []}
    with pytest.raises(ValueError, match="missing its host event or stderr stream"):
        verify_host_receipts(row, tmp_path, tmp_path)
