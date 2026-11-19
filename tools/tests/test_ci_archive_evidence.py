"""Exercise retained proof against exact-attempt API responses and real artifact ZIPs."""

from dataclasses import replace
from io import BytesIO
import json
from pathlib import Path
import re
from urllib.parse import parse_qs
from zipfile import ZipFile

import pytest
import yaml

from tools import ci_archive_attempts as audit
from tools import ci_archive_evidence as ev


RUN = ev.Run("owner/project", 42, 1, "c" * 40, "b" * 40, "d" * 40, 29)
ROWS = [
    ev.InstallRow("published", os, version)
    for os, version in [
        ("ubuntu-26.04", "3.11"),
        ("windows-latest", "3.11"),
        ("ubuntu-26.04", "3.15"),
    ]
]


class Actions:
    def __init__(self):
        self.jobs = {}
        self.entries = {}
        self.files = {}

    def add_artifact(self, name, files):
        i = max(self.files, default=0) + 1
        self.files[i] = files
        self.entries[name] = {
            "id": i,
            "name": name,
            "expired": False,
            "size_in_bytes": 1024,
            "workflow_run": {
                "id": RUN.run_id,
                "head_sha": RUN.head_sha,
                "repository_id": RUN.repository_id,
            },
        }

    def add_job(self, row, number, conclusion="success", steps=None):
        phases = (
            steps
            if steps is not None
            else {
                ev.CHECKPOINT: "success",
                row.step_name: "success",
                ev.COMPARE: "success",
                row.upload_step_name: "success",
            }
        )
        job = {
            "name": row.job_name,
            "id": len(self.jobs.get(number, [])) + 100 * number,
            "head_sha": RUN.head_sha,
            "run_attempt": number,
            "run_id": RUN.run_id,
            "conclusion": conclusion,
            "steps": [
                {
                    "name": name,
                    "conclusion": outcome,
                    "status": "completed",
                    "started_at": "2026-01-01T12:01:00Z",
                }
                for name, outcome in phases.items()
            ],
        }
        self.jobs.setdefault(number, []).append(job)
        for role, name, expected in ev.expected_artifacts(row, job, RUN):
            if not expected:
                continue
            identity = {
                "kind": row.kind,
                "repository": RUN.repository,
                "run_id": RUN.run_id,
                "attempt": number,
            }
            if role == "checkpoint":
                files = {
                    "archive-checkpoint.json": {
                        **identity,
                        "platform": row.runner,
                        "python_requested": row.python,
                        "source_commit": RUN.source_pin,
                        "checked_commit": RUN.checked_sha,
                    }
                }
            elif role == "report":
                files = {
                    "install-report.json": {
                        **identity,
                        "runner": row.runner,
                        "python": row.python,
                        "current": {
                            "outcome": phases[row.step_name],
                            "source_commit": RUN.source_pin,
                        },
                    }
                }
            else:
                files = dict.fromkeys(ev.RAW_FILES, "data")
                files["source.json"] = {
                    "checked_commit": RUN.checked_sha,
                    "server_commit": RUN.source_pin,
                    "server_source": f"https://github.com/ykh09242/unity-mcp/archive/{RUN.source_pin}.zip#subdirectory=Server",
                }
                files["behavior.json"] = {
                    "candidate_sha": RUN.source_pin,
                    "python": row.python + ".2",
                }
            self.add_artifact(name, files)
        return job

    def fetch(self, endpoint):
        if match := re.search(r"/artifacts/(\d+)/zip$", endpoint):
            output = BytesIO()
            with ZipFile(output, "w") as archive:
                for name, value in self.files[int(match[1])].items():
                    archive.writestr(name, json.dumps(value) if isinstance(value, dict) else value)
            return output.getvalue()
        path, query = endpoint.split("?")
        if match := re.search(r"/attempts/(\d+)/jobs$", path):
            field, items = "jobs", self.jobs[int(match[1])]
        else:
            assert path == f"repos/{RUN.repository}/actions/runs/{RUN.run_id}/artifacts"
            field, items = "artifacts", list(self.entries.values())
        params = parse_qs(query)
        size, page = int(params["per_page"][0]), int(params["page"][0])
        return json.dumps(
            {"total_count": len(items), field: items[(page - 1) * size : page * size]}
        ).encode()

    def reconcile(self, number=1, history=False):
        return audit.reconcile(
            replace(RUN, attempt=number),
            "published",
            {r.job_name: r for r in ROWS},
            self.fetch,
            history,
        )


