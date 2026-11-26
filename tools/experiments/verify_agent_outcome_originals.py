"""Verify the native original-request cohort against the sealed schedule and receipts."""

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import sys
import zipfile

if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.experiments import live_agent_outcomes as outcomes
from tools.experiments import live_agent_report as report
from tools.experiments import live_agent_trial as trial


def require(condition, message):
    if not condition:
        raise ValueError(message)


def public_method(name):
    """The sealed recorder stores the tool name or the absolute resource URI."""
    if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", name):
        return "resources/read"
    require(re.fullmatch(r"[a-zA-Z0-9_.-]+", name), "Unknown public request binding")
    return "tools/call"


def actor_inventories(events):
    """Describe the actual tools/list replies received over the actor's own transport."""
    snapshots = []
    previous = None
    pending = set()
    for event in sorted(events, key=lambda e: e["time_ns"]):
        if event.get("surface") != "tools/list":
            continue
        names = sorted(event["names"])
        current = set(names)
        pending.update(current)
        page = {"count": len(names), "sha256": report.digest(names), "more": event["more"]}
        if previous is not None and not event["more"]:
            page.update(added=sorted(pending - previous), removed=sorted(previous - pending))
        if not event["more"]:
            previous = pending
            pending = set()
        snapshots.append(page)
    return snapshots


STDERR_CLASSES = {
    "argument_parsing",
    "resource_read",
    "unknown_server",
    "image_processing",
    "other_router",
    "other_host",
}


def host_stderr_summary(path):
    """Count structured host error records without publishing messages or local paths."""
    classes = Counter()
    if not path.is_file():
        return {"captured": False, "sha256": None, "error_records": 0, "classes": {}}
    raw = path.read_bytes()
    for line in raw.decode("utf-8", errors="replace").splitlines():
        plain = re.sub(r"\x1b\[[0-9;]*m", "", line)
        event = re.match(r"^\s*(?:\d{4}-\d\d-\d\dT\S+\s+)?ERROR\s+([\w:.-]+):\s*(.*)", plain)
        if not event:
            continue
        source, message = event.groups()
        kind = "other_host"
        if source == "codex_core::tools::router":
            kind = "other_router"
            for prefix, category in (
                ("error=failed to parse function arguments", "argument_parsing"),
                ("error=resources/read failed", "resource_read"),
                ("error=unknown MCP server", "unknown_server"),
                ("error=unable to process image", "image_processing"),
            ):
                if message.startswith(prefix):
                    kind = category
                    break
        classes[kind] += 1
    return {
        "captured": True,
        "sha256": hashlib.sha256(raw).hexdigest(),
        "error_records": sum(classes.values()),
        "classes": dict(sorted(classes.items())),
    }


