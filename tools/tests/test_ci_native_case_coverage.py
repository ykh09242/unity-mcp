"""Check the coverage decision against Actions attempts, immutable artifacts and source identities."""

import hashlib
import io
import json
import zipfile
from copy import deepcopy
from urllib.parse import parse_qs, urlsplit

import pytest
from ci_native_case_coverage import SELF, UPLOADS, Coverage, checked_payload, render

REPO = "owner/repo"
HEAD = "a" * 40
OLD = "b" * 40
MERGE = "c" * 40
VERSION = "6000.0.86f1"


def run(run_id, workflow, sha=HEAD, **overrides):
    return {
        "id": run_id,
        "path": ".github/workflows/" + workflow,
        "head_sha": sha,
        "event": "push",
        "head_branch": "beta",
        "run_attempt": 2,
        "head_repository": {"full_name": REPO},
        "repository": {"full_name": REPO, "id": 71},
        "status": "completed",
        "conclusion": "success",
        **overrides,
    }


def pointer_files(sha):
    stages = {"baseline": {"backend": "uitoolkit"}, "candidate": {"backend": "ugui"}}
    comparison = json.dumps(
        {
            "status": "diverged",
            "first_divergence": {"stage": "first_recipient"},
            "first_recipient": stages,
            "terminal_handler": stages,
            "gameplay_state": {
                "baseline": {"after": {"inventory_open": False, "inventory_open_count": 0}},
                "candidate": {"after": {"inventory_open": True, "inventory_open_count": 1}},
            },
        }
    )
    return {
        "source.json": json.dumps({"source_sha": sha}),
        "1x-comparison.json": comparison,
        "1.5x-comparison.json": comparison,
    }


class Records:
    def __init__(self, sha=HEAD):
        self.producer = run(101, "unity-tests.yml", sha, event="workflow_dispatch")
        self.runs = [self.producer]
        self.original = {
            "name": f"Optional mixed-ui-pointer on Unity {VERSION}",
            "id": 400,
            "run_id": 101,
            "run_attempt": 1,
            "runner_id": 805,
            "status": "completed",
            "head_sha": sha,
            "conclusion": "success",
            "started_at": "2026-11-20T03:10:00Z",
            "completed_at": "2026-11-20T03:11:00Z",
            "steps": [
                {
                    "name": UPLOADS["pointer"],
                    "status": "completed",
                    "conclusion": "success",
                    "started_at": "2026-11-20T03:10:50Z",
                    "completed_at": "2026-11-20T03:10:57Z",
                }
            ],
        }
        self.latest = {**deepcopy(self.original), "id": 401, "run_attempt": 2}
        self.artifact = {
            "id": 600,
            "name": f"native-case-pointer-{VERSION}-101-1",
            "workflow_run": {
                "head_sha": sha,
                "id": 101,
                "repository_id": 71,
                "head_repository_id": 71,
            },
            "expired": False,
            "created_at": "2026-11-20T03:10:55Z",
        }
        self.files = pointer_files(sha)
        self.repack()
        self.ancestor_status = "ahead"

    def repack(self):
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, "w") as z:
            for path, value in self.files.items():
                z.writestr(path, value)
        self.content = archive.getvalue()
        self.artifact.update(
            size_in_bytes=len(self.content),
            digest="sha256:" + hashlib.sha256(self.content).hexdigest(),
        )

    def fetch(self, endpoint):
        route = urlsplit(endpoint)
        path = route.path.removeprefix(f"repos/{REPO}/")

        def inventory(key, values):
            return {key: values, "total_count": len(values)}

        if path == "actions/runs":
            sha = parse_qs(route.query)["head_sha"][0]
            data = inventory("workflow_runs", [r for r in self.runs if r["head_sha"] == sha])
        elif path == "actions/runs/101/jobs":
            data = inventory("jobs", [self.latest])
        elif path == "actions/runs/101/attempts/1/jobs":
            data = inventory("jobs", [self.original])
        elif path == "actions/runs/101/artifacts":
            data = inventory("artifacts", [self.artifact])
        elif path == "actions/artifacts/600":
            data = self.artifact
        elif path == "actions/artifacts/600/zip":
            return self.content
        elif path == "actions/runs/101":
            data = self.producer
        elif path == f"compare/{OLD}...{HEAD}":
            data = {"status": self.ancestor_status, "merge_base_commit": {"sha": OLD}}
        else:
            raise AssertionError(f"Unexpected Actions read: {path}")
        return json.dumps(data).encode()

    def report(self, ids=(), head_repo=REPO):
        return Coverage(REPO, self.fetch).collect(HEAD, head_repo, ids)


def test_job_carried_forward_retains_original_attempt_and_native_artifact():
    records = Records()
    records.runs.extend(
        [
            run(102, "native-case-coverage.yml"),
            run(105, "compile.yml"),
            run(106, "tests.yml", conclusion="failure"),
            run(107, "other.yml", MERGE),
            run(108, "foreign.yml", repository={"full_name": "another/repo", "id": 99}),
        ]
    )
    report = records.report()
    row = report["cases"]["pointer"]
    assert row["state"] == "current"
    assert row["evidence"]["attempt"] == 1
    assert row["evidence"]["job_id"] == 400
    assert row["evidence"]["artifact_id"] == 600
    title, markdown = render(report)
    assert (
        title == "1/3 native · Prefab unavailable · Pointer current · Recovery unavailable · CI 2/3"
    )
    assert "/actions/runs/101/job/400" in markdown
    assert "/actions/runs/101/artifacts/600" in markdown
    assert SELF not in {r["workflow"] for r in report["runs"]}


