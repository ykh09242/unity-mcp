"""Exercise archive install reporting across evidence and GitHub history boundaries."""

import io
import json
import os
from pathlib import Path
import subprocess
import sys
from urllib.parse import parse_qs, urlsplit
import zipfile

import pytest

from ci_archive_install_history import ActionsHistory, HistoryUnavailable, InstallRow, read_archive
import ci_archive_install_report as report


ROOT = Path(__file__).resolve().parents[2]


def test_reports_current_install_without_history_or_direct_reference_credentials(tmp_path):
    evidence = tmp_path / "candidate-archive"
    evidence.mkdir()
    secret = "https://secret-user:secret-password@example.test/package.zip"
    (evidence / "resolved.txt").write_text(
        f"Fast_MCP==4.2.1\nykh09242-unity-mcp-server @ {secret}\n", encoding="utf-8"
    )
    (evidence / "install.log").write_text(
        f"Resolved 2 packages in 39ms\nBuilding cryptography==48.0.0\n"
        f"\x1b[32mBuilt ykh09242-unity-mcp-server @ {secret}\x1b[0m\n"
        f"Prepared 2 packages in 1m 04s\nInstalled 2 packages in 10ms\n"
        f" + ykh09242-unity-mcp-server==9.6.0 (from {secret})\n",
        encoding="utf-8",
    )
    summary = tmp_path / "summary.md"
    env = {
        **os.environ,
        "GH_TOKEN": "",
        "GITHUB_REPOSITORY": "example/repo",
        "GITHUB_RUN_ID": "20",
        "GITHUB_RUN_ATTEMPT": "1",
        "GITHUB_STEP_SUMMARY": str(summary),
    }

    process = subprocess.run(
        [
            sys.executable,
            str(ROOT / "tools" / "ci_archive_install_report.py"),
            "--kind",
            "candidate",
            "--os",
            "ubuntu-26.04",
            "--python",
            "3.11",
            "--evidence",
            str(evidence),
            "--install-outcome",
            "success",
        ],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert process.returncode == 0, process.stderr
    serialized = (evidence / "install-report.json").read_text(encoding="utf-8")
    result = json.loads(serialized)
    assert result["baseline"] is None and result["changes"] is None
    assert result["current"]["versions"] == {
        "fast-mcp": "4.2.1",
        "ykh09242-unity-mcp-server": "9.6.0",
    }
    assert result["current"]["confirmed_builds"] == {"ykh09242-unity-mcp-server": "9.6.0"}
    assert result["current"]["phase_seconds"] == {
        "resolved": 0.039,
        "prepared": 64.0,
        "installed": 0.01,
    }
    markdown = summary.read_text(encoding="utf-8")
    assert "64.000 s" in markdown and "ykh09242-unity-mcp-server" in markdown
    assert "secret-password" not in serialized + markdown + process.stderr


def test_complete_inventory_diff_preserves_unknown_direct_versions():
    before = report.observe(
        {"resolved.txt": b"change==1.0\nremove==1\nurl @ https://untrusted.test\n"}, "success"
    )
    after = report.observe({"resolved.txt": b"change==2.0\nadd==1\nurl==2\n"}, "success")

    changes = report.compare(after, before)

    assert changes == {
        "added": {"add": "1"},
        "removed": {"remove": "1"},
        "changed": {"change": ["1.0", "2.0"]},
        "unknown": ["url"],
    }


@pytest.mark.parametrize("outcome,line", [("failure", ""), ("success", "invalid evidence\n")])
def test_partial_inventory_cannot_report_removals(outcome, line):
    before = report.observe({"resolved.txt": b"retained==1\nmissing==1\n"}, "success")
    after = report.observe({"resolved.txt": f"retained==1\n{line}".encode()}, outcome)

    changes = report.compare(after, before)

    assert changes is None


@pytest.mark.parametrize("outcome", ["failure", "success"])
def test_missing_freeze_retains_explicitly_logged_installed_versions_as_partial(outcome):
    files = {"install.log": b"Installed 2 packages in 8ms\n + alpha==1.2.3\n + beta==4.0.0\n"}
    before = report.observe({"resolved.txt": b"alpha==1.2.2\nold==1\n"}, "success")

    current = report.observe(files, outcome)

    assert current.inventory_status == "partial"
    assert current.versions == {"alpha": "1.2.3", "beta": "4.0.0"}
    assert report.compare(current, before) is None


def test_conflicting_install_log_preserves_freeze_versions():
    files = {
        "resolved.txt": b"alpha==1.2.3\nbeta @ https://user:secret@example.test/archive.zip\n",
        "install.log": b" + alpha==1.2.2\n + beta==4.0.0 (from https://user:secret@example.test/archive.zip)\n",
    }

    current = report.observe(files, "success")

    assert current.inventory_status == "partial"
    assert current.versions == {"alpha": "1.2.3", "beta": "4.0.0"}


def test_rejects_duplicate_archive_evidence_and_never_extracts_paths():
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        archive.writestr("../escape", "ignored")
        archive.writestr("resolved.txt", "dep==1")
        with pytest.warns(UserWarning, match="Duplicate name"):
            archive.writestr("resolved.txt", "dep==2")

    with pytest.raises(HistoryUnavailable):
        read_archive(stream.getvalue())


@pytest.mark.parametrize(
    "latest,first_artifact_suffix,expected_attempt,missing_newer_history",
    [(2, "-attempt-1", 1, False), (1, "", 1, False), (2, "", 2, False), (2, "", 1, True)],
)
def test_baseline_uses_latest_comparable_success_and_labels_previous_attempt(
    monkeypatch, latest, first_artifact_suffix, expected_attempt, missing_newer_history
):
    row = InstallRow("candidate", "ubuntu-26.04", "3.11")
    running_id, current_attempt, workflow = 108, 3, 998
    legacy_retry = latest == 2 and not first_artifact_suffix
    for key, value in {
        "GH_TOKEN": "test-token",
        "GITHUB_REPOSITORY": "example/repo",
        "GITHUB_RUN_ID": str(running_id),
        "GITHUB_RUN_ATTEMPT": str(current_attempt),
    }.items():
        monkeypatch.setenv(key, value)

    def run(id_, attempt, repo="example/repo", *, event="push", workflow_id=workflow):
        return {
            "id": id_,
            "run_attempt": attempt,
            "workflow_id": workflow_id,
            "status": "completed",
            "conclusion": "failure",
            "event": event,
            "head_repository": {"full_name": repo},
        }

    def artifact(id_, run_id, suffix, *, prefix=row.artifact_prefix):
        created = "2026-11-18T14:07:50Z" if expected_attempt == 2 else "2026-11-18T13:51:06Z"
        return {
            "id": id_,
            "name": f"{prefix}-{run_id}{suffix}",
            "expired": False,
            "size_in_bytes": 512,
            "created_at": created,
        }

    def jobs(run_id, attempt, conclusion):
        upload_started = "2026-11-18T14:07:47Z" if attempt == 2 else "2026-11-18T13:51:03Z"
        upload_completed = "2026-11-18T14:07:50Z" if attempt == 2 else "2026-11-18T13:51:06Z"
        return {
            "total_count": 1,
            "jobs": [
                {
                    "id": 1000 + run_id,
                    "run_id": run_id,
                    "run_attempt": attempt,
                    "name": f"python / {row.job_name}",
                    "steps": [
                        {"name": row.step_name, "status": "completed", "conclusion": conclusion},
                        {
                            "name": row.upload_step_name,
                            "status": "completed",
                            "conclusion": "success",
                            "started_at": upload_started,
                            "completed_at": upload_completed,
                        },
                    ],
                }
            ],
        }

    records = {
        f"runs/{running_id}": run(running_id, current_attempt),
        f"workflows/{workflow}/runs": {
            "workflow_runs": [
                run(107, 1, "contributor/repo", event="pull_request"),
                run(106, 1, workflow_id=workflow + 1),
                run(105, 1),
                run(104, latest),
                run(103, 1),
                run(102, 1),
            ],
        },
        # An artifact from the other source kind would sort newer if it were eligible.
        "runs/105/artifacts": {
            "artifacts": [
                artifact(51, 105, "", prefix="distribution-canary-ubuntu-26.04-python-3.11")
            ]
        },
        # The linked successful upload can be a modern attempt or a timestamped legacy rerun.
        "runs/104/artifacts": {
            "artifacts": [artifact(41, 104, first_artifact_suffix)]
            + ([] if legacy_retry else [artifact(42, 104, "-attempt-2")])
        },
        "runs/104/attempts/2/jobs": jobs(104, 2, "success" if legacy_retry else "failure"),
        "runs/104/attempts/1/jobs": jobs(104, 1, "success"),
        "runs/108/artifacts": {"artifacts": [artifact(82, 108, "-attempt-2")]},
        "runs/108/attempts/2/jobs": jobs(108, 2, "failure"),
    }

    def fake_api(_self, endpoint):
        url = urlsplit(endpoint)
        query = parse_qs(url.query)
        if missing_newer_history and url.path == "runs/105/artifacts":
            raise HistoryUnavailable("Newer history request unavailable")
        records_by_id = {41: b"dep==1\n", 82: b"dep==6\n"}
        if url.path.startswith("artifacts/"):
            artifact_id = int(url.path.split("/")[1])
            stream = io.BytesIO()
            with zipfile.ZipFile(stream, "w") as archive:
                archive.writestr("resolved.txt", records_by_id[artifact_id])
            return stream.getvalue()
        payload = records[url.path]
        if url.path.endswith("/artifacts"):
            name = query["name"][0]
            payload = {"artifacts": [item for item in payload["artifacts"] if item["name"] == name]}
        if url.path == "runs/104/attempts/2/jobs":
            size, page = int(query["per_page"][0]), int(query.get("page", ["1"])[0])
            if size == 10 and page == 2:
                raise HistoryUnavailable("Synthetic timeout on the second large job page")
            inventory = [{"name": f"Other job {number}"} for number in range(10)] + payload["jobs"]
            payload = {
                "total_count": len(inventory),
                "jobs": inventory[(page - 1) * size : page * size],
            }
        return json.dumps(payload).encode()

    monkeypatch.setattr(ActionsHistory, "api", fake_api)
    current = report.observe({"resolved.txt": b"dep==5\n"}, "success")

    result = report.build_report(row, current)

    assert result["history_status"] == ("incomplete" if missing_newer_history else "available")
    assert result["changes"] == {
        "added": {},
        "changed": {"dep": ["1", "5"]},
        "removed": {},
        "unknown": [],
    }
    baseline, previous = result["baseline"], result["previous_attempt"]
    assert (baseline["run_id"], baseline["attempt"], baseline["artifact_id"]) == (
        104,
        expected_attempt,
        41,
    )
    assert (previous["run_id"], previous["attempt"], previous["artifact_id"]) == (108, 2, 82)
    assert previous["outcome"] == "failure" and previous["inventory_status"] == "partial"


@pytest.mark.parametrize(
    "unavailable_runs,expected_status",
    [((), "no_comparable_artifact"), ((19, 18), "unavailable"), ((19,), "incomplete")],
)
def test_missing_baseline_distinguishes_absent_evidence_from_api_errors(
    monkeypatch, unavailable_runs, expected_status
):
    for key, value in {
        "GH_TOKEN": "test-token",
        "GITHUB_REPOSITORY": "example/repo",
        "GITHUB_RUN_ID": "20",
        "GITHUB_RUN_ATTEMPT": "1",
    }.items():
        monkeypatch.setenv(key, value)

    def fake_api(_self, endpoint):
        path = urlsplit(endpoint).path
        run = {"run_attempt": 1, "workflow_id": 998}
        if path == "runs/20":
            return json.dumps({**run, "id": 20}).encode()
        if path == "workflows/998/runs":
            return json.dumps(
                {
                    "workflow_runs": [
                        {
                            **run,
                            "id": number,
                            "status": "completed",
                            "event": "push",
                            "head_repository": {"full_name": "example/repo"},
                        }
                        for number in (19, 18)
                    ]
                }
            ).encode()
        if path in {f"runs/{number}/artifacts" for number in unavailable_runs}:
            raise HistoryUnavailable("Artifact request unavailable")
        assert path in {"runs/19/artifacts", "runs/18/artifacts"}
        return b'{"artifacts": []}'

    monkeypatch.setattr(ActionsHistory, "api", fake_api)
    row = InstallRow("candidate", "ubuntu-26.04", "3.11")
    current = report.observe({"resolved.txt": b"dep==5\n"}, "success")

    result = report.build_report(row, current)

    assert result["history_status"] == expected_status
    assert result["baseline"] is None and result["changes"] is None
