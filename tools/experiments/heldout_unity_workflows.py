"""Prepare and score blinded, project-held-out native Unity workflow comparisons."""

import argparse
from collections import Counter
from hashlib import sha256
from itertools import product
import json
import os
from pathlib import Path
import re
import secrets
from statistics import median
import subprocess
import sys
from tempfile import NamedTemporaryFile
from uuid import uuid4

if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.experiments import paired_unity_workflows as paired
from tools.experiments import heldout_three_arm as three_arm

ROOT = Path(__file__).resolve().parents[2]
LANGUAGES = ("en", "ko")
PHASES = ("actor", "replay")
QUESTIONS = {
    "asset-move": {
        "en": "Move the specified asset, repair its runtime lookup, and verify the result in a Player.",
        "ko": "지정된 에셋을 이동하고 런타임 조회를 복구한 뒤 플레이어에서 결과를 확인해 주세요.",
    },
    "blocked-click": {
        "en": "Find why a pointer click misses the specified target, fix it, and verify the transition.",
        "ko": "포인터 클릭이 지정한 대상에 도달하지 않는 원인을 찾아 수정하고 상태 전환을 확인해 주세요.",
    },
    "interrupted-edit": {
        "en": "Create and register the requested asset. Recover from an interruption and verify the final state.",
        "ko": "요청한 에셋을 생성하고 등록해 주세요. 중단이 발생하면 복구한 뒤 최종 상태를 확인해 주세요.",
    },
}
HANDOFF = {
    "en": "Report whether the task succeeded and leave instructions for a teammate to repeat it from the initial state.",
    "ko": "성공 여부를 보고하고 팀원이 초기 상태에서 재현할 수 있도록 절차를 남겨 주세요.",
}
FACT_KEYS = {
    "asset-move": (
        "guid_before",
        "guid_after",
        "original_ready",
        "moved_old_key_ready",
        "repaired_key_ready",
        "witness_environment",
    ),
    "blocked-click": (
        "input_mode",
        "blocked_target_first",
        "repaired_target_first",
        "blocked_transitions",
        "repaired_transitions",
    ),
    "interrupted-edit": (
        "interruption_observed",
        "created_guid",
        "registered_guid",
        "assets",
        "entries",
        "creation_effects",
        "registration_effects",
    ),
}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def text(value):
    return isinstance(value, str) and bool(value.strip())


def checksum(content):
    return sha256(content).hexdigest()


def json_digest(value):
    return checksum(
        json.dumps(
            value, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(",", ":")
        ).encode()
    )


def git(directory, *args):
    return subprocess.check_output(
        ["git", "-C", str(directory), *args], text=True, encoding="utf-8"
    ).strip()


def project_snapshot(directory):
    """Bind both repository ancestry and the Unity project inside that repository."""
    directory = Path(directory).resolve(strict=True)
    prefix = git(directory, "rev-parse", "--show-prefix")
    require(
        git(directory, "rev-parse", "--is-shallow-repository") == "false",
        "Project history is shallow",
    )
    require(
        not git(
            directory,
            "status",
            "--porcelain",
            "--ignore-submodules=none",
            "--untracked-files=all",
            "--",
            ".",
        ),
        "Project must be clean",
    )
    version = git(directory, "show", "HEAD:" + prefix + "ProjectSettings/ProjectVersion.txt")
    match = re.search(r"^m_EditorVersion: (6000\.\S+)\s*$", version, re.M)
    require(match, "A committed Unity 6 project is required")
    guid_line = git(
        directory,
        "grep",
        "-h",
        "-E",
        "^[[:space:]]*productGUID: ",
        "HEAD",
        "--",
        "ProjectSettings/ProjectSettings.asset",
    )
    guid = re.fullmatch(r"\s*productGUID: ([0-9a-f]{32})\s*", guid_line)
    require(guid and guid[1] != "0" * 32, "Missing committed Unity product GUID")
    return {
        "revision": git(directory, "rev-parse", "HEAD"),
        "ancestry": git(directory, "rev-list", "--max-parents=0", "HEAD").splitlines(),
        "assets_tree": git(directory, "rev-parse", "HEAD:" + prefix + "Assets"),
        "product_guid": guid[1],
        "unity_version": match[1],
    }


