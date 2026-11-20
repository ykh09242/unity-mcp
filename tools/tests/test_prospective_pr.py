"""Synthetic policy controls; the hosted cohort reads only real GitHub PRs and jobs."""

import os
import subprocess
import sys
from pathlib import Path

import prospective_github as provider
import prospective_pr as study
import pytest


NAMES = ["tools/tests/test_a.py::test_one", "tools/tests/test_b.py::test_two"]
EARLY = "2026-10-01T00:00:00+00:00"
LATE = "2026-10-01T00:01:00+00:00"


def fixture():
    plan = {
        "selection": {"manual": NAMES[:1], "automatic": NAMES[1:], "full": NAMES},
        "head": "b" * 40,
        "run_id": "42",
        "attempt": "1",
        "pr": 7,
        "repository": "sample/repo",
        "scope": "synthetic",
        "plan_at": EARLY,
        "manual_gap": None,
    }
    runs = {
        r: {
            "role": r,
            "head": plan["head"],
            "plan_sha256": study.digest(plan),
            "started_at": LATE,
            "seconds": 10 if r == "full" else 4,
            "exit": 1,
            "execution_id": f"42/1/{r}",
            "cases": {n: ("failed" if r == "manual" or n == NAMES[1] else "passed") for n in names},
        }
        for r, names in plan["selection"].items()
    }
    return plan, runs


def test_observed_failure_names_and_independent_time():
    result = study.compare(*fixture())
    assert result["state"] == "paired" and result["full_failures"] == NAMES[1:]
    assert result["comparisons"]["manual"]["missed_full_failures"] == NAMES[1:]
    assert result["comparisons"]["manual"]["additional_failures"] == NAMES[:1]
    assert result["additional_full_failures_caught_by_automatic"] == NAMES[1:]
    assert result["comparisons"]["automatic"]["seconds_saved"] == 6


def test_incomplete_and_unrelated_controls_never_generate_metrics():
    for key, value in (
        ("head", "a" * 40),
        ("execution_id", "42/1/manual"),
        ("started_at", EARLY),
        ("cases", {NAMES[0]: "passed"}),
        ("plan_sha256", "bad"),
    ):
        plan, runs = fixture()
        runs["full"][key] = value
        report = study.compare(plan, runs)
        assert report["state"] == "incomplete" and report["full_failures"] is None
        assert report["comparisons"]["manual"]["seconds_saved"] is None
    del runs["full"]
    assert study.compare(plan, runs)["gaps"]["full"] == "missing execution"


def test_human_block_resolves_only_observed_exact_cases_and_modules():
    assert study.manual_select("```prospective-tests\ntools/tests/test_a.py\n```", NAMES) == (
        NAMES[:1],
        None,
    )
    assert study.manual_select("```prospective-tests\n```", NAMES) == ([], None)
    for content in ("", "```prospective-tests\n--collect-only\n```"):
        assert study.manual_select(content, NAMES)[0] is None


def test_real_pytest_entry_points_on_separate_synthetic_checkout(tmp_path):
    runner = Path(study.__file__).with_name("prospective_pytest_runner.py")
    cli = Path(study.__file__)
    tests = tmp_path / "tools/tests"
    tests.mkdir(parents=True)
    for module in ("a", "b"):
        (tests / f"test_{module}.py").write_text(f"def test_{module}():\n    assert True\n")
    env = {
        **os.environ,
        "GITHUB_RUN_ID": "73",
        "GITHUB_RUN_ATTEMPT": "1",
        "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
        "GIT_AUTHOR_NAME": "Fixture",
        "GIT_COMMITTER_NAME": "Fixture",
        "GIT_AUTHOR_EMAIL": "fixture@example.test",
        "GIT_COMMITTER_EMAIL": "fixture@example.test",
    }

    def run(*command, check=True):
        return subprocess.run(
            command, cwd=tmp_path, env=env, text=True, capture_output=True, check=check
        )

    run("git", "init", "--quiet")
    run("git", "add", ".")
    run("git", "commit", "--quiet", "-m", "baseline")
    base = run("git", "rev-parse", "HEAD").stdout.strip()
    (tests / "test_b.py").write_text("def test_b():\n    assert False\n")
    run("git", "add", ".")
    run("git", "commit", "--quiet", "-m", "candidate")
    head = run("git", "rev-parse", "HEAD").stdout.strip()
    event = {
        "action": "opened",
        "pull_request": {
            "number": 1,
            "head": {"sha": head},
            "base": {"sha": base, "repo": {"full_name": "sample/repo"}},
            "user": {"type": "User", "login": "operator"},
            "created_at": EARLY,
            "body": "```prospective-tests\ntools/tests/test_a.py\n```",
        },
    }
    study.write(tmp_path / "event.json", event)
    run(sys.executable, str(runner), "inventory", "inventory.json")
    run(sys.executable, str(cli), "freeze", "event.json", "inventory.json", "plan.json")
    for role, expected in (("full", 1), ("automatic", 1), ("manual", 0)):
        process = run(
            sys.executable, str(runner), role, f"{role}.json", "--plan", "plan.json", check=False
        )
        assert process.returncode == expected, process.stdout + process.stderr
    run(sys.executable, str(cli), "assess", "plan.json", ".", "report.json")
    report = study.read(tmp_path / "report.json")
    assert report["state"] == "paired"
    assert report["additional_full_failures_caught_by_automatic"] == [
        "tools/tests/test_b.py::test_b"
    ]