def summarize(rows):
    counts = {}
    for profile in ("core", "full"):
        group = [r for r in rows if r["profile"] == profile]
        stderr = [r["host_stderr"] for r in group]
        stderr_only = [
            r["host_stderr"]["error_records"]
            for r in group
            if r["host_stderr"]["error_records"]
            and not (
                r["public_errors"]
                or r["unresolved_calls"]
                or r["host_failures"]
                or r["unresolved_host_calls"]
            )
        ]
        counts[profile] = {
            "scheduled": len(group),
            "submitted": sum(r["prompt_submissions"] for r in group),
            "delivered": sum(r["delivered"] for r in group),
            "completed_turns": sum(r["turn_completed"] for r in group),
            "failed_admission_gates": sum(len(r.get("admission_failures", [])) for r in group),
            "outcomes": dict(Counter(r["outcome"] for r in group)),
            "observed": sum(r["observed"] for r in group),
            "verified_native_edits": sum(r["native_effect"] is True for r in group),
            "host_approval_denials": sum(len(r["host_approval_receipts"]) for r in group),
            "public_calls": sum(len(r["calls"]) for r in group),
            "unsuccessful_public_replies": sum(r["public_errors"] for r in group),
            "calls_by_method": {
                method: {
                    "requests": sum(c["method"] == method for r in group for c in r["calls"]),
                    "unsuccessful_replies": sum(
                        c["method"] == method and c["ok"] is False
                        for r in group
                        for c in r["calls"]
                    ),
                    "missing_replies": sum(
                        c["method"] == method and c["ok"] is None for r in group for c in r["calls"]
                    ),
                }
                for method in ("tools/call", "resources/read")
            },
            "public_requests_without_reply": sum(r["unresolved_calls"] for r in group),
            "host_requests_without_terminal_event": sum(
                len(r["unresolved_host_calls"] or []) for r in group
            ),
            "failed_host_items": sum(len(r["host_failures"]) for r in group),
            "host_stderr_error_records": sum(s["error_records"] for s in stderr),
            "cases_with_host_stderr_errors": sum(bool(s["error_records"]) for s in stderr),
            "host_stderr_errors_in_otherwise_clean_cases": sum(stderr_only),
            "cases_with_only_host_stderr_errors": len(stderr_only),
            "host_stderr_error_classes": dict(
                sum((Counter(s["classes"]) for s in stderr), Counter())
            ),
            "completed_host_calls": sum(sum(r["host_calls_by_binding"].values()) for r in group),
            "host_errors_without_unsuccessful_wire_reply": sum(
                f["location"] == "host_error_without_unsuccessful_wire_reply"
                for r in group
                for f in r["host_failures"]
            ),
            "missing_verified_final": sum(not r["final_answer_verified"] for r in group),
        }
    return counts