def identity_keys(snapshot):
    require(isinstance(snapshot["ancestry"], list) and snapshot["ancestry"], "Missing Git ancestry")
    keys = [*snapshot["ancestry"], snapshot["assets_tree"]]
    require(
        all(
            isinstance(key, str) and re.fullmatch("[0-9a-f]{40}|[0-9a-f]{64}", key) for key in keys
        ),
        "Invalid Git identity",
    )
    require(re.fullmatch("[0-9a-f]{32}", snapshot["product_guid"]), "Invalid Unity product GUID")
    return {*keys, snapshot["product_guid"]}


def file_digest(path):
    content = path.read_bytes()
    require(content.strip(), f"Empty input: {path.name}")
    return checksum(content)


def fixture_digest(path, project, task):
    raw = path.read_bytes()
    state = json.loads(raw)
    require(
        state["project_guid"] == project["product_guid"]
        and state["project_revision"] == project["revision"]
        and state["unity_version"] == project["unity_version"]
        and state["task"] == task
        and isinstance(state["facts"], dict)
        and state["facts"],
        "Frozen native state does not identify this project, revision and task",
    )
    return checksum(raw)


def catalog_digest(names):
    require(
        isinstance(names, list)
        and names
        and all(text(name) for name in names)
        and len(set(names)) == len(names),
        "Missing or duplicate tool names in provider catalog",
    )
    return json_digest(sorted(names))


