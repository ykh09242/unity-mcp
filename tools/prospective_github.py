"""Verify original GitHub enrollment, producer source, jobs and immutable artifact receipts."""

import hashlib
import io
import json
import os
import re
import subprocess
import zipfile
from datetime import datetime
from itertools import groupby

from prospective_pr import ROLES, compare, git

ENROLL = ".github/workflows/prospective-enrollment.yml"
PRODUCER = ".github/workflows/prospective-controls.yml"
CONTROL = ".github/workflows/prospective-control-run.yml"
OID = r"[0-9a-f]{40}"
LIMIT = 12 * 1024 * 1024


def api(repository, path):
    return json.loads(subprocess.check_output(["gh", "api", f"repos/{repository}/{path}"]))


def activation(repository, introduction):
    endpoint = "actions/workflows/prospective-pr.yml/runs"
    listing = api(repository, endpoint + "?per_page=1")
    last = (listing["total_count"] + 99) // 100
    # GitHub timestamps show when the workflow actually reached beta, including
    # grouped pushes whose receiving SHA descends from the introducing commit.
    # Read unfiltered pages oldest first to avoid the filtered API's 1000-result cap.

    def push_receipt(run):
        match = re.fullmatch(rf"Cohort push ({OID}) ({OID})", run["display_title"])
        if not (
            match
            and run["path"] == ".github/workflows/prospective-pr.yml"
            and run["event"] == "push"
            and run["head_branch"] == "beta"
            and run["repository"]["full_name"] == repository
            and run["head_sha"] == match[2]
        ):
            return None
        before, after = match.groups()
        subprocess.run(["git", "cat-file", "-e", f"{before}^{{commit}}"], check=True)
        after_contains = subprocess.run(
            ["git", "merge-base", "--is-ancestor", introduction, after], check=False
        ).returncode
        before_contains = subprocess.run(
            ["git", "merge-base", "--is-ancestor", introduction, before], check=False
        ).returncode
        if after_contains == 0 and before_contains == 1:
            return {
                "activation_commit": introduction,
                "activation_before": before,
                "activation_head": after,
                "activation_run_id": run["id"],
                "activation_at": run["created_at"],
            }
        return None

    for page in range(last, 0, -1):
        batch = api(repository, endpoint + f"?per_page=100&page={page}")["workflow_runs"]
        for run in sorted(batch, key=lambda r: (r["created_at"], r["id"])):
            if receipt := push_receipt(run):
                return receipt
    if os.environ.get("GITHUB_EVENT_NAME") == "push":
        if receipt := push_receipt(api(repository, "actions/runs/" + os.environ["GITHUB_RUN_ID"])):
            return receipt
    raise ValueError("First receiving beta push activation unavailable")


def opened_prs(repository, activation_at):
    # Query the canonical PR connection across all states and targets.
    for page in range(1, 10001):
        batch = api(
            repository, f"pulls?state=all&sort=created&direction=asc&per_page=100&page={page}"
        )
        yield from (r for r in batch if r["created_at"] > activation_at)
        if len(batch) < 100:
            return
    raise ValueError("PR opening history exceeds pagination limit")


def original_target(repository, row):
    owner, name = repository.split("/")
    query = """query($owner:String!,$repo:String!,$number:Int!){
      repository(owner:$owner,name:$repo){pullRequest(number:$number){
        number createdAt baseRefName timelineItems(first:1,itemTypes:[BASE_REF_CHANGED_EVENT]){
          filteredCount nodes{... on BaseRefChangedEvent {previousRefName}}
        }
      }}
    }"""
    response = json.loads(
        subprocess.check_output(
            [
                "gh",
                "api",
                "graphql",
                "-f",
                f"query={query}",
                "-f",
                f"owner={owner}",
                "-f",
                f"repo={name}",
                "-F",
                f"number={row['number']}",
            ]
        )
    )
    if response.get("errors"):
        raise ValueError("Original target history unavailable")
    pr = response["data"]["repository"]["pullRequest"]
    if (pr["number"], pr["createdAt"]) != (row["number"], row["created_at"]):
        raise ValueError("PR opening identity differs")
    return initial_base(pr)


def initial_base(pr):
    changes = pr["timelineItems"]
    if changes["filteredCount"] == 0 and changes["nodes"] == []:
        base = pr["baseRefName"]
    elif changes["filteredCount"] > 0 and len(changes["nodes"]) == 1:
        base = changes["nodes"][0]["previousRefName"]
    else:
        raise ValueError("Incomplete original target history")
    if not isinstance(base, str) or not base:
        raise ValueError("Unknown original target branch")
    return base