@pytest.mark.parametrize(
    "change",
    [
        "rerun",
        "failed",
        "wrong-origin",
        "expired",
        "superseded",
        "wrong-sha",
        "one-scale",
        "digest",
        "no-upload",
        "wrong-job",
        "wrong-workflow",
        "untrusted-branch",
        "pr-merge",
    ],
)
def test_current_requires_exact_native_source_and_original_successful_producer(change):
    records = Records()
    if change == "rerun":
        records.latest["runner_id"] += 1
    elif change == "failed":
        records.latest["conclusion"] = "failure"
    elif change == "wrong-origin":
        records.artifact["workflow_run"]["head_repository_id"] = 10
    elif change == "expired":
        records.artifact["expired"] = True
    elif change == "superseded":
        records.producer["run_attempt"] = 0
    elif change == "wrong-sha":
        records.files.update(pointer_files(OLD))
        records.repack()
    elif change == "one-scale":
        del records.files["1.5x-comparison.json"]
        records.repack()
    elif change == "digest":
        records.artifact["digest"] = "sha256:" + "f" * 64
    elif change == "no-upload":
        records.latest["steps"] = records.original["steps"] = []
    elif change == "wrong-job":
        records.latest["name"] = records.original["name"] = "Unrelated passing job"
    elif change == "wrong-workflow":
        records.producer["path"] = ".github/workflows/compile.yml"
    elif change == "untrusted-branch":
        records.producer["head_branch"] = "feature"
    elif change == "pr-merge":
        records.producer["event"] = "pull_request"
    assert records.report()["cases"]["pointer"]["state"] == "unavailable"


def test_archive_requires_explicit_id_verified_ancestor_and_remains_prior():
    records = Records(OLD)
    assert records.report()["cases"]["pointer"]["state"] == "unavailable"
    report = records.report([600])
    assert report["cases"]["pointer"]["state"] == "archived"
    assert report["cases"]["pointer"]["archived"]["sha"] == OLD
    assert render(report)[0].startswith("0/3 native")
    records.ancestor_status = "diverged"
    assert records.report([600])["cases"]["pointer"]["state"] == "unavailable"


@pytest.mark.parametrize("change", ["early", "late", "missing-start", "missing-end", "duplicate"])
def test_upload_step_must_uniquely_bound_artifact_creation(change):
    records = Records()
    if change in ("early", "late"):
        records.artifact["created_at"] = (
            "2026-11-20T03:10:49Z" if change == "early" else "2026-11-20T03:10:58Z"
        )
    else:
        for job in (records.original, records.latest):
            steps = job["steps"]
            if change == "duplicate":
                steps.append(deepcopy(steps[0]))
            else:
                del steps[0]["started_at" if change == "missing-start" else "completed_at"]
    assert records.report()["cases"]["pointer"]["state"] == "unavailable"


def test_fork_pr_head_and_merge_are_separate_and_self_does_not_count():
    records = Records()
    records.runs = [
        run(301, "checks.yml", event="pull_request", head_repository={"full_name": "fork/repo"}),
        run(302, "checks.yml", event="pull_request"),
        run(303, "checks.yml", MERGE, event="pull_request"),
        run(304, "native-case-coverage.yml", event="pull_request"),
    ]
    report = records.report(head_repo="fork/repo")
    assert [r["id"] for r in report["runs"]] == [301]
    assert all(row["state"] == "unavailable" for row in report["cases"].values())


def test_replaying_historical_candidate_does_not_prove_current_prefab_build():
    candidate = {"project_commit": MERGE, "package_commit": HEAD}
    record = {
        "kind": "verification_player_pair_consistency",
        "status": "consistent",
        "jobs": {"first": ["1", "2"], "second": ["3", "4"]},
        "semantics": {"builds": {"candidate": candidate}},
        "project_checkout_commit": MERGE,
        "verifier_commit": HEAD,
    }
    checked_payload("prefab", {"consistency.json": json.dumps(record)}, HEAD)
    candidate["package_commit"] = OLD
    with pytest.raises(ValueError):
        checked_payload("prefab", {"consistency.json": json.dumps(record)}, HEAD)
    candidate["package_commit"] = HEAD
    candidate["project_commit"] = OLD
    with pytest.raises(ValueError):
        checked_payload("prefab", {"consistency.json": json.dumps(record)}, HEAD)


def test_recovery_requires_observation_at_all_three_cuts():
    cut = {
        "forced_termination": True,
        "exit_code": 1,
        "terminated_pid": 11,
        "process_id": 22,
        "state": "unknown",
        "entry_count": 0,
        "guid": "1" * 32,
        "user_address": "manual/" + "2" * 32,
        "run_id": "2" * 32,
        "recorded_phase": "creating",
    }
    cuts = {
        "prefab_saved": cut,
        "created": {**cut, "state": "conflict", "recorded_phase": "created"},
        "registration_saved": {**cut, "entry_count": 1, "recorded_phase": "registering"},
    }
    for result in cuts.values():
        result["disk"] = {k: result[k] for k in ("guid", "entry_count", "user_address")}
    record = {"native": True, "source_sha": HEAD, "cuts": cuts}
    checked_payload("recovery", {"process-death.json": json.dumps(record)}, HEAD)
    cuts["registration_saved"]["forced_termination"] = False
    with pytest.raises(ValueError):
        checked_payload("recovery", {"process-death.json": json.dumps(record)}, HEAD)
