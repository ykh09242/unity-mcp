"""Bounded coherent saved evidence fixtures for offline comparison tests."""

from hashlib import sha256
import json
from pathlib import Path
from uuid import uuid4

from models.play_scenarios import PlayScenario


def _evidence(
    directory: Path,
    values: list[int | None],
    *,
    revision: str = "baseline-build",
    warmup: int = 1,
    environment: dict[str, str] | None = None,
) -> Path:
    """Create a truthful saved contract fixture with one boundary sample per actual row."""
    directory.mkdir()
    scenario = PlayScenario.model_validate(
        {
            "name": "metric-comparison",
            "steps": [{"name": "Reset", "action": "load_scene", "scene": "Assets/Menu.unity"}],
            "metrics": {"enabled": True, "warmup_iterations": warmup},
        }
    ).model_dump(mode="json", exclude_none=True)
    canonical = json.dumps(scenario, sort_keys=True, separators=(",", ":"))
    job_id, build_id = uuid4().hex, uuid4().hex
    identity = {
        "job_id": job_id,
        "definition_hash": sha256(canonical.encode()).hexdigest(),
        "build_id": build_id,
        "build_source_revision": revision,
        "payload_hash": sha256(revision.encode()).hexdigest(),
        "process_id": 1234,
    }
    request = {
        "schema_version": 2,
        **{key: value for key, value in identity.items() if key != "process_id"},
        "scenario_name": scenario["name"],
        "repeat_count": 1,
        "timeout_seconds": 300,
        "soak": {
            "iterations": len(values),
            "max_runtime_seconds": 3600,
            "failure_policy": "stop",
            "retain_reports": 1,
        },
    }
    encoded_request = (json.dumps(request) + "\n").encode()
    (directory / "request.json").write_bytes(encoded_request)
    (directory / "definition.json").write_bytes(canonical.encode() + b"\n")
    rows, samples, reports = [], [], []
    reproduction = {
        **{key: value for key, value in identity.items() if key not in {"job_id", "process_id"}},
        "unity_version": "6000.0.69f1",
        "package_version": "fixture",
        "source_revision": "comparison",
        "payload_verification": "launcher_admission",
        **(environment or {}),
    }
    request["source_revision"] = "comparison"
    encoded_request = (json.dumps(request) + "\n").encode()
    (directory / "request.json").write_bytes(encoded_request)
    for iteration, value in enumerate(values, 1):
        start, finish = 1000 + iteration * 100, 1050 + iteration * 100
        sample = {
            "iteration": iteration,
            "timestamp_unix_ms": finish,
            "managed_bytes": value,
            "allocated_bytes": value,
            "object_count": value,
            "runner_subscription_count": None,
            "runner_handle_count": None,
            "ignored_for_trend": iteration <= warmup,
            "error": None,
        }
        samples.append(sample)
        row = {
            **identity,
            "schema_version": 1,
            "iteration": iteration,
            "status": "succeeded",
            "started_unix_ms": start,
            "finished_unix_ms": finish,
            "runner_resources_released": True,
            "failure_code": None,
            "error": None,
            "cleanup_error": None,
            "unexpected_log_count": 0,
            "query_counts": None,
            "metrics": [sample],
            "report_file": f"iteration-{iteration:04d}.json",
        }
        rows.append(row)
        reports.append(
            {
                "job_id": job_id,
                "scenario": scenario,
                "status": "succeeded",
                "execution_environment": "player",
                "finalization_state": "completed",
                "runner_resources_released": True,
                "exit_code": 0,
                "repeat_count": 1,
                "timeout_seconds": 300,
                "player_schema_version": 2,
                "reproduction": reproduction,
                "started_unix_ms": start,
                "finished_unix_ms": finish,
                "iteration_results_version": 1,
                "iteration_results": [
                    {
                        "iteration": 1,
                        "status": "passed",
                        "started_unix_ms": start,
                        "finished_unix_ms": finish,
                    }
                ],
                "metrics": [dict(sample, iteration=1)],
            }
        )
    encoded_journal = b"".join(
        (json.dumps(row, separators=(",", ":")) + "\n").encode() for row in rows
    )
    (directory / "iterations.jsonl").write_bytes(encoded_journal)
    summary = {
        **identity,
        "schema_version": 1,
        "process_scope": "same-process",
        "requested_iterations": len(values),
        "recorded_iterations": len(values),
        "attempted_iterations": len(values),
        "passed_iterations": len(values),
        "failed_iterations": 0,
        "skipped_iterations": 0,
        "first_failure_iteration": None,
        "started_unix_ms": 1000,
        "deadline_unix_ms": 3601000,
        "finished_unix_ms": rows[-1]["finished_unix_ms"],
        "status": "succeeded",
        "finalization_state": "completed",
        "failure_policy": "stop",
        "retain_reports": 1,
        "journal_file": "iterations.jsonl",
        "journal_rows": len(values),
        "journal_bytes": len(encoded_journal),
        "journal_sha256": sha256(encoded_journal).hexdigest(),
        "journal_complete": True,
        "runner_resources_released": True,
        "selected_iteration": len(values),
        "retained_report_files": [rows[-1]["report_file"]],
        "error": None,
        "metrics_summary": {
            "sample_count": len(values),
            "usable_samples": len(values) - warmup,
            "warmup_iterations": warmup,
            "bounded_samples": samples[-10:],
            "warnings": [],
            "summary": "Fixture measurements",
        },
    }
    (directory / "soak.json").write_text(json.dumps(summary))
    (directory / rows[-1]["report_file"]).write_text(json.dumps(reports[-1]))
    (directory / "run.json").write_text(json.dumps(dict(reports[-1], soak=summary)))
    (directory / "process.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "job_id": job_id,
                "process_id": identity["process_id"],
                "request_sha256": sha256(encoded_request).hexdigest(),
                "process_ended": True,
                "forced_termination": False,
                "actual_exit_code": 0,
            }
        )
    )
    client = {
        "schema_version": 1,
        "execution_environment": "player",
        "process_scope": "same-process",
        "evidence_state": "complete",
        "status": "succeeded",
        "job_id": job_id,
        "process_id": identity["process_id"],
        "requested_iterations": len(values),
        "recorded_iterations": len(values),
        "attempted_iterations": len(values),
        "passed_iterations": len(values),
        "failed_iterations": 0,
        "skipped_iterations": 0,
        "unknown_iterations": 0,
        "journal_bytes": len(encoded_journal),
        "journal_sha256": summary["journal_sha256"],
        "client_error": None,
        "native_summary_file": "soak.json",
        "journal_file": "iterations.jsonl",
        "native_report_file": "run.json",
    }
    (directory / "client-soak.json").write_text(json.dumps(client))
    return directory