def opening_receipts(repository, selected, activation_at):
    # Query by actual creation window. The run's trusted opening descriptor
    # must identify the same PR and opening time as the canonical PR record.
    end = max(row["opened_at"] for row in selected)
    day = end[:10]
    receipts = {}
    for page in range(1, 11):
        payload = api(
            repository,
            f"actions/runs?event=pull_request_target&created={activation_at[:10]}..{day}&per_page=100&page={page}",
        )
        for run in payload["workflow_runs"]:
            if run["path"] != ENROLL or run["event"] != "pull_request_target":
                continue
            match = re.fullmatch(
                rf"Opening beta ([1-9][0-9]*) (\S+) ({OID}) ({OID})", run["display_title"]
            )
            if match:
                number, opened, base, head = match.groups()
                receipts.setdefault((int(number), opened), []).append(
                    {"base": base, "head": head, "enrollment_run_id": run["id"]}
                )
        if len(payload["workflow_runs"]) < 100:
            break
    return receipts


def cohort(repository, activation_at):
    selected = []
    # The REST connection is oldest first. Resolve ties by PR number even if
    # the tied opening timestamps fall on two pages.
    for _, same_second in groupby(
        opened_prs(repository, activation_at), key=lambda r: r["created_at"]
    ):
        for pr in sorted(same_second, key=lambda r: r["number"]):
            try:
                base = original_target(repository, pr)
            except (ValueError, OSError, KeyError, TypeError, subprocess.CalledProcessError):
                base = None
            if base not in ("beta", None):
                continue
            selected.append(
                {
                    "slot": len(selected) + 1,
                    "pr": pr["number"],
                    "opened_at": pr["created_at"],
                    "original_target": base,
                    "base": None,
                    "head": None,
                    "full_failures": None,
                    "comparisons": None,
                }
            )
            if len(selected) == 3:
                break
        if len(selected) == 3:
            break
    if selected:
        try:
            receipts = opening_receipts(repository, selected, activation_at)
        except (ValueError, OSError, KeyError, TypeError, subprocess.CalledProcessError):
            receipts = {}
        for row in selected:
            found = receipts.get((row["pr"], row["opened_at"]), [])
            if len(found) == 1 and row["original_target"] == "beta":
                row.update(found[0])
            elif row["original_target"] is None:
                row["reason"] = (
                    "Original target history unavailable; possible beta opening reserves its slot"
                )
            else:
                row["reason"] = "Independent opening-head receipt missing or ambiguous"
    return selected


def only(rows, reason):
    if len(rows) != 1:
        raise ValueError(reason)
    return rows[0]


def trusted_producer(repository, row, run):
    match = re.fullmatch(rf"Controls {row['pr']} ({OID})", run["display_title"])
    if not (
        match
        and run["path"] == PRODUCER
        and run["event"] == "pull_request"
        and run["head_sha"] == row["head"]
        and run["status"] == "completed"
        and run["repository"]["full_name"] == repository
    ):
        raise ValueError("Producer does not identify the original PR head")
    merge = match[1]
    subprocess.run(["git", "merge-base", "--is-ancestor", row["base"], "HEAD"], check=True)
    if merge != row["head"]:
        commit = api(repository, f"git/commits/{merge}")
        if [p["sha"] for p in commit["parents"]] != [row["base"], row["head"]]:
            raise ValueError("Producer merge revision differs from opening base and head")
    blobs = {}
    for path in (PRODUCER, CONTROL):
        base_blob = git("rev-parse", f"{row['base']}:{path}")
        for revision in {row["head"], merge}:
            if api(repository, f"contents/{path}?ref={revision}")["sha"] != base_blob:
                raise ValueError("PR workflow definition differs from the trusted opening base")
        blobs[path] = base_blob
    return {"base_revision": row["base"], "workflow_revision": merge, "workflow_blobs": blobs}


def load_artifact(repository, run, catalog, artifact_id, name, filename):
    meta = catalog.get(artifact_id)
    if not meta or meta["name"] != name or meta["expired"] or meta["size_in_bytes"] > LIMIT:
        raise ValueError(f"Missing or expired producer artifact {name}")
    if meta.get("workflow_run", {}).get("id") != run["id"]:
        raise ValueError(f"Wrong producing run for {name}")
    data = subprocess.check_output(
        ["gh", "api", f"repos/{repository}/actions/artifacts/{artifact_id}/zip"]
    )
    if len(data) > LIMIT or "sha256:" + hashlib.sha256(data).hexdigest() != meta["digest"]:
        raise ValueError(f"Provider archive digest differs for {name}")
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            entries = archive.infolist()
            if len(entries) != 1 or entries[0].filename != filename or entries[0].file_size > LIMIT:
                raise ValueError(f"Unexpected contents for {name}")
            return json.loads(archive.read(entries[0]))
    except zipfile.BadZipFile as exc:
        raise ValueError(f"Invalid archive for {name}") from exc