def test_first_three_roster_retains_missing_full_and_missing_reports(monkeypatch, tmp_path):
    monkeypatch.setenv("GITHUB_REPOSITORY", "sample/repo")
    monkeypatch.setattr(
        study, "rollout", lambda: {"activation_commit": "c" * 40, "activation_at": EARLY}
    )

    original = [{"slot": i, "pr": i, "head": "d" * 40} for i in range(1, 4)]
    monkeypatch.setattr(provider, "cohort", lambda repo, activation: original)
    plan, runs = fixture()
    del runs["full"]
    missing_full = {**study.compare(plan, runs), "pr": 1}

    def retained(repo, row):
        if row["pr"] == 1:
            return missing_full
        raise ValueError("Expired independent controls")

    monkeypatch.setattr(provider, "paired_report", retained)
    output = tmp_path / "cohort.json"
    study.summarize(output)
    report = study.read(output)
    assert [r["pr"] for r in report["prs"]] == [1, 2, 3]
    assert report["prs"][1]["head"] == "d" * 40
    assert (report["enrolled_prs"], report["paired_prs"], report["incomplete_prs"]) == (3, 0, 3)
    assert report["metrics"]["manual"]["missed_failures"] is None


def test_canonical_openings_include_suppressed_event_and_preserve_original_base(monkeypatch):
    prs = [{"number": n, "created_at": f"2026-10-0{n}T00:00:00Z"} for n in range(1, 6)]
    monkeypatch.setattr(provider, "opened_prs", lambda repo, cutoff: iter(prs))

    def target(repo, row):
        num = row["number"]
        if num in (1, 2):
            return provider.initial_base(
                {
                    "baseRefName": "main" if num == 1 else "beta",
                    "timelineItems": {
                        "filteredCount": 1,
                        "nodes": [{"previousRefName": "beta" if num == 1 else "main"}],
                    },
                }
            )
        return provider.initial_base(
            {"baseRefName": "beta", "timelineItems": {"filteredCount": 0, "nodes": []}}
        )

    monkeypatch.setattr(provider, "original_target", target)

    def captures(repo, rows, activation):
        return {
            (r["number"], r["created_at"]): [
                {"head": "a" * 40, "base": "b" * 40, "enrollment_run_id": r["number"]}
            ]
            for r in prs[2:]
        }

    monkeypatch.setattr(provider, "opening_receipts", captures)
    rows = provider.cohort("sample/repo", EARLY)
    assert [r["pr"] for r in rows] == [1, 3, 4]
    assert rows[0]["head"] is None and rows[0]["comparisons"] is None and rows[0]["reason"]
    assert rows[1]["enrollment_run_id"] == 3

    def uncertain(repo, row):
        if row["number"] == 2:
            raise ValueError("missing timeline")
        return target(repo, row)

    monkeypatch.setattr(provider, "original_target", uncertain)
    rows = provider.cohort("sample/repo", EARLY)
    assert [r["pr"] for r in rows] == [1, 2, 3]
    assert rows[1]["original_target"] is None and rows[1]["reason"]


def test_trusted_activation_uses_first_actual_receiving_push_not_local_commit_time(monkeypatch):
    monkeypatch.delenv("GITHUB_EVENT_NAME", raising=False)
    intro, before, after, newer = ("1" * 40, "2" * 40, "3" * 40, "4" * 40)

    def push(i, a, b):
        return {
            "created_at": LATE,
            "id": i,
            "event": "push",
            "head_branch": "beta",
            "head_sha": b,
            "path": ".github/workflows/prospective-pr.yml",
            "display_title": f"Cohort push {a} {b}",
            "repository": {"full_name": "sample/repo"},
        }

    pushes = [push(11, after, newer), push(10, before, after)]
    monkeypatch.setattr(
        provider, "api", lambda repo, path: {"total_count": 2, "workflow_runs": pushes}
    )
    monkeypatch.setattr(
        provider.subprocess,
        "run",
        lambda command, check: subprocess.CompletedProcess(
            command, 1 if command[-1] == before and command[1] == "merge-base" else 0
        ),
    )
    result = provider.activation("sample/repo", intro)
    assert (
        result["activation_run_id"] == 10
        and result["activation_at"] == LATE
        and result["activation_head"] == after
    )
    pushes.pop()
    with pytest.raises(ValueError, match="First receiving"):
        provider.activation("sample/repo", intro)


