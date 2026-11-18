"""First-attempt archive evidence survives a successful matrix rerun."""

import json
from pathlib import Path
import subprocess

import pytest
import yaml

from tools import ci_archive_attempts as attempts


ROOT = Path(__file__).resolve().parents[2]
JOB = "Python tests / Installed candidate archive (macos-26-intel, Python 3.11)"
INSTALL = "Install candidate archive without runtime lock"


@pytest.mark.parametrize("kind", ["candidate", "published", "bootstrap"])
def test_checkpoint_records_the_actual_toolchain_and_distinct_source_before_install(
    tmp_path, monkeypatch, kind
):
    action = yaml.safe_load((ROOT / ".github/actions/archive-checkpoint/action.yml").read_text())
    capture, upload = action["runs"]["steps"]
    assert "github.run_attempt" in upload["with"]["name"]
    manifest = tmp_path / "MCPForUnity/package.json"
    manifest.parent.mkdir()
    selected = "a" * 40
    checked = "b" * 40
    manifest.write_text(
        json.dumps(
            {
                "mcpServerSource": f"https://github.com/ykh09242/unity-mcp/archive/{selected}.zip#subdirectory=Server"
            }
        )
    )
    monkeypatch.chdir(tmp_path)
    for name, value in {
        "RUNNER_TEMP": str(tmp_path),
        "GITHUB_STEP_SUMMARY": str(tmp_path / "summary"),
        "GITHUB_REPOSITORY": "demo/repo",
        "GITHUB_RUN_ID": "99",
        "GITHUB_RUN_ATTEMPT": "2",
        "RUNNER_ARCH": "X64",
        "ARCHIVE_PLATFORM": "macos-26-intel",
        "ARCHIVE_PYTHON": "3.11",
        "ARCHIVE_KIND": kind,
    }.items():
        monkeypatch.setenv(name, value)
    commands = {
        ("git", "rev-parse", "HEAD"): checked,
        ("uv", "--version"): "uv 0.13.9",
        ("uv", "python", "find", "--offline", "--no-config", "3.11"): "/python3.11",
        ("/python3.11", "-I", "-c", "import platform; print(platform.python_version())"): "3.11.15",
    }
    monkeypatch.setattr(
        subprocess, "check_output", lambda command, **kwargs: commands[tuple(command)]
    )
    exec(compile(capture["run"], "archive-checkpoint", "exec"), {})
    checkpoint = json.loads((tmp_path / "archive-checkpoint.json").read_text())
    assert (checkpoint["run_id"], checkpoint["attempt"], checkpoint["checked_commit"]) == (
        99,
        2,
        checked,
    )
    assert checkpoint["source_commit"] == (checked if kind == "candidate" else selected)
    assert (checkpoint["python"], checkpoint["uv"], checkpoint["next_phase"]) == (
        "3.11.15",
        "uv 0.13.9",
        "installation",
    )


def example_job(attempt, conclusion, steps):
    return {
        "id": 10 + attempt,
        "name": JOB,
        "run_attempt": attempt,
        "conclusion": conclusion,
        "steps": [
            {"name": name, "status": state, "conclusion": outcome, "started_at": started}
            for name, state, outcome, started in steps
        ],
    }


def test_attempts_keep_interrupted_install_and_completed_retry():
    ready = ("Preserve installer checkpoint", "completed", "success", "2026-11-18T00:00:01Z")
    first = example_job(
        1, "cancelled", [ready, (INSTALL, "in_progress", None, "2026-11-18T00:00:02Z")]
    )
    second = example_job(
        2, "success", [ready, (INSTALL, "completed", "success", "2026-11-18T01:00:02Z")]
    )
    pages = {1: [first], 2: [second, first, {"name": "unrelated"}]}

    def fetch(endpoint):
        attempt = int(endpoint.split("/attempts/")[1].split("/")[0])
        return json.dumps({"total_count": len(pages[attempt]), "jobs": pages[attempt]}).encode()

    rows, warnings = attempts.collect("owner/project", 112, 2, "candidate", fetch)
    assert not warnings
    assert [(r["attempt"], r["job_id"], r["conclusion"]) for r in rows] == [
        (1, 11, "cancelled"),
        (2, 12, "success"),
    ]
    assert rows[0]["last_started"] == INSTALL
    assert rows[0]["last_succeeded"] == "Preserve installer checkpoint"
    assert rows[1]["last_succeeded"] == INSTALL


def test_missing_first_attempt_and_skipped_steps_remain_unknown():
    def fetch(endpoint):
        if "/attempts/1/" in endpoint:
            raise ValueError("unavailable")
        skipped = example_job(2, "skipped", [(INSTALL, "completed", "skipped", None)])
        return json.dumps({"total_count": 1, "jobs": [skipped]}).encode()

    rows, warnings = attempts.collect("owner/project", 112, 2, "candidate", fetch)
    assert warnings and "1" in warnings[0]
    assert rows[0]["last_started"] is None and rows[0]["last_succeeded"] is None


def test_gateway_failure_splits_same_page_without_skipping_later_jobs():
    original = [example_job(1, "success", []) for _ in range(17)]
    for index, job in enumerate(original):
        job["id"] = index + 1

    def fetch(endpoint):
        size, page = (int(part.split("=")[1]) for part in endpoint.split("?")[1].split("&"))
        if page == 2 and size == 10:
            raise ValueError("gateway timeout")
        return json.dumps(
            {"total_count": 17, "jobs": original[(page - 1) * size : page * size]}
        ).encode()

    rows, warnings = attempts.collect("owner/project", 112, 1, "candidate", fetch)
    assert not warnings
    assert [r["job_id"] for r in rows] == list(range(1, 18))


@pytest.mark.parametrize(
    "filename,job,kind,step",
    [
        ("python-tests.yml", "installed_archive", "candidate", INSTALL),
        (
            "python-tests.yml",
            "server_bootstrap",
            "bootstrap",
            "Verify cold and offline server bootstrap",
        ),
        (
            "distribution-canary.yml",
            "canary",
            "published",
            "Resolve and install the pinned archive without a lock or shared cache",
        ),
    ],
)
def test_checkpoint_is_stored_before_install_and_attempt_names_are_immutable(
    filename, job, kind, step
):
    jobs = yaml.safe_load((ROOT / ".github/workflows" / filename).read_text())["jobs"]
    steps = jobs[job]["steps"]
    checkpoint = next(s for s in steps if s.get("uses") == "./.github/actions/archive-checkpoint")
    assert checkpoint["with"]["kind"] == kind
    assert steps.index(checkpoint) < next(i for i, s in enumerate(steps) if s.get("name") == step)
    if job != "server_bootstrap":
        artifact = next(
            s for s in steps if s.get("uses", "").startswith("actions/upload-artifact@")
        )
        assert "github.run_attempt" in artifact["with"]["name"]
        report = next(
            s for s in steps if s.get("uses") == "./.github/actions/report-archive-install"
        )
        assert steps.index(artifact) < steps.index(report)
        assert report["with"]["kind"] == kind
        assert report["with"]["outcome"] == "${{ steps.archive_install.outcome }}"
        assert next(s for s in steps if s.get("id") == "archive_install")["name"] == step
        assert report["continue-on-error"] and "always()" in report["if"]
    assert job in jobs["archive_attempts"]["needs"]
    assert "always()" in jobs["archive_attempts"]["if"]
