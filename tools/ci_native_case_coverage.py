"""Summarize the three maintained native fixtures from read-only GitHub Actions evidence."""

import argparse
import json
import os
import re
import subprocess
from functools import cache
from hashlib import sha256
from pathlib import Path

from ci_archive_install_history import (
    ARCHIVE_LIMIT,
    actions_timestamp,
    positive_int,
    read_archive,
    uploaded_during,
)
from ci_test_performance import github_api

SELF = ".github/workflows/native-case-coverage.yml"
WORKFLOWS = {
    "prefab": ("prebuilt-player-pair.yml",),
    "pointer": ("fork-beta-tools.yml", "unity-tests.yml"),
    "recovery": ("release-qualification.yml",),
}
NAMES = {"prefab": "Prefab", "pointer": "Pointer", "recovery": "Recovery"}
UPLOADS = {
    "prefab": "Retain verified prefab native evidence",
    "pointer": "Upload source-bound mixed UI verdict",
    "recovery": "Retain verified prefab process-death evidence",
}
ARTIFACT = re.compile(
    r"native-case-(?:prefab|recovery|pointer-(?P<version>[0-9]+\.[0-9]+\.[0-9]+[abfp][0-9]+))"
    r"-(?P<run>[1-9][0-9]{0,19})-(?P<attempt>[1-9][0-9]{0,5})"
)
SHA = re.compile(r"[0-9a-f]{40}")
ERRORS = (KeyError, TypeError, ValueError, OSError, RecursionError, subprocess.SubprocessError)


class Unavailable(ValueError):
    """A fixed diagnostic suitable for a public check summary."""


def require(ok, reason):
    if not ok:
        raise Unavailable(reason)


def checked_payload(case, files, sha):
    """The success-only fixture artifact also has to describe the checked source and outcome."""
    if case == "prefab":
        report = json.loads(files["consistency.json"])
        candidate = report["semantics"]["builds"]["candidate"]
        require(
            report["kind"] == "verification_player_pair_consistency"
            and report["status"] == "consistent"
            and len(set(report["jobs"]["first"] + report["jobs"]["second"])) == 4
            and report["verifier_commit"] == candidate["package_commit"] == sha
            and SHA.fullmatch(candidate["project_commit"])
            and report["project_checkout_commit"] == candidate["project_commit"],
            "The admitted pair does not establish both independent replays of this revision.",
        )
    elif case == "pointer":
        require(json.loads(files["source.json"])["source_sha"] == sha, "Pointer checkout differs.")
        for scale in ("1x", "1.5x"):
            report = json.loads(files[f"{scale}-comparison.json"])
            require(
                report["status"] == "diverged"
                and report["first_divergence"]["stage"] == "first_recipient"
                and report["gameplay_state"]["baseline"]["after"]["inventory_open"] is False
                and report["gameplay_state"]["candidate"]["after"]["inventory_open"] is True
                and report["gameplay_state"]["baseline"]["after"]["inventory_open_count"] == 0
                and report["gameplay_state"]["candidate"]["after"]["inventory_open_count"] == 1
                and all(
                    report[stage]["baseline"]["backend"] == "uitoolkit"
                    and report[stage]["candidate"]["backend"] == "ugui"
                    for stage in ("first_recipient", "terminal_handler")
                ),
                "The two-scale pointer comparison is incomplete.",
            )
    else:
        report = json.loads(files["process-death.json"])
        require(
            report["native"] is True and report["source_sha"] == sha, "Recovery checkout differs."
        )
        for boundary, count, state, phase in (
            ("prefab_saved", 0, "unknown", "creating"),
            ("created", 0, "conflict", "created"),
            ("registration_saved", 1, "unknown", "registering"),
        ):
            cut = report["cuts"][boundary]
            require(
                cut["forced_termination"] is True
                and cut["exit_code"] != 0
                and positive_int(cut["terminated_pid"])
                and positive_int(cut["process_id"])
                and cut["state"] == state
                and cut["entry_count"] == count
                and cut["recorded_phase"] == phase
                and all(cut["disk"][k] == cut[k] for k in ("guid", "entry_count", "user_address"))
                and re.fullmatch(r"[0-9a-f]{32}", cut["guid"])
                and re.fullmatch(r"[0-9a-f]{32}", cut["run_id"])
                and cut["user_address"] == "manual/" + cut["run_id"],
                "Recovery lacks an independently reopened persistence boundary.",
            )


def producer(case, run, repo):
    return (
        run["path"] in {".github/workflows/" + f for f in WORKFLOWS[case]}
        and run["event"] in ("push", "workflow_dispatch")
        and run["head_branch"] == "beta"
        and (run.get("head_repository") or {}).get("full_name") == repo
        and run["repository"]["full_name"] == repo
    )