def test_modified_producer_workflow_rejected_even_when_its_report_claims_match(monkeypatch):
    base, head = "b" * 40, "a" * 40
    row = {"pr": 9, "base": base, "head": head}
    run = {
        "display_title": f"Controls 9 {head}",
        "path": provider.PRODUCER,
        "repository": {"full_name": "sample/repo"},
        "event": "pull_request",
        "head_sha": head,
        "status": "completed",
    }
    monkeypatch.setattr(provider.subprocess, "run", lambda *a, **k: None)
    monkeypatch.setattr(provider, "git", lambda *a: "original-blob")
    monkeypatch.setattr(
        provider,
        "api",
        lambda repo, path: {"sha": "edited-blob" if provider.CONTROL in path else "original-blob"},
    )
    with pytest.raises(ValueError, match="definition differs"):
        provider.trusted_producer("sample/repo", row, run)


def test_mismatched_raw_artifact_owner_and_host_failure_marker_cannot_supply_metrics():
    meta = {"name": "trial", "expired": False, "size_in_bytes": 1, "workflow_run": {"id": 5}}
    with pytest.raises(ValueError, match="Wrong producing"):
        provider.load_artifact("sample/repo", {"id": 6}, {4: meta}, 4, "trial", "trial.json")
    job = {
        "id": 61,
        "conclusion": "success",
        "steps": [
            {"name": "Execute the sealed selection", "conclusion": "success"},
            {"name": "Pytest returned failure", "conclusion": "skipped"},
        ],
    }
    with pytest.raises(ValueError, match="outcomes differ"):
        provider.validate_execution(job, "full", {"exit": 1, "cases": {"test_a": "failed"}})


def test_verified_provider_jobs_recompute_metrics_from_raw_artifacts(monkeypatch):
    plan, captures = fixture()
    observer = {
        "revision": "c" * 40,
        "blobs": {
            p: "blob" for p in ("tools/prospective_pr.py", "tools/prospective_pytest_runner.py")
        },
    }
    plan.update(base=observer["revision"], opened_at=EARLY, observer=observer)
    row = {k: plan[k] for k in ("pr", "head", "base", "opened_at")}
    for capture in captures.values():
        capture["plan_sha256"] = study.digest(plan)
    run = {"id": 42, "display_title": "Controls 7 " + plan["head"], "conclusion": "success"}
    jobs = [
        {"name": "Evidence 1-2-3-4-5", "conclusion": "success", "id": 6},
        {"name": "plan", "conclusion": "success"},
        {"name": "inventory", "conclusion": "success"},
    ]
    for i, role in enumerate(study.ROLES):
        jobs.append(
            {
                "id": 10 + i,
                "name": f"{role} / Observe {role}",
                "conclusion": "success",
                "steps": [
                    {
                        "name": "Execute the sealed selection",
                        "conclusion": "success",
                        "number": 8,
                        "started_at": LATE,
                        "completed_at": "2026-10-01T00:01:30+00:00",
                    },
                    {"name": "Pytest returned failure", "conclusion": "success"},
                ],
            }
        )
    artifacts = [
        {"id": i, "digest": f"hash{i}", "created_at": EARLY, "workflow_run": {"id": 42}}
        for i in range(1, 6)
    ]

    def api(repo, path):
        if "head_sha=" in path:
            return {"workflow_runs": [run]}
        if "/jobs?" in path:
            return {"jobs": jobs}
        if "/artifacts?" in path:
            return {"artifacts": artifacts}
        raise AssertionError(path)

    monkeypatch.setattr(provider, "api", api)
    monkeypatch.setattr(provider, "git", lambda *a: "blob")
    monkeypatch.setattr(provider, "trusted_producer", lambda *a: {})
    bodies = [NAMES, plan, *(captures[r] for r in study.ROLES)]
    monkeypatch.setattr(
        provider, "load_artifact", lambda repo, run, catalog, aid, name, filename: bodies[aid - 1]
    )
    report = provider.paired_report(plan["repository"], row)
    assert report["state"] == "paired" and report["comparisons"]["manual"]["seconds_saved"] == 6
    assert report["provenance"]["receipt_job_id"] == 6