def prepare(spec, base):
    design = spec.get("design", "two-arm")
    require(design in {"two-arm", "three-arm"}, "Unknown held-out design")
    training = spec["training"]
    require(training["inventory_complete"] is True, "A complete tuning inventory is required")
    excluded = set(git(ROOT, "rev-list", "--max-parents=0", "HEAD").splitlines())
    require(
        git(ROOT, "rev-parse", "--is-shallow-repository") == "false",
        "Study repository history is shallow",
    )
    for name in ("UnityMCPTests", "AssetStoreUploads"):
        excluded.add(git(ROOT, "rev-parse", f"HEAD:TestProjects/{name}/Assets"))
    for prior in training["projects"]:
        excluded.update(identity_keys(prior))
    training_keys = sorted(excluded)
    spent = training["used_evidence"]
    require(
        isinstance(spent, list) and all(text(item) for item in spent),
        "Invalid prior evidence inventory",
    )
    controls = {key: spec[key] for key in ("host", "model", "effort")}
    require(all(text(value) for value in controls.values()), "Incomplete host controls")
    require(set(spec["permissions"]) == set(paired.MODES), "Freeze permissions for both modes")
    permissions = {mode: file_digest(base / path) for mode, path in spec["permissions"].items()}
    products = {
        arm: {
            key: spec["products"][arm][key]
            for key in (
                ("product_identity", "unity_cli", "pipeline_package")
                if arm == "official"
                else ("product_identity",)
            )
        }
        for arm in paired.ARMS
    }
    for arm in paired.ARMS:
        require(text(products[arm]["product_identity"]), "Missing product identity")
    require(
        products["fork"]["product_identity"] != products["official"]["product_identity"],
        "Shared product identity",
    )
    require(
        all(text(products["official"][key]) for key in ("unity_cli", "pipeline_package")),
        "Missing official product pins",
    )
    require(len(spec["projects"]) == 2, "Two independent held-out projects are required")
    repeats = spec.get("repetitions", 1)
    require(paired.count(repeats) and repeats > 0, "Invalid repetition count")
    cases = []
    offset = secrets.randbelow(2)
    snapshots = []
    catalogs = []
    for index, entry in enumerate(spec["projects"]):
        require(entry["excluded_from_tuning"] is True, "Custodian holdout declaration is required")
        snapshot = project_snapshot(base / entry["path"])
        keys = identity_keys(snapshot)
        require(
            keys.isdisjoint(excluded), "Project overlaps the training inventory or another holdout"
        )
        excluded.update(keys)
        snapshots.append(snapshot)
        catalogs.append(
            {
                mode: {
                    arm: catalog_digest(
                        json.loads((base / entry["catalogs"][mode][arm]).read_bytes())
                    )
                    for arm in paired.ARMS
                }
                for mode in paired.MODES
            }
        )
        fixtures = {
            task: {
                "fixture_digest": fixture_digest(
                    base / entry["fixtures"][task]["initial_state"], snapshot, task
                ),
                "observer_digest": file_digest(base / entry["fixtures"][task]["observer"]),
            }
            for task in paired.TASKS
        }
        for task, language, mode, repetition in product(
            paired.TASKS, LANGUAGES, paired.MODES, range(1, repeats + 1)
        ):
            fixture = entry["fixtures"][task]
            context = fixture["context"][language]
            require(text(context), "Missing localized project context")
            prompt = "\n\n".join((QUESTIONS[task][language], context, HANDOFF[language]))
            order = (
                index
                + paired.TASKS.index(task)
                + LANGUAGES.index(language)
                + paired.MODES.index(mode)
                + repetition
                + offset
            )
            arms = paired.ARMS if order % 2 == 0 else paired.ARMS[::-1]
            cases.append(
                {
                    "project": index,
                    "task": task,
                    "language": language,
                    "mode": mode,
                    "repetition": repetition,
                    **fixtures[task],
                    "prompt": prompt,
                    "runs": [
                        {"arm": arm, "id": uuid4().hex, "replay_id": uuid4().hex} for arm in arms
                    ],
                }
            )
    require(snapshots[0]["unity_version"] == snapshots[1]["unity_version"], "Unity versions differ")
    plan = {
        "schema": 1,
        "controls": controls,
        "products": products,
        "permissions": permissions,
        "projects": snapshots,
        "catalogs": catalogs,
        "excluded_project_identities": training_keys,
        "used_evidence": spent,
        "cases": cases,
    }
    if design == "three-arm":
        three_arm.extend_plan(plan, spec, base)
    return plan


def trace_metrics(trace, witness):
    """Count all attempts dispatched by the first independently accepted response."""
    require(
        isinstance(trace, list) and witness["trace_digest"] == json_digest(trace),
        "Witness/trace mismatch",
    )
    valid = witness["valid_observations"]
    require(isinstance(valid, list), "Observation adjudication missing")
    returned = {}
    events = set()
    kinds = Counter()
    for call in trace:
        start, end = call["dispatch"], call["return"]
        require(
            paired.count(start) and start not in events and call["kind"] in ("mcp", "shell"),
            "Invalid dispatch",
        )
        events.add(start)
        kinds[call["kind"]] += 1
        if end is None:
            require("reply" not in call, "Outstanding call has a reply")
            continue
        require(paired.count(end) and end > start and end not in events, "Invalid return order")
        events.add(end)
        returned[end] = json_digest(call["reply"])
    require(events == set(range(len(events))), "Incomplete trace event sequence")
    for mark in valid:
        require(
            paired.count(mark["return"])
            and paired.digest(mark["reply_digest"])
            and returned.get(mark["return"]) == mark["reply_digest"],
            "Invalid observation reference",
        )
    first = min((mark["return"] for mark in valid), default=None)
    return (
        kinds["mcp"],
        kinds["shell"],
        None if first is None else sum(call["dispatch"] < first for call in trace),
    )


