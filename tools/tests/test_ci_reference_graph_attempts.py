"""Actions receipts preserve a lost runner's last confirmation and the actual retry."""

import json
from dataclasses import replace
from io import BytesIO
from zipfile import ZipFile

from tools import ci_archive_evidence
from tools import ci_reference_graph_attempts as graph

RUN = ci_archive_evidence.Run("owner/project", 42, 2, "c" * 40, "", "d" * 40, 29)
MAC = "Reference graph (macos-26-intel, Python 3.15)"
LINUX = "Reference graph (ubuntu-26.04, Python 3.15)"


def job(name, attempt, states, conclusion="success"):
    steps = []
    for label, phase in graph.PHASES.items():
        if phase not in states:
            continue
        outcome = states[phase]
        steps.append(
            {
                "number": len(steps) + 1,
                "name": label,
                "status": "in_progress" if outcome is None else "completed",
                "conclusion": outcome,
                "started_at": None if outcome == "skipped" else "2026-11-20T00:01:00Z",
                "completed_at": "2026-11-20T00:02:00Z" if outcome is not None else None,
            }
        )
    return {
        "id": 100 * attempt + (2 if name == MAC else 1),
        "run_id": RUN.run_id,
        "run_attempt": attempt,
        "head_sha": RUN.head_sha,
        "name": f"Python validation / {name}",
        "status": "completed",
        "conclusion": conclusion,
        "steps": steps,
    }


def test_attempts_preserve_before_and_after_without_accessing_logs():
    success = dict.fromkeys(
        (
            "checkout_receipt",
            "install",
            "collection",
            "execution_core",
            "execution_service",
            "initialization",
        ),
        "success",
    )
    first = job(
        MAC,
        1,
        {
            "checkout_receipt": "success",
            "install": "success",
            "collection": "success",
            "execution_core": None,
            "execution_service": "skipped",
        },
        "timed_out",
    )
    carried = job(LINUX, 1, success)
    carried.update(
        runner_id=1000036296,
        started_at="2026-11-20T00:00:59Z",
        completed_at="2026-11-20T00:02:01Z",
    )
    retry = job(MAC, 2, success)
    copied = {**carried, "id": 201, "run_attempt": 2}
    jobs = {1: [first, carried], 2: [carried, copied, retry]}

    entry = {
        "name": "reference-graph-checkout-macos-26-intel-python-3.15-42-attempt-1",
        "id": 500,
        "workflow_run": {
            "id": RUN.run_id,
            "head_sha": RUN.head_sha,
            "repository_id": RUN.repository_id,
        },
        "expired": False,
        "size_in_bytes": 500,
    }
    receipt = {
        "schema_version": 1,
        "repository": RUN.repository,
        "run_id": RUN.run_id,
        "run_attempt": 1,
        "job_key": "reference_graph",
        "checkout_sha": RUN.checked_sha,
        "matrix_os": "macos-26-intel",
        "matrix_python": "3.15",
    }
    retry_entry = {**entry, "name": entry["name"].removesuffix("1") + "2", "id": 502}
    linux_entry = {
        **entry,
        "name": entry["name"].replace("macos-26-intel", "ubuntu-26.04"),
        "id": 503,
    }
    again = {**receipt, "run_attempt": 2}
    repo = f"repos/{RUN.repository}/actions"
    api = {
        f"{repo}/runs/42/artifacts?per_page=100&page=1": {
            "total_count": 3,
            "artifacts": [entry, retry_entry, linux_entry],
        },
        **{
            f"{repo}/runs/42/attempts/{number}/jobs?per_page=10&page=1": {
                "total_count": len(rows),
                "jobs": rows,
            }
            for number, rows in jobs.items()
        },
    }
    artifacts = {
        f"{repo}/artifacts/500/zip": receipt,
        f"{repo}/artifacts/502/zip": again,
        f"{repo}/artifacts/503/zip": {**receipt, "matrix_os": "ubuntu-26.04"},
    }

    def fetch(endpoint):
        if endpoint in artifacts:
            data = BytesIO()
            with ZipFile(data, "w") as archive:
                archive.writestr("reference-graph-checkout.json", json.dumps(artifacts[endpoint]))
            return data.getvalue()
        assert endpoint in api, endpoint
        return json.dumps(api[endpoint]).encode()

    result = graph.inspect(RUN, fetch)
    failed, passed = [row for row in result["jobs"] if row["job"] == MAC]
    assert failed["last_confirmed_phase"] == "collection"
    assert failed["phases"][-2]["conclusion"] is None
    assert failed["phases"][-1]["conclusion"] == "skipped"
    assert failed["conclusion"] == "timed_out"
    assert failed["checkout_receipt"]["status"] == "verified"
    assert passed["last_confirmed_phase"] == "initialization"
    assert passed["checkout_receipt"]["status"] == "verified"
    assert result["effective"][MAC]["attempt"] == 2
    assert result["effective"][MAC]["conclusion"] == "success"
    assert result["effective"][LINUX]["attempt"] == 1
    assert result["carried_forward"] == [
        {
            "job": LINUX,
            "attempt": 2,
            "job_id": copied["id"],
            "execution_attempt": 1,
            "execution_job_id": carried["id"],
        }
    ]
    copied["runner_id"] += 1
    result = graph.inspect(RUN, fetch)
    assert result["effective"][LINUX]["attempt"] == 2
    assert result["effective"][LINUX]["checkout_receipt_status"] == "missing"

    receipt["run_attempt"] = 2
    result = graph.inspect(replace(RUN, attempt=1), fetch)
    row = next(row for row in result["jobs"] if row["job"] == MAC)
    assert row["checkout_receipt"]["status"] == "invalid"


def test_unavailable_retry_does_not_make_the_earlier_attempt_the_effective_result(monkeypatch):
    first = job(MAC, 1, {"legacy_combined": "success"})

    def inventory(_fetch, prefix):
        if "/attempts/1/" in prefix:
            return [first]
        raise ValueError("Unavailable attempt")

    monkeypatch.setattr(graph.ci_archive_attempts, "attempt_job_inventory", inventory)
    report = graph.inspect(RUN, lambda _: b'{"total_count": 0, "artifacts": []}')
    assert report["unavailable_attempts"] == [2]
    assert report["jobs"][0]["last_confirmed_phase"] == "legacy_combined"
    assert report["effective"][MAC]["status"] == "inventory_unknown"