def verify_case(case, spec):
    for key in ("id", "profile", "task", "language", "panel", "seed"):
        require(case[key] == spec[key], f"Changed {key} for {spec['id']}")
    digest = hashlib.sha256(spec["query"].encode()).hexdigest()
    require(
        type(case["prompt_submissions"]) is int and case["prompt_submissions"] in (0, 1),
        "Unexpected count of original submissions",
    )
    if case["turn_started"] or case["delivered"]:
        require(case["prompt_submissions"] == 1, "Actor started without original submission")
    if case["prompt_submissions"]:
        require(case["prompt_sha256"] == digest, f"Prompt mismatch for {spec['id']}")
    require(len(case["attempts"]) == 1, f"Unexpected replacement for {spec['id']}")
    require(case["outcome"] in {"pass", "fail", "incomplete", "unobserved"}, "Unresolved verdict")
    calls = case["calls"]
    stderr = case["host_stderr"]
    require(
        type(stderr["captured"]) is bool
        and (
            bool(re.fullmatch(r"[0-9a-f]{64}", str(stderr["sha256"])))
            if stderr["captured"]
            else stderr["sha256"] is None
        )
        and bool(case["prompt_submissions"]) == stderr["captured"]
        and type(stderr["error_records"]) is int
        and set(stderr["classes"]) <= STDERR_CLASSES
        and all(type(v) is int and v > 0 for v in stderr["classes"].values())
        and stderr["error_records"] == sum(stderr["classes"].values()),
        "Invalid host stderr classification",
    )
    require(case["public_errors"] == sum(c["ok"] is False for c in calls), "Error count differs")
    require(
        case["unresolved_calls"] == sum(c["ok"] is None for c in calls),
        "Missing reply count differs",
    )
    for c in calls:
        require(isinstance(c["binding"], str) and c["binding"], "Missing public binding")
        require(
            c["method"] in ("tools/call", "resources/read")
            and (c["method"] == "resources/read")
            == bool(re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", c["binding"])),
            "Public call/URI classification differs",
        )
        require(c["ok"] is None or type(c["ok"]) is bool, "Invalid public reply state")
    for key in ("answer_sha256", "evidence_sha256"):
        require(re.fullmatch(r"[0-9a-f]{64}", case[key]), f"Invalid {key}")
    indices = [receipt["index"] for receipt in case["positive_receipts"]]
    require(len(set(indices)) == len(indices), "Repeated positive receipt")
    for receipt in case["positive_receipts"]:
        index = receipt["index"]
        require(
            type(index) is int and 0 <= index < len(calls),
            "Positive receipt falls outside this attempt",
        )
        require(
            calls[index]["ok"] is True and calls[index]["receipt_sha256"] == receipt["sha256"],
            "Positive receipt differs from this successful public call",
        )
    assessment = case["native_assessment"]
    require(
        assessment["rubric_sha256"] == outcomes._digest(outcomes.RUBRICS[spec["task"]]),
        "Request rubric differs",
    )
    if case["outcome"] == "pass":
        require(
            case["observed"]
            and case["turn_completed"]
            and case["delivered"]
            and case["positive_receipts"]
            and case["final_answer_verified"]
            and case["answer_sha256"] != outcomes._digest(""),
            "Pass without completed observed answer",
        )
        require(
            assessment["result"] is None, "Native request or preservation criteria did not pass"
        )
        if spec["task"] in outcomes.EDITS:
            require(
                case["target_reached"] is True and case["native_effect"] is True,
                "Missing verified requested edit",
            )
        if case["reason"] == "verified_by_review":
            review = case["semantic_review"]
            require(
                review
                and outcomes._digest(review) == case["review_sha256"]
                and review["evidence_sha256"] == case["evidence_sha256"]
                and review["call_indices"] == indices,
                "Unbound semantic review",
            )
            require(
                all(
                    review[k] is True
                    for k in ("answer_correct", "evidence_supports", "constraints_respected")
                )
                and bool(review["reason"].strip()),
                "Incomplete request-specific review",
            )
        else:
            require(
                spec["task"] in outcomes.ACTIVE and case["reason"] == "exact_active_scene",
                "Unknown request-specific pass evidence",
            )
    if case["observed"]:
        require(
            set(case["state_sha256"]) == {"before", "after"}, "Missing independent native snapshots"
        )
        require(
            all(re.fullmatch(r"[0-9a-f]{64}", value) for value in case["state_sha256"].values()),
            "Invalid native state digest",
        )


def verify(data):
    schedule = {r["id"]: r for pair in trial.pairs() for r in pair}
    controls = data["baseline"]
    baseline = controls["cases"]
    expected = {
        k
        for k, r in schedule.items()
        if r["task"] == "sample_surface_mask" and r["profile"] == "core"
    }
    require(len(baseline) == 6 and {r["id"] for r in baseline} == expected, "Baseline six differ")
    require(
        controls["source_revision"] == "4f454080c0b95ae27b8510f74610200da81833cc",
        "Baseline revision differs",
    )
    for row in baseline:
        verify_case(row, schedule[row["id"]])
    rows = data["cases"]
    require(
        len(rows) == len(schedule) == 142 and {r["id"] for r in rows} == schedule.keys(),
        "Candidate schedule differs",
    )
    require(len({r["language"] for r in rows}) == 17, "Language coverage differs")
    require(data["actor"] == controls["actor"], "Actor or configured reasoning effort differs")
    require(data["turn_seconds"] == trial.TURN_SECONDS, "Actor deadline differs")
    require(
        data["actor_route"] == "original_ephemeral",
        "Formal originals require a single ordinary MCP actor turn",
    )
    require(re.fullmatch(r"[0-9a-f]{40}", data["candidate_product"]), "Missing pinned product")
    require(
        data["controls"]["actual_archived_server_source"]
        == data["controls"]["embedded_server_source"]
        == data["runtime_revision"]
        and re.fullmatch(r"[0-9a-f]{40}", data["runtime_revision"])
        and re.fullmatch(r"[0-9a-f]{40}", data["skill_revision"]),
        "Control product/server revision differs",
    )
    prefs = data["shared_preferences"]
    require(
        prefs["before_sha256"] == prefs["after_sha256"]
        and prefs["count"] == 130
        and prefs["shared_terrain"] is False
        and prefs == controls["shared_preferences"],
        "Shared native settings differ",
    )
    require(
        data["catalogs"]["core"]["tools"] < data["catalogs"]["full"]["tools"],
        "Core and full inventories are not distinct",
    )
    matched = data["matched_disabled"]
    require(
        matched["source_revision"] == data["candidate_product"]
        and matched["runtime_revision"] == data["runtime_revision"]
        and matched["skill_revision"] == data["skill_revision"]
        and matched["actor_route"] == data["actor_route"],
        "Changed product or host for matched settings",
    )
    require(
        len(matched["cases"]) == 6 and {r["id"] for r in matched["cases"]} == expected,
        "Missing matched disabled controls",
    )
    require(summarize(matched["cases"]) == matched["summary"], "Matched disabled totals differ")
    require(
        matched["shared_preferences"] == prefs, "Shared settings differ between candidate panels"
    )
    for profile in ("core", "full"):
        enabled = set(data["catalogs"][profile]["tool_names"])
        disabled = set(matched["catalogs"][profile]["tool_names"])
        require(
            enabled - disabled == {"manage_terrain"} and not disabled - enabled,
            "More than terrain differs between matched catalogs",
        )
    require(
        set(data["catalogs"]["core"]["tool_names"]) < set(data["catalogs"]["full"]["tool_names"]),
        "Catalog membership is not nested",
    )
    for phase, expected_selection in (("final", ["manage_terrain"]), ("matched-disabled", [])):
        evidence = data["controls"][phase]
        require(
            evidence["source_revision"] == data["candidate_product"]
            and evidence["private_store"]["selected"] == expected_selection
            and evidence["private_store"]["inherited"] == prefs["count"],
            "Control Editor is not the selected pinned configuration",
        )
        require(
            evidence["collateral_unchanged"]
            and evidence["native_enabled"] == bool(expected_selection),
            "Unexpected collateral or native gate status",
        )
        if expected_selection:
            require(
                evidence["changed_native_fields"] == ["surfaceSamples"]
                and evidence["public_get_region"] == [[False]]
                and evidence["public_set_region"] == [[True]]
                and evidence["undo_registered"],
                "Expected scoped native read/edit control",
            )
            before = {(c["x"], c["y"]): c["surface"] for c in evidence["native_samples_before"]}
            after = {(c["x"], c["y"]): c["surface"] for c in evidence["native_samples_after"]}
            require(
                before.get((35, 42)) is False and after == before | {(35, 42): True},
                "Unexpected native terrain change",
            )
        else:
            require(
                evidence["diagnostic_reason"] == "native_disabled"
                and not evidence["after_selection_available"]
                and evidence["blocked"]
                and not evidence["changed_native_fields"]
                and evidence["state_sha256"]["before"] == evidence["state_sha256"]["after"],
                "Disabled control was not enforced",
            )
    for row in rows + matched["cases"]:
        spec = schedule[row["id"]]
        verify_case(row, spec)
        if row["delivered"]:
            require(
                row["readiness"]
                and row["readiness"]["ready"]
                and row["readiness"]["profile"] == row["profile"],
                "Missing selected project/inventory readiness before delivery",
            )
        child = row["actor_session"]
        require(
            bool(child) == bool(row["prompt_submissions"]),
            "Ordinary actor session differs from original submission",
        )
        if row["prompt_submissions"]:
            launch = row.get("launch") or {}
            require(
                launch.get("program") == "codex.exe"
                and launch.get("command") == "exec"
                and launch.get("ephemeral") is True
                and launch.get("prompt_source") == "stdin"
                and launch.get("model") == data["actor"]["model"]
                and launch.get("reasoning_effort") == data["actor"]["reasoning_effort"]
                and re.fullmatch(r"[0-9a-f]{64}", str(launch.get("argv_sha256", ""))),
                "Original process arguments differ from the sealed ordinary actor",
            )
        require(
            (child["public_calls"] if child else 0) == len(row["calls"]),
            "The original actor's public calls are missing",
        )
        if child:
            require(
                isinstance(row["unresolved_host_calls"], list)
                and child["unresolved_host_calls"] == len(row["unresolved_host_calls"]),
                "Outstanding host calls differ from actor terminal state",
            )
            require(
                type(child["turn_start_count"]) is int
                and child["turn_start_count"] in (0, 1)
                and child["turn_started"] == bool(child["turn_start_count"]),
                "Recorded unexpected host turn starts",
            )
            require(
                type(child["turn_completion_count"]) is int
                and child["turn_completion_count"] in (0, 1)
                and child["turn_completed"] == bool(child["turn_completion_count"]),
                "Recorded unexpected host turn completions",
            )
            require(
                child["turn_started"] == row["turn_started"]
                and child["turn_completed"] == row["turn_completed"],
                "Host event differs from the published ordinary turn",
            )
    require(summarize(rows) == data["summary"], "Published totals differ")
    for original in baseline:
        variants = [r for r in rows + matched["cases"] if r["id"] == original["id"]]
        require(
            len(variants) == 2
            and all(
                r["native_terrain"]["samples_before"]
                == original["native_terrain"]["samples_before"]
                for r in variants
            ),
            "Different starting cells in matched terrain controls",
        )
    return data["summary"]


def require_perfect(summary):
    for profile in ("core", "full"):
        group = summary[profile]
        require(
            group["scheduled"]
            == group["submitted"]
            == group["delivered"]
            == group["completed_turns"]
            == 71,
            f"{profile} did not submit, deliver and finish every original",
        )
        require(
            group["outcomes"] == {"pass": 71},
            f"{profile} did not pass all 71 ordinary first attempts",
        )
        require(
            not any(
                group[key]
                for key in (
                    "unsuccessful_public_replies",
                    "public_requests_without_reply",
                    "host_requests_without_terminal_event",
                    "failed_host_items",
                    "host_stderr_error_records",
                    "missing_verified_final",
                    "host_approval_denials",
                )
            ),
            f"{profile} had an unsuccessful call, host error, missing reply or missing final",
        )


def verify_raw_case(published, raw):
    record = raw | {"review": published["semantic_review"]}
    actual = outcomes.score(record)
    for key in (
        "outcome",
        "stage",
        "reason",
        "target_reached",
        "native_effect",
        "evidence_sha256",
        "answer_sha256",
        "review_sha256",
        "positive_receipts",
        "host_approval_receipts",
    ):
        require(actual[key] == published[key], f"{raw['id']} differs from native grading in {key}")
    require(
        [outcomes._digest(call) for call in raw["calls"]]
        == [call["receipt_sha256"] for call in published["calls"]],
        "Public calls differ from the original receipt",
    )
    require(
        [public_method(call["name"]) for call in raw["calls"]]
        == [c["method"] for c in published["calls"]],
        "Wire tool/resource methods differ",
    )
    require(
        published["state_sha256"]
        == (
            {"before": report.digest(raw["before"]), "after": report.digest(raw["after"])}
            if raw.get("before") and raw.get("after")
            else {}
        ),
        "Native snapshots differ",
    )
    native = outcomes._state_verdict(raw)
    require(
        published["native_assessment"]["result"] == (list(native) if native else None),
        "Native oracle verdict differs",
    )
    projections = report.public_case(record)
    for field in ("target_before", "target_after_changes"):
        require(
            published["native_assessment"][field + "_sha256"]
            == outcomes._digest(projections[field]),
            "Request-specific native observation differs",
        )


def verify_private(data, root):
    for label, revision, rows in (
        ("baseline-six", data["baseline"]["source_revision"], data["baseline"]["cases"]),
        ("final", data["candidate_product"], data["cases"]),
        (
            "matched-disabled",
            data["candidate_product"],
            data.get("matched_disabled", {}).get("cases", []),
        ),
    ):
        group = root / label / revision[:9]
        if len(rows) == (142 if label == "final" else 6):
            scheduled = {r["id"] for r in rows}
            archived = {
                folder.name for folder in group.glob("*/receipts-reviewed/*") if folder.is_dir()
            }
            require(
                archived == scheduled,
                "Private original case folders differ from the published schedule",
            )
        checked = set()
        for public in rows:
            initial = list(group.glob("*/receipts-reviewed/" + public["id"] + "/row.json"))
            require(len(initial) == 1, "Private original receipt is missing or duplicated")
            attempts = initial + sorted(
                group.glob("*/receipts-admitted-*/" + public["id"] + "/row.json")
            )
            for preserved in attempts:
                require(
                    [
                        folder.name
                        for folder in preserved.parent.glob("attempt-*")
                        if folder.is_dir()
                    ]
                    == ["attempt-1"],
                    "Unexpected hidden model or admission attempt folder",
                )
            path = attempts[-1]
            gates = attempts[:-1]
            final_input = json.loads(path.read_bytes())
            if (
                label != "baseline-six"
                and not public["prompt_submissions"]
                and (path.parent / "attempt-1/readiness.json").exists()
            ):
                gates = attempts
            require(
                len(gates) == len(public.get("admission_failures", [])),
                "Infrastructure admission records differ",
            )
            for gate, recorded in zip(gates, public.get("admission_failures", []), strict=True):
                first = json.loads(gate.read_bytes())
                require(
                    all(
                        first[key] == final_input[key]
                        for key in ("id", "query", "profile", "task", "language", "seed", "lane")
                    ),
                    "Admission used a different original, native lane or seed",
                )
                host_dir = gate.parent / "attempt-1"
                no_host = not any(
                    (host_dir / n).exists()
                    for n in (
                        "ordinary-launch.json",
                        "host.stderr",
                        "children.json",
                        "actor-events.jsonl",
                        "answer.txt",
                    )
                ) and not list(host_dir.glob("wire-*.jsonl"))
                require(
                    first.get("status") == "precondition"
                    and first.get("prompt_sha256") is None
                    and not first.get("turn_started")
                    and not first.get("delivered")
                    and not first["calls"]
                    and not first.get("host_calls")
                    and no_host
                    and recorded["prompt_submissions"] == recorded["public_actor_calls"] == 0,
                    "Another actor reached the original before the admitted turn",
                )
                require(
                    report.digest(first) == recorded["initial_row_sha256"]
                    and report.digest(json.loads((host_dir / "readiness.json").read_bytes()))
                    == recorded["readiness_sha256"]
                    and report.digest(first["before"]) == recorded["native_before_sha256"]
                    and report.digest(first["after"]) == recorded["native_after_sha256"],
                    "Original failed admission evidence differs",
                )
            fixture = path.parents[2]
            if fixture not in checked:
                checked.add(fixture)
                require(
                    json.loads((fixture / "actor-version.json").read_bytes()) == data["actor"],
                    "Unexpected private actor",
                )
                identities = sorted(fixture.glob("lane-*/matched-identity.json"))
                require(bool(identities), "Missing native fixture identity")
                for identity in identities:
                    observed = json.loads(identity.read_bytes())
                    require(observed["revision"] == revision, "Unexpected native revision")
                    for name, key in (
                        ("server-source.zip", "server_sha256"),
                        ("dependencies/mcp.zip", "product_archive_sha256"),
                    ):
                        archive = fixture / name
                        require(
                            hashlib.sha256(archive.read_bytes()).hexdigest() == observed[key],
                            "Pinned archive digest differs",
                        )
                        expected_revision = (
                            data["runtime_revision"]
                            if label != "baseline-six" and name == "server-source.zip"
                            else revision
                        )
                        with zipfile.ZipFile(archive) as files:
                            require(
                                files.comment == expected_revision.encode("ascii"),
                                "Editor/server archive is from a different commit",
                            )
                            if label != "baseline-six" and name == "dependencies/mcp.zip":
                                package = json.loads(files.read("MCPForUnity/package.json"))
                                require(
                                    package["mcpSkillRevision"] == data["skill_revision"]
                                    and "/archive/" + data["runtime_revision"] + ".zip#"
                                    in package["mcpServerSource"],
                                    "Distributed package references differ from loaded server or skill",
                                )
            raw = final_input
            require(
                re.fullmatch(r"lane-[0-3]", raw["lane"])
                and (fixture / raw["lane"] / "matched-identity.json").is_file(),
                "Original target has no source-pinned native identity",
            )
            attempt = path.parent / "attempt-1"
            calls, events = trial.read_wire(attempt)
            require(calls == raw["calls"], "Raw row differs from original wire")
            require(
                actor_inventories(events) == public["actor_tool_inventories"],
                "Actor tools/list observation differs from recorded wire",
            )
            failed = [
                (i, item.get("tool"), item.get("status"))
                for i, item in enumerate(raw.get("host_calls", []))
                if item.get("error") or item.get("status") == "failed"
            ]
            require(
                dict(
                    Counter(
                        str(c.get("server")) + "." + str(c.get("tool"))
                        for c in raw.get("host_calls", [])
                    )
                )
                == public["host_calls_by_binding"],
                "Recorded host bindings differ from raw host events",
            )
            require(
                failed
                == [(f["host_index"], f["tool"], f["status"]) for f in public["host_failures"]],
                "A host-reported failure differs from raw events",
            )
            verify_raw_case(public, raw)
            require(
                public["host_stderr"] == host_stderr_summary(attempt / "host.stderr"),
                "Host stderr evidence differs from the source log",
            )
            answer_file = attempt / "answer.txt"
            verified_final = bool(
                raw.get("turn_completed")
                and isinstance(raw.get("answer"), str)
                and raw["answer"].strip()
                and answer_file.is_file()
                and answer_file.read_text(encoding="utf-8") == raw["answer"]
            )
            require(
                public["final_answer_verified"] is verified_final, "Final differs from host output"
            )
            if label != "baseline-six" and public["prompt_submissions"]:
                require(
                    json.loads((attempt / "ordinary-launch.json").read_bytes()) == public["launch"],
                    "Private original command evidence differs",
                )
                tracked = json.loads((attempt / "children.json").read_bytes())
                require(
                    tracked["original_prompt_writes"] == 1
                    and len(tracked["children"]) == 1
                    and not tracked["markers"],
                    "Source actor had an additional prompt, child or managed handoff",
                )
                child = tracked["children"][0]
                for key in (
                    "turn_start_count",
                    "turn_completion_count",
                    "turn_started",
                    "turn_completed",
                    "host_items",
                ):
                    require(
                        child[key] == public["actor_session"][key],
                        "Host child accounting differs from private original",
                    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "result",
        nargs="?",
        type=Path,
        default=Path(__file__).with_name("agent_outcome_original_intermediate.json"),
    )
    parser.add_argument(
        "--private-root",
        type=Path,
        help="Recompute native grades, source pins and the actual host/wire receipts under the original private cohort root.",
    )
    parser.add_argument(
        "--require-perfect",
        action="store_true",
        help="Require both 71/71 outcome tables to pass without public or host errors, missing replies or missing finals.",
    )
    args = parser.parse_args()
    data = json.loads(args.result.read_bytes())
    summary = verify(data)
    if args.private_root:
        verify_private(data, args.private_root.resolve())
    print(json.dumps(summary, indent=2))
    if args.require_perfect:
        try:
            require_perfect(summary)
        except ValueError as error:
            parser.exit(1, f"{error}\n")


if __name__ == "__main__":
    main()