def assess(plan, case, allocation, phase, evidence):
    trial_id = allocation["id" if phase == "actor" else "replay_id"]
    arm = allocation["arm"]
    project = plan["projects"][case["project"]]
    fixed = {
        **plan["controls"],
        **plan["products"][arm],
        "trial_id": trial_id,
        "project_revision": project["revision"],
        "product_guid": project["product_guid"],
        "unity_version": project["unity_version"],
        "fixture_digest": case["fixture_digest"],
        "permission_digest": plan["permissions"][case["mode"]],
        "catalog_digest": plan["catalogs"][case["project"]][case["mode"]][arm],
        "observer_digest": case["observer_digest"],
        "prompt_digest": checksum(case["prompt"].encode()),
    }
    host, review = evidence["host"], evidence["review"]
    require(
        catalog_digest(evidence["catalog"]) == fixed["catalog_digest"],
        "Exposed provider catalog differs from the frozen tool selection",
    )
    require(
        all(host.get(key) == value for key, value in fixed.items()),
        "Run disagrees with frozen controls",
    )
    require(
        all(text(host.get(key)) for key in ("operator_id", "session_id", "fixture_run_id")),
        "Missing fresh session identity",
    )
    require(
        host["execution"] == "native" and host["coverage"] == "complete",
        "Native host trace unavailable",
    )
    capture = evidence["native_capture"]
    require(
        text(capture) and checksum(capture.encode()) == review["artifact_digest"],
        "Missing or mismatched native capture",
    )
    witness = json.loads(capture)
    require(
        all(
            witness.get(key) == host[key]
            for key in (
                "trial_id",
                "fixture_run_id",
                "product_guid",
                "project_revision",
                "unity_version",
                "product_identity",
                "fixture_digest",
                "observer_digest",
            )
        ),
        "Native observer binding mismatch",
    )
    require(
        witness["coverage"] == review["coverage"] == "complete"
        and text(review["observer_id"])
        and review["observer_id"] != host["operator_id"],
        "Independent native observer required",
    )
    require(
        text(witness["capture_id"]) and set(FACT_KEYS[case["task"]]) <= witness["facts"].keys(),
        "Missing native outcome facts",
    )
    if case["task"] == "asset-move":
        require(
            witness["facts"]["witness_environment"] == "player",
            "Missing independent Player coverage",
        )
    require(review["trial_id"] == trial_id, "Observation review belongs to another trial")
    witness["artifact_digest"] = review["artifact_digest"]
    mcp, shell, first = trace_metrics(evidence["calls"], review)
    require(isinstance(evidence["handoff"], str), "Missing handoff record")
    handoff = checksum(evidence["handoff"].encode())
    require(host["handoff_digest"] == handoff, "Handoff disagrees with host record")
    if arm in ("official", "official-reader"):
        require(
            host["provider_command"] == "unity mcp"
            and host["connected_pipeline"] == fixed["pipeline_package"],
            "Official Unity MCP/Pipeline execution unavailable",
        )
        require(
            witness["unity_cli"] == fixed["unity_cli"]
            and witness["pipeline_package"] == fixed["pipeline_package"]
            and witness["provider_session_id"] == host["session_id"],
            "Missing native official provider binding",
        )
    run = {
        **host,
        "status": "completed",
        "independent": witness,
        "mcp_calls": mcp,
        "shell_calls": shell,
        "duplicate_writes": witness["duplicate_writes"],
    }
    provider = "official" if arm == "official-reader" else arm
    score, reason = paired.run_score(case["task"], case["mode"], provider, run)
    require(score is not None, f"Unscorable native receipt: {reason}")
    sample = {
        "run": run,
        "passed": score["independent_pass"],
        "first": first,
        "handoff": handoff,
        "has_handoff": bool(evidence["handoff"].strip()),
        "uses": [
            host["session_id"],
            host["fixture_run_id"],
            witness["capture_id"],
            witness["artifact_digest"],
        ],
    }
    if plan.get("schema") == 2:
        sample["first_seconds"] = three_arm.check_dispatch(plan, case, allocation, phase, evidence)
    return sample


def allocations(plan):
    for case in plan["cases"]:
        for allocation in case["runs"]:
            for phase in PHASES:
                yield allocation["id" if phase == "actor" else "replay_id"], case, allocation, phase