def validate_execution(job, role, captured):
    step = only(
        [s for s in job["steps"] if s["name"] == "Execute the sealed selection"],
        "Missing invocation step",
    )
    marker = only(
        [s for s in job["steps"] if s["name"] == "Pytest returned failure"],
        "Missing host exit observation",
    )
    failed = captured["exit"] == 1
    if (
        job["conclusion"] != "success"
        or step["conclusion"] not in (("success", "failure") if failed else ("success",))
        or marker["conclusion"] != ("success" if failed else "skipped")
        or failed != any(v == "failed" for v in captured["cases"].values())
    ):
        raise ValueError(f"GitHub and pytest outcomes differ for {role}")
    start = datetime.fromisoformat(step["started_at"])
    end = datetime.fromisoformat(step["completed_at"])
    actual = datetime.fromisoformat(captured["started_at"])
    # REST step bounds have one-second precision; pytest measures its own lifetime.
    if (
        not start.timestamp() - 1 <= actual.timestamp() <= end.timestamp() + 1
        or actual.timestamp() + captured["seconds"] > end.timestamp() + 2
    ):
        raise ValueError(f"Elapsed pytest time outside host invocation for {role}")
    return {
        "job_id": job["id"],
        "step_number": step["number"],
        "conclusion": job["conclusion"],
        "started_at": step["started_at"],
        "completed_at": step["completed_at"],
    }


def paired_report(repository, row):
    query = f"actions/workflows/prospective-controls.yml/runs?event=pull_request&head_sha={row['head']}&per_page=100"
    runs = [
        r
        for r in api(repository, query)["workflow_runs"]
        if r["display_title"].startswith(f"Controls {row['pr']} ")
    ]
    run = only(runs, "Original producer run absent or ambiguous")
    if run.get("run_attempt", 1) != 1:
        run = api(repository, f"actions/runs/{run['id']}/attempts/1")
    if run["conclusion"] not in ("success", "failure"):
        raise ValueError("Original producer did not complete its observation jobs")
    provenance = trusted_producer(repository, row, run)
    run_id = run["id"]
    jobs = api(repository, f"actions/runs/{run_id}/attempts/1/jobs?per_page=100")["jobs"]
    witness = only(
        [j for j in jobs if j["name"].startswith("Evidence ") and j["conclusion"] == "success"],
        "Missing original receipt job",
    )
    match = re.fullmatch(r"Evidence (\d+)-(\d+)-(\d+)-(\d+)-(\d+)", witness["name"])
    if not match:
        raise ValueError("Malformed producer receipts")
    discovery_id, plan_id, *result_ids = map(int, match.groups())
    ids = [i for i in [discovery_id, plan_id, *result_ids] if i]
    if len(set(ids)) != len(ids):
        raise ValueError("Reused producer artifact ID")
    catalog = {
        a["id"]: a
        for a in api(repository, f"actions/runs/{run_id}/artifacts?per_page=100")["artifacts"]
    }
    # The list-for-run representation can omit the nested workflow_run field.
    # Resolve by ID before admitting bytes so it is explicitly bound to this run.
    for aid in ids:
        if aid in catalog and "workflow_run" not in catalog[aid]:
            catalog[aid] = api(repository, f"actions/artifacts/{aid}")
    inventory = load_artifact(
        repository, run, catalog, discovery_id, "prospective-discovery", "inventory.json"
    )
    plan = load_artifact(
        repository, run, catalog, plan_id, f"prospective-plan-{row['pr']}", "plan.json"
    )
    for field in ("pr", "base", "head", "opened_at"):
        if plan[field] != row[field]:
            raise ValueError("Plan differs from the independent opening receipt")
    expected_observer = {
        "revision": row["base"],
        "blobs": {
            path: git("rev-parse", f"{row['base']}:{path}")
            for path in ("tools/prospective_pr.py", "tools/prospective_pytest_runner.py")
        },
    }
    if (
        plan["observer"] != expected_observer
        or plan["run_id"] != str(run_id)
        or plan["attempt"] != "1"
        or plan["repository"] != repository
        or sorted(inventory) != plan["selection"]["full"]
    ):
        raise ValueError("Plan observer, discovery or producing attempt differs")
    for name in ("plan", "inventory"):
        only(
            [j for j in jobs if j["name"] == name and j["conclusion"] == "success"],
            f"Missing successful {name} job",
        )
    captures, records, gaps = {}, {}, {}
    for role, aid in zip(ROLES, result_ids, strict=True):
        if not aid:
            continue
        try:
            capture = load_artifact(
                repository, run, catalog, aid, f"prospective-run-{role}", role + ".json"
            )
            job = only(
                [j for j in jobs if j["name"] in (f"{role} / Observe {role}", f"Observe {role}")],
                f"Missing {role} job",
            )
            records[role] = validate_execution(job, role, capture)
            if datetime.fromisoformat(records[role]["started_at"]) < datetime.fromisoformat(
                catalog[plan_id]["created_at"]
            ):
                raise ValueError("Control predates plan archive")
            captures[role] = capture
        except (ValueError, OSError, KeyError, TypeError, subprocess.CalledProcessError) as exc:
            gaps[role] = str(exc)
    report = compare(plan, captures)
    report["gaps"].update(gaps)
    report["provenance"] = {
        **provenance,
        "observer": expected_observer,
        "receipt_job_id": witness["id"],
        "host_jobs": records,
        "artifacts": {a: catalog[a]["digest"] for a in ids if a in catalog},
    }
    return report