def _change(directory, filename, **changes):
    path = directory / filename
    value = json.loads(path.read_text())
    value.update(changes)
    path.write_text(json.dumps(value))


def _change_reproduction(directory, **changes):
    for filename in (
        "run.json",
        json.loads((directory / "soak.json").read_text())["retained_report_files"][0],
    ):
        raw = json.loads((directory / filename).read_text())
        raw["reproduction"].update(changes)
        (directory / filename).write_text(json.dumps(raw))


def _change_samples(directory, transform):
    """Keep hashes/checkpoints truthful while testing missing/error sample semantics."""
    rows = [json.loads(line) for line in (directory / "iterations.jsonl").read_text().splitlines()]
    for row in rows:
        transform(row)
    encoded = b"".join((json.dumps(row, separators=(",", ":")) + "\n").encode() for row in rows)
    (directory / "iterations.jsonl").write_bytes(encoded)
    summary = json.loads((directory / "soak.json").read_text())
    samples = [sample for row in rows for sample in row["metrics"] or []]
    summary.update(journal_bytes=len(encoded), journal_sha256=sha256(encoded).hexdigest())
    summary["metrics_summary"].update(
        sample_count=len(samples),
        usable_samples=sum(
            not sample["ignored_for_trend"] and sample["error"] is None for sample in samples
        ),
        bounded_samples=samples[-10:],
    )
    (directory / "soak.json").write_text(json.dumps(summary))
    _change(
        directory,
        "client-soak.json",
        journal_bytes=len(encoded),
        journal_sha256=summary["journal_sha256"],
    )
    _change(directory, "run.json", soak=summary)
    for filename in ("run.json", summary["retained_report_files"][0]):
        metrics = [dict(sample, iteration=1) for sample in rows[-1]["metrics"] or []]
        _change(directory, filename, metrics=metrics)