def report(plan, admitted):
    if plan.get("schema") == 2:
        three_arm.validate_operator_schedule(plan)
        for case in plan["cases"]:
            three_arm.check_case_adjudicators(case, admitted)
    measurements = {}
    uses = Counter(plan["used_evidence"])
    for key, case, allocation, phase in allocations(plan):
        if key in admitted:
            sample = assess(plan, case, allocation, phase, admitted[key])
            measurements[key] = sample
            uses.update(sample["uses"])
    require(measurements.keys() == admitted.keys(), "Unknown admitted trial")
    invalid = {
        key
        for key, sample in measurements.items()
        if any(uses[item] > 1 for item in sample["uses"])
    }
    if plan.get("schema") == 2:
        return three_arm.report(plan, measurements, invalid)
    pairs = []
    for serial, case in enumerate(plan["cases"]):
        pair = {"task": case["task"], "mode": case["mode"], "repetition": serial}
        for allocation in case["runs"]:
            key = allocation["id"]
            if key in invalid:
                pair[allocation["arm"]] = {"status": "unavailable", "reason": "reused_evidence"}
            elif key in measurements:
                pair[allocation["arm"]] = measurements[key]["run"]
        pairs.append(pair)
    scored = paired.compare(pairs)
    for case, pair in zip(plan["cases"], scored["pairs"], strict=True):
        pair.update({key: case[key] for key in ("project", "language", "repetition")})
        if pair["scores"] is None:
            continue
        replay_scores = {}
        replay_reasons = {}
        for allocation in case["runs"]:
            arm, key = allocation["arm"], allocation["id"]
            actor = measurements[key]
            pair["scores"][arm]["calls_to_first_valid_observation"] = actor["first"]
            replay_id = allocation["replay_id"]
            replay = measurements.get(replay_id)
            if replay is None:
                replay_reasons[arm] = "missing"
            elif replay_id in invalid:
                replay_reasons[arm] = "reused_evidence"
            elif replay["handoff"] != actor["handoff"]:
                replay_reasons[arm] = "handoff_mismatch"
            elif replay["run"]["operator_id"] == actor["run"]["operator_id"]:
                replay_reasons[arm] = "same_operator"
            else:
                replay_scores[arm] = actor["has_handoff"] and replay["passed"]
        pair["unscored_replay_reasons"] = replay_reasons
        for arm in paired.ARMS:
            pair["scores"][arm]["teammate_replay_success"] = (
                replay_scores[arm] if len(replay_scores) == 2 else None
            )
    for mode, summary in scored["summary"].items():
        for arm, totals in (summary["arms"] or {}).items():
            runs = [
                pair["scores"][arm]
                for pair in scored["pairs"]
                if pair["mode"] == mode and pair["scores"]
            ]
            first = [
                row["calls_to_first_valid_observation"]
                for row in runs
                if row["calls_to_first_valid_observation"] is not None
            ]
            replay = [
                row["teammate_replay_success"]
                for row in runs
                if row["teammate_replay_success"] is not None
            ]
            totals.update(
                success_rate=totals["successes"] / len(runs),
                false_pass_rate=totals["false_passes"] / len(runs),
                first_valid_observation_samples=len(first),
                no_valid_observation=len(runs) - len(first),
                calls_to_first_valid_observation_median=median(first) if first else None,
                teammate_replay_samples=len(replay),
                teammate_replay_successes=sum(replay),
                teammate_replay_success_rate=sum(replay) / len(replay) if replay else None,
            )
    coverage = []
    for index in range(len(plan["projects"])):
        rows = [row for row in scored["pairs"] if row["project"] == index]
        actors = [row for row in rows if row["scores"] is not None]
        coverage.append(
            {
                "project": index,
                "planned_pairs": len(rows),
                "matched_actor_pairs": len(actors),
                "matched_replay_pairs": sum(not row["unscored_replay_reasons"] for row in actors),
            }
        )
    scored["project_coverage"] = coverage
    scored["complete"] = all(
        row["planned_pairs"] == row["matched_actor_pairs"] == row["matched_replay_pairs"]
        for row in coverage
    )
    return scored