@pytest.fixture
def actions():
    api = Actions()
    for row in ROWS:
        api.add_job(row, 1)
    return api


def test_complete_rows_and_missing_ubuntu_row_with_other_rows_intact(actions):
    assert not actions.reconcile()["issues"]
    for name in list(actions.entries):
        if "ubuntu-26.04-python-3.11" in name:
            del actions.entries[name]
    report = actions.reconcile()
    assert len(report["issues"]) == 3
    for row in report["evidence"]:
        status = "missing" if row["job"] == ROWS[0].job_name else "verified"
        assert {p["status"] for p in row["artifacts"].values()} == {status}
    actions.jobs[1].pop(0)
    assert actions.reconcile()["effective"][ROWS[0].job_name]["status"] == "missing_job"


def test_partial_rerun_uses_the_latest_actual_attempt_for_each_row():
    actions = Actions()
    actions.add_job(
        ROWS[0], 1, "cancelled", {ev.CHECKPOINT: "success", ROWS[0].step_name: "cancelled"}
    )
    for row in ROWS[1:]:
        actions.add_job(row, 1)
    actions.add_job(ROWS[0], 2)
    report = actions.reconcile(2)
    assert not report["issues"]
    assert [report["effective"][r.job_name]["attempt"] for r in ROWS] == [2, 1, 1]
    interrupted = report["evidence"][0]["artifacts"]
    assert {k: v["status"] for k, v in interrupted.items()} == {
        "checkpoint": "verified",
        "report": "not_expected",
        "raw": "not_expected",
    }


@pytest.mark.parametrize("conclusion", ["skipped", "cancelled", "failure"])
def test_interrupted_before_checkout_preserves_job_conclusion(conclusion):
    actions = Actions()
    actions.add_job(ROWS[0], 1, conclusion, {})
    report = actions.reconcile()
    assert report["issues"] and report["effective"][ROWS[0].job_name]["status"] == conclusion
    assert {p["status"] for p in report["evidence"][0]["artifacts"].values()} == {"not_expected"}


def test_failed_install_accepts_partial_raw_but_keeps_install_failure():
    actions = Actions()
    row = ROWS[0]
    actions.add_job(
        row,
        1,
        "failure",
        {
            ev.CHECKPOINT: "success",
            row.step_name: "failure",
            row.upload_step_name: "success",
            ev.COMPARE: "success",
        },
    )
    raw = next(v for k, v in actions.entries.items() if k.startswith("distribution-canary-"))
    actions.files[raw["id"]] = {"install.log": "partial"}
    report = actions.reconcile()
    assert report["effective"][row.job_name]["status"] == "failure"
    assert {p["status"] for p in report["evidence"][0]["artifacts"].values()} == {"verified"}


@pytest.mark.parametrize(
    "field,value",
    [
        ("attempt", 2),
        ("repository", "other/project"),
        ("kind", "candidate"),
        ("platform", "windows-latest"),
        ("python_requested", "3.15"),
        ("checked_commit", "0" * 40),
        ("source_commit", "0" * 40),
        ("run_id", 1),
    ],
)
def test_internal_checkpoint_identity_cannot_substitute_for_selected_row(actions, field, value):
    first = next(iter(actions.files.values()))["archive-checkpoint.json"]
    first[field] = value
    assert actions.reconcile()["evidence"][0]["artifacts"]["checkpoint"]["status"] == "invalid"