def job_name(case, version, path):
    if case == "prefab":
        return "Compare independent Windows Player evidence"
    if case == "recovery":
        return "package_shapes / Windows package package shapes (6000.0.69f1)"
    prefix = (
        "Unity and optional integration tests / " if path.endswith("/fork-beta-tools.yml") else ""
    )
    return prefix + f"Optional mixed-ui-pointer on Unity {version}"


class Coverage:
    def __init__(self, repo, fetch=github_api):
        require(re.fullmatch(r"[\w.-]+/[\w.-]+", repo), "Invalid repository.")
        self.repo = repo
        self.base = f"repos/{repo}/"
        self.web = f"https://github.com/{repo}"
        self.fetch = fetch
        self.document = cache(lambda path: json.loads(fetch(self.base + path)))
        self.inventory = cache(self._inventory)

    def _inventory(self, path, field, size):
        rows, seen, total = [], set(), None
        sep = "&" if "?" in path else "?"
        for page in range(1, 501 // size + 1):
            data = self.document(f"{path}{sep}page={page}&per_page={size}")
            count, batch = data["total_count"], data[field]
            require(
                type(count) is int
                and 0 <= count <= 500
                and total in (None, count)
                and len(batch) == min(size, count - len(rows)),
                "Actions inventory is incomplete.",
            )
            total = count
            for row in batch:
                require(
                    positive_int(row["id"]) and row["id"] not in seen, "Ambiguous Actions record."
                )
                seen.add(row["id"])
                rows.append(row)
            if len(rows) == total:
                return rows
        raise Unavailable("Actions inventory exceeded the collection bound.")

    def jobs(self, run_id, attempt=None):
        suffix = f"attempts/{attempt}/jobs" if attempt else "jobs?filter=latest"
        return self.inventory(f"actions/runs/{run_id}/{suffix}", "jobs", 25)

    def archive(self, art, limits):
        require(
            art["expired"] is False and 0 < art["size_in_bytes"] <= ARCHIVE_LIMIT,
            "Native artifact expired or outside the size bound.",
        )
        content = self.fetch(self.base + f"actions/artifacts/{art['id']}/zip")
        require(
            len(content) == art["size_in_bytes"]
            and "sha256:" + sha256(content).hexdigest() == art["digest"],
            "Downloaded native artifact differs from the Actions digest.",
        )
        return read_archive(content, limits)

    def missing_pair(self, run, artifacts):
        # The legacy admission name lacks an attempt suffix. Use it for a negative reason
        # only when a single attempt could have produced it.
        ids = [a["id"] for a in artifacts if a["name"] == "player-pair-admission"]
        if len(ids) != 1 or run["run_attempt"] != 1:
            return None
        art = self.document(f"actions/artifacts/{ids[0]}")
        origin = art["workflow_run"]
        if (
            art["id"] != ids[0]
            or art["name"] != "player-pair-admission"
            or origin["id"] != run["id"]
            or origin["head_sha"] != run["head_sha"]
            or origin["repository_id"] != run["repository"]["id"]
            or origin["head_repository_id"] != run["repository"]["id"]
            or art["expired"]
        ):
            return None
        report = json.loads(self.archive(art, {"admission.json": 4096})["admission.json"])
        if (
            report.get("verifier_commit") == run["head_sha"]
            and report.get("status") == "unavailable"
            and report.get("native_execution") is False
            and isinstance(report.get("missing"), list)
            and report["missing"]
        ):
            return {
                "state": "unavailable",
                "reason": "Reviewed Player build pins missing.",
                "source": f"{self.web}/actions/runs/{run['id']}/artifacts/{art['id']}",
            }
        return None

    def observation(self, case, artifact_id, run, sha):
        require(
            producer(case, run, self.repo) and run["head_sha"] == sha, "Untrusted native producer."
        )
        art = self.document(f"actions/artifacts/{artifact_id}")
        origin = art["workflow_run"]
        name = ARTIFACT.fullmatch(art["name"])
        require(
            name
            and art["name"].startswith(f"native-case-{case}-")
            and art["id"] == artifact_id
            and art["expired"] is False
            and 0 < art["size_in_bytes"] <= ARCHIVE_LIMIT
            and origin["id"] == int(name["run"]) == run["id"]
            and origin["repository_id"] == origin["head_repository_id"] == run["repository"]["id"]
            and origin["head_sha"] == sha,
            "Artifact is expired or its producer identity differs.",
        )
        run_id, attempt = run["id"], int(name["attempt"])
        expected = job_name(case, name["version"], run["path"])
        require(0 < attempt <= run["run_attempt"], "Invalid artifact attempt.")
        current = [j for j in self.jobs(run_id) if j["name"] == expected]
        original = [
            j
            for j in self.jobs(run_id, attempt)
            if j["name"] == expected and j["run_attempt"] == attempt
        ]
        require(
            len(current) == len(original) == 1, "The producing job was unavailable or ambiguous."
        )
        job, latest = original[0], current[0]
        created = actions_timestamp(art.get("created_at"))
        started = actions_timestamp(job.get("started_at"))
        completed = actions_timestamp(job.get("completed_at"))
        # Actions may clone a successful job into a partial rerun under a new attempt/job ID.
        # Match its complete original runner timeline; an actually rerun or failed job supersedes it.
        keys = ("run_id", "head_sha", "name", "runner_id", "started_at", "completed_at", "steps")
        require(
            job["conclusion"] == latest["conclusion"] == "success"
            and job["status"] == latest["status"] == "completed"
            and job["run_id"] == run_id
            and job["head_sha"] == sha
            and positive_int(job["runner_id"])
            and started is not None
            and completed is not None
            and created is not None
            and started <= created <= completed
            and all(job[k] == latest[k] for k in keys)
            and (latest["id"] == job["id"] or latest["run_attempt"] > attempt)
            and sum(step.get("name") == UPLOADS[case] for step in job["steps"]) == 1
            and uploaded_during(job, UPLOADS[case], created),
            "A later job execution superseded the artifact, or the producing timeline differs.",
        )
        files = self.archive(
            art,
            {
                "consistency.json": 1024 * 1024,
                "source.json": 4096,
                "1x-comparison.json": 256 * 1024,
                "1.5x-comparison.json": 256 * 1024,
                "process-death.json": 64 * 1024,
            },
        )
        checked_payload(case, files, sha)
        url = f"{self.web}/actions/runs/{run_id}"
        return {
            "sha": sha,
            "run_id": run_id,
            "attempt": attempt,
            "job_id": job["id"],
            "artifact_id": artifact_id,
            "digest": art["digest"],
            "job": f"{url}/job/{job['id']}",
            "artifact": f"{url}/artifacts/{artifact_id}",
        }

    def current(self, case, runs, sha):
        eligible = sorted(
            (r for r in runs if producer(case, r, self.repo)), key=lambda r: r["id"], reverse=True
        )
        workflow = WORKFLOWS[case][0]
        row = {
            "state": "unavailable",
            "reason": "No trusted beta producer run recorded for this SHA.",
            "source": f"{self.web}/actions/workflows/{workflow}",
        }
        seen = set()
        for run in eligible:
            if run["path"] in seen:
                continue
            seen.add(run["path"])
            run_id = run["id"]
            url = f"{self.web}/actions/runs/{run_id}"
            try:
                artifacts = self.inventory(f"actions/runs/{run_id}/artifacts", "artifacts", 100)
                candidates = [a for a in artifacts if a["name"].startswith(f"native-case-{case}-")]
                for a in sorted(candidates, key=lambda a: a["id"], reverse=True):
                    try:
                        evidence = self.observation(case, a["id"], run, sha)
                    except ERRORS as exc:
                        row.update(reason=diagnostic(exc), source=f"{url}/artifacts/{a['id']}")
                        continue
                    return {
                        **row,
                        "state": "current",
                        "reason": "Native fixture observed.",
                        "source": evidence["job"],
                        "evidence": evidence,
                    }
                if candidates:
                    continue
                if case == "prefab" and (missing := self.missing_pair(run, artifacts)):
                    return missing
                reason = (
                    "Producer is still running."
                    if run["status"] != "completed"
                    else "Producer retained no successful native fixture receipt."
                )
                source = url
                for job in self.jobs(run_id):
                    name = job["name"]
                    if (
                        case == "prefab"
                        and (
                            "independent Windows Player" in name
                            or "Execute reviewed broken" in name
                        )
                        or case == "pointer"
                        and "Optional " in name
                        and ("mixed-ui-pointer" in name or "matrix.suite.id" in name)
                        or case == "recovery"
                        and (name == "package_shapes" or "Windows package package shapes" in name)
                    ) and job["conclusion"] in ("failure", "cancelled", "skipped", "timed_out"):
                        reason = f"Native job {job['conclusion']}."
                        source = f"{url}/job/{job['id']}"
                        break
                row.update(reason=reason, source=source)
            except ERRORS as exc:
                row.update(reason=diagnostic(exc), source=url)
        return row

    def collect(self, sha, head_repo, archive_ids=()):
        require(SHA.fullmatch(sha), "Expected the full observed SHA.")
        runs = self.inventory(f"actions/runs?head_sha={sha}", "workflow_runs", 50)
        runs = [
            run
            for run in runs
            if run["head_sha"] == sha
            and run["repository"]["full_name"] == self.repo
            and run["path"] != SELF
            and run["event"] in ("push", "workflow_dispatch", "pull_request")
            and (run.get("head_repository") or {}).get("full_name")
            == (head_repo if run["event"] == "pull_request" else self.repo)
        ]
        cases = {case: self.current(case, runs, sha) for case in WORKFLOWS}
        notices = []
        for artifact_id in archive_ids:
            try:
                art = self.document(f"actions/artifacts/{artifact_id}")
                require(ARTIFACT.fullmatch(art["name"]), "Unrecognized native artifact.")
                case = art["name"].split("-")[2]
                run = self.document(f"actions/runs/{int(art['workflow_run']['id'])}")
                earlier = run["head_sha"]
                require(
                    SHA.fullmatch(earlier) and earlier != sha,
                    "Archive needs an earlier source revision.",
                )
                comparison = self.document(f"compare/{earlier}...{sha}?per_page=1")
                require(
                    comparison["status"] == "ahead"
                    and comparison["merge_base_commit"]["sha"] == earlier,
                    "Archived revision is not a proven ancestor.",
                )
                evidence = self.observation(case, artifact_id, run, earlier)
                cases[case].setdefault("archived", evidence)
                if cases[case]["state"] == "unavailable":
                    cases[case]["state"] = "archived"
            except ERRORS as exc:
                notices.append(f"Prior artifact {artifact_id}: {diagnostic(exc)}")
        return {
            "sha": sha,
            "repository": self.repo,
            "cases": cases,
            "notices": notices,
            "runs": [
                {
                    "id": r["id"],
                    "attempt": r["run_attempt"],
                    "workflow": r["path"],
                    "outcome": r["conclusion"] or r["status"],
                }
                for r in runs
            ],
        }


def diagnostic(exc):
    return (
        str(exc)
        if isinstance(exc, Unavailable)
        else "Required Actions metadata or receipt unavailable."
    )


def render(report):
    cases, runs = report["cases"], report["runs"]
    current = sum(c["state"] == "current" for c in cases.values())
    count = (
        f"{sum(r['outcome'] == 'success' for r in runs)}/{len(runs)}"
        if runs is not None
        else "unknown"
    )
    title = " · ".join(f"{NAMES[case]} {row['state']}" for case, row in cases.items())
    title = f"{current}/3 native · {title} · CI {count}"
    lines = [
        f"## {title}",
        "",
        f"Observed source `{report['sha']}`.",
        "",
        "| Fixture | Current native evidence | Prior native observation |",
        "| --- | --- | --- |",
    ]
    for case, row in cases.items():

        def reference(e):
            return f"[`{e['sha'][:10]}`]({e['job']}) · [artifact {e['artifact_id']}, attempt {e['attempt']}]({e['artifact']})"

        now = (
            reference(row["evidence"])
            if "evidence" in row
            else f"[{row['reason']}]({row['source']})"
        )
        prior = reference(row["archived"]) if "archived" in row else "None admitted"
        lines.append(f"| {NAMES[case]} | {now} | {prior} |")
    lines.extend(
        [
            "",
            f"Other workflow runs recorded for this SHA at collection time: **{count} successful**.",
        ]
    )
    for r in runs or []:
        path = r["workflow"].removeprefix(".github/workflows/")
        if not re.fullmatch(r"[\w.-]+\.ya?ml", path):
            path = "Workflow"
        lines.append(
            f"- [{path} (attempt {r['attempt']})](https://github.com/{report['repository']}/actions/runs/{r['id']}/attempts/{r['attempt']}) — {r['outcome']}"
        )
    lines.extend(["", *report["notices"]])
    return title, "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sha", required=True)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--head-repo", required=True)
    parser.add_argument("--archive-ids", default="")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    collector = Coverage(args.repository)
    require(SHA.fullmatch(args.sha), "Expected a full commit SHA.")
    ids = [i.strip() for i in args.archive_ids.split(",") if i.strip()]
    require(
        len(ids) <= 12 and all(re.fullmatch(r"[1-9][0-9]{0,19}", i) for i in ids),
        "Expected up to 12 numeric prior artifact IDs.",
    )
    try:
        report = collector.collect(args.sha, args.head_repo, [int(i) for i in ids])
    except ERRORS as exc:
        reason = diagnostic(exc)
        report = {
            "sha": args.sha,
            "repository": args.repository,
            "runs": None,
            "notices": [reason],
            "cases": {
                case: {
                    "state": "unavailable",
                    "reason": reason,
                    "source": f"{collector.web}/actions",
                }
                for case in WORKFLOWS
            },
        }
    title, summary = render(report)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as f:
        f.write(summary)
    with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as f:
        print(f"title={title}", file=f)


if __name__ == "__main__":
    main()