def write_json(path, value):
    # Publish only a complete document. Link creation is exclusive on both Windows and POSIX.
    with NamedTemporaryFile(dir=path.parent, delete=False) as stream:
        pending = Path(stream.name)
    try:
        pending.write_bytes(
            (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode()
        )
        os.link(pending, path)
    finally:
        pending.unlink()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    identify = commands.add_parser("identify")
    identify.add_argument("project", type=Path)
    probe = commands.add_parser(
        "probe-reader", help="Verify the reader interpreter before dispatch"
    )
    probe.add_argument("wheel", type=Path)
    probe.add_argument("trial_id")
    probe.add_argument("session_id")
    create = commands.add_parser("prepare")
    create.add_argument("spec", type=Path)
    for name in ("prepare", "admit", "score"):
        sub = create if name == "prepare" else commands.add_parser(name)
        sub.add_argument("study", type=Path)
        if name != "prepare":
            sub.add_argument("--pin", required=True, help="Manifest SHA-256 printed at preparation")
        if name == "admit":
            sub.add_argument("id")
            sub.add_argument("bundle", type=Path)
        if name == "score":
            sub.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == "identify":
            print(json.dumps(project_snapshot(args.project), indent=2))
            return
        if args.command == "probe-reader":
            print(json.dumps(three_arm.probe_reader(args.wheel, args.trial_id, args.session_id)))
            return
        manifest = args.study / "manifest.json"
        receipts = args.study / "admitted"
        if args.command == "prepare":
            plan = prepare(json.loads(args.spec.read_bytes()), args.spec.resolve().parent)
            args.study.mkdir()
            receipts.mkdir()
            write_json(manifest, plan)
            print(checksum(manifest.read_bytes()))
            return
        raw = manifest.read_bytes()
        require(checksum(raw) == args.pin, "Manifest pin mismatch")
        plan = json.loads(raw)
        if args.command == "score":
            admitted = {
                path.stem: json.loads(path.read_bytes()) for path in receipts.glob("*.json")
            }
            result = report(plan, admitted)
            result["manifest_sha256"] = args.pin
            write_json(args.output, result)
            return
        if plan.get("schema") == 2:
            three_arm.validate_operator_schedule(plan)
        slots = {
            key: (case, allocation, phase) for key, case, allocation, phase in allocations(plan)
        }
        require(args.id in slots, "Unknown trial ID")
        evidence = json.loads(args.bundle.read_bytes())
        case, allocation, phase = slots[args.id]
        sample = assess(plan, case, allocation, phase, evidence)
        if plan.get("schema") == 2:
            siblings = {}
            for run in case["runs"]:
                for key in (run["id"], run["replay_id"]):
                    path = receipts / (key + ".json")
                    if key != args.id and path.exists():
                        siblings[key] = json.loads(path.read_bytes())
            siblings[args.id] = evidence
            three_arm.check_case_adjudicators(case, siblings)
        require(
            set(sample["uses"]).isdisjoint(plan["used_evidence"]),
            "Training evidence cannot be reused",
        )
        if phase == "replay":
            actor = assess(
                plan,
                case,
                allocation,
                "actor",
                json.loads((receipts / (allocation["id"] + ".json")).read_bytes()),
            )
            require(
                actor["handoff"] == sample["handoff"]
                and actor["run"]["operator_id"] != sample["run"]["operator_id"],
                "Replay requires the original handoff and a different teammate",
            )
        write_json(receipts / (args.id + ".json"), evidence)
    except (
        OSError,
        subprocess.CalledProcessError,
        ValueError,
        KeyError,
        TypeError,
        AttributeError,
    ) as error:
        parser.exit(2, f"{parser.prog}: {error}\n")


if __name__ == "__main__":
    main()