def test_artifact_expiry_unknown_inventory_and_ownership_have_distinct_results(
    actions, monkeypatch
):
    name = next(iter(actions.entries))
    actions.entries[name]["expired"] = True
    assert actions.reconcile()["evidence"][0]["artifacts"]["checkpoint"]["status"] == "expired"
    actions.entries[name]["workflow_run"]["id"] += 1
    assert actions.reconcile()["evidence"][0]["artifacts"]["checkpoint"]["status"] == "invalid"
    fetch = actions.fetch

    def truncated(endpoint):
        return b'{"total_count":3,"artifacts":[]}' if "/artifacts?" in endpoint else fetch(endpoint)

    monkeypatch.setattr(actions, "fetch", truncated)
    report = actions.reconcile()
    assert report["evidence"][0]["artifacts"]["checkpoint"]["status"] == "inventory_unknown"
    assert report["issues"]


def test_history_is_required_after_upload_and_checked_against_actual_jobs(actions):
    assert "missing" in actions.reconcile(history=True)["issues"][0]
    report = actions.reconcile()
    name = "archive-history-published-42-attempt-1"
    actions.add_artifact(name, {"archive-attempts.json": report})
    assert not actions.reconcile(history=True)["issues"]
    report["jobs"].pop()
    assert actions.reconcile(history=True)["history"][0]["status"] == "invalid"
    store = audit.artifacts(RUN, actions.fetch)
    assert audit.history_artifact(store, "published", 1, [], b"changed")["status"] == "invalid"
    assert (
        audit.history_artifact(store, "published", 1, [], json.dumps(report).encode())["status"]
        == "verified"
    )


def test_workflow_retains_failures_and_checks_the_actual_upload():
    workflow = Path(__file__).resolve().parents[2] / ".github/workflows/archive-attempts.yml"
    job = yaml.safe_load(workflow.read_text())["jobs"]["report"]
    assert job["permissions"] == {"actions": "read", "contents": "read"}
    assert not job.get("continue-on-error")
    assert all(not s.get("continue-on-error") for s in job["steps"])
    upload = next(s for s in job["steps"] if s.get("id") == "upload")
    assert "!cancelled()" in upload["if"] and upload["with"]["if-no-files-found"] == "error"
    assert "--verify-upload" in job["steps"][-1]["run"]
    assert (
        job["steps"][-1]["env"]["HISTORY_ARTIFACT_ID"] == "${{ steps.upload.outputs.artifact-id }}"
    )


def test_upload_verification_uses_the_returned_artifact_id(actions):
    name = "archive-history-published-42-attempt-1"
    actions.add_artifact(name, {"archive-attempts.json": "exact report bytes"})
    item = actions.entries[name]
    artifact_id = item["id"]

    def fetch(endpoint):
        if endpoint.endswith("/zip"):
            return actions.fetch(endpoint)
        assert endpoint.endswith(f"/actions/artifacts/{artifact_id}")
        return json.dumps(item)

    assert (
        audit.verify_upload(RUN, "published", artifact_id, b"exact report bytes", fetch)["status"]
        == "verified"
    )
    item["id"] += 1
    assert (
        audit.verify_upload(RUN, "published", artifact_id, b"exact report bytes", fetch)["status"]
        != "verified"
    )


def test_unavailable_rerun_job_inventory_cannot_certify_earlier_success(actions):
    report = actions.reconcile(2)
    assert report["issues"] and all(
        row["status"] == "inventory_unknown" for row in report["effective"].values()
    )


def test_success_job_without_install_step_cannot_certify_installation(actions):
    actions.jobs[1][0]["steps"] = []
    assert any("install step unverified" in issue for issue in actions.reconcile()["issues"])


@pytest.mark.parametrize(
    "role,member,field,value",
    [
        ("report", "install-report.json", "runner", "windows-latest"),
        ("report", "install-report.json", "current", {"outcome": "failure"}),
        ("raw", "source.json", "checked_commit", "0" * 40),
        ("raw", "behavior.json", "python", "3.15.0"),
        ("raw", "registration.jsonl", None, None),
    ],
)
def test_each_successful_row_requires_matching_report_and_raw_members(
    actions, role, member, field, value
):
    proof = actions.reconcile()["evidence"][0]["artifacts"][role]
    files = actions.files[proof["id"]]
    if field is None:
        del files[member]
    else:
        files[member][field] = value
    report = actions.reconcile()
    assert report["issues"] == [f"{proof['name']}: invalid."]
