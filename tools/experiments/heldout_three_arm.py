"""Matched official, official plus standalone reader, and fork study design."""

import hashlib
import json
from collections import Counter
from configparser import ConfigParser, Error as ConfigError
from email.parser import BytesParser
from importlib.metadata import PackageNotFoundError, distribution
from itertools import permutations
from pathlib import Path
from secrets import SystemRandom
from statistics import median
from uuid import uuid4
from zipfile import BadZipFile, ZipFile

from tools.experiments import paired_unity_workflows as paired

ARMS = ("official", "official-reader", "fork")


def reader_wheel_digest(path, *, check_installed=False):
    """Pin the actual standalone distribution and console entry point."""
    with path.open("rb") as stream:
        try:
            with ZipFile(stream) as wheel:
                metadata = [
                    name for name in wheel.namelist() if name.endswith(".dist-info/METADATA")
                ]
                if len(metadata) != 1:
                    raise ValueError("Expected one standalone reader wheel distribution")
                info = wheel.getinfo(metadata[0])
                entry = wheel.getinfo(metadata[0].rsplit("/", 1)[0] + "/entry_points.txt")
                if info.file_size > 1024 * 1024 or entry.file_size > 64 * 1024:
                    raise ValueError("Oversized reader wheel metadata")
                package = BytesParser().parsebytes(wheel.read(info))
                entrypoints = ConfigParser(interpolation=None)
                entrypoints.read_string(wheel.read(entry).decode())
                main = entrypoints.get("console_scripts", "unity-mcp-evidence", fallback="")
                module, separator, function = main.partition(":")
                if (
                    package["Name"] != "ykh09242-unity-mcp-evidence"
                    or not separator
                    or not function.isidentifier()
                    or module.replace(".", "/") + ".py" not in wheel.namelist()
                ):
                    raise ValueError("Expected the standalone unity-mcp-evidence wheel")
                if check_installed:
                    _check_installation(package["Name"], wheel, info.filename, entry.filename)
        except (BadZipFile, KeyError, ConfigError) as exc:
            raise ValueError("Invalid standalone reader wheel") from exc
        stream.seek(0)
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _check_installation(name, wheel, metadata, entrypoints):
    try:
        installed = distribution(name)
    except PackageNotFoundError as exc:
        raise ValueError("Standalone reader distribution absent in this interpreter") from exc
    records = {str(file).replace("\\", "/") for file in installed.files or ()}
    root = Path(installed.locate_file("")).resolve(strict=True)
    for member in wheel.infolist():
        if member.filename not in (metadata, entrypoints) and not member.filename.endswith(".py"):
            continue
        if member.filename not in records:
            raise ValueError("Installed reader inventory differs from the pinned wheel")
        path = (root / member.filename).resolve(strict=True)
        if not path.is_relative_to(root) or not path.is_file():
            raise ValueError("Installed reader module lies outside the installation")
        with path.open("rb") as source, wheel.open(member) as archived:
            if (
                hashlib.file_digest(source, "sha256").digest()
                != hashlib.file_digest(archived, "sha256").digest()
            ):
                raise ValueError("Installed reader bytes differ from the pinned wheel")


def probe_reader(wheel, trial_id, session_id):
    return {
        "trial_id": trial_id,
        "session_id": session_id,
        "wheel_sha256": reader_wheel_digest(wheel, check_installed=True),
    }


def _check_actor_inputs(plan, inputs):
    private = {row["observer_digest"] for row in plan["cases"]}
    private.update(value.lower() for value in plan["used_evidence"])
    if not private.isdisjoint(inputs.values()):
        raise ValueError("Actor inputs contain observer or tuning evidence")


def assign_operators(plan, rosters):
    """Randomize participants within panels and rotate their arm across new tasks."""
    strata = {}
    for case in plan["cases"]:
        strata.setdefault((case["project"], case["task"]), []).append(case)
    slots = len(next(iter(strata.values())))
    if any(len(cases) != slots for cases in strata.values()):
        raise ValueError("Unequal project/task blocks")
    participants = set()
    for phase in ("actor", "replay"):
        roster = rosters[phase]
        if not isinstance(roster, list) or not all(
            isinstance(p, str) and p.strip() for p in roster
        ):
            raise ValueError("Distinct pseudonymous operator rosters are required")
        if len(set(roster)) != len(roster) or not participants.isdisjoint(roster):
            raise ValueError("Actor and teammate operator rosters must be independent")
        participants.update(roster)
        panels, remainder = divmod(len(roster), slots * len(ARMS))
        if remainder or panels not in (1, 2, len(strata)):
            raise ValueError("Supply one, two or six complete operator panels per phase")
        randomized = list(roster)
        SystemRandom().shuffle(randomized)
        rotations = len(strata) // panels
        for index, cases in enumerate(strata.values()):
            panel, rotation = divmod(index, rotations)
            for slot, case in enumerate(cases):
                for run in case["runs"]:
                    column = (ARMS.index(run["arm"]) + rotation) % len(ARMS)
                    run[phase + "_operator"] = randomized[
                        (panel * len(ARMS) + column) * slots + slot
                    ]
    plan["operators"] = {phase: sorted(rosters[phase]) for phase in ("actor", "replay")}


def validate_operator_schedule(plan):
    exposures = {}
    participants = {}
    rosters = {phase: set(plan["operators"][phase]) for phase in ("actor", "replay")}
    if not rosters["actor"].isdisjoint(rosters["replay"]):
        raise ValueError("Actor and teammate operator rosters must be independent")
    for case in plan["cases"]:
        seen = participants.setdefault((case["project"], case["task"]), set())
        for run in case["runs"]:
            for phase, roster in rosters.items():
                operator = run[phase + "_operator"]
                if operator in seen or operator not in roster:
                    raise ValueError(
                        "An operator cannot repeat a project/task across arms or roles"
                    )
                seen.add(operator)
                exposures.setdefault(operator, Counter())[run["arm"]] += 1
    for operator in rosters["actor"] | rosters["replay"]:
        counts = [exposures.get(operator, {}).get(arm, 0) for arm in ARMS]
        if max(counts) == 0 or max(counts) - min(counts) > 1:
            raise ValueError("Operator assignments are not counterbalanced across arms")


def check_case_adjudicators(case, bundles):
    """An observer may adjudicate actor and replay only within one arm of this case."""
    assigned = {}
    for run in case["runs"]:
        for key in (run["id"], run["replay_id"]):
            if key in bundles:
                observer = bundles[key]["review"]["observer_id"]
                if assigned.setdefault(observer, run["arm"]) != run["arm"]:
                    raise ValueError("Adjudicator covers multiple arms of the same matched case")


def extend_plan(plan, spec, base):
    """Extend the native held-out plan before any dispatch, preserving its controls."""
    from tools.experiments.heldout_unity_workflows import catalog_digest, text

    wheel = reader_wheel_digest(base / spec["reader_wheel"])
    reader_tool = spec["reader_tool"]
    if not text(reader_tool):
        raise ValueError("Name the restricted host tool serving the reader in MCP-only mode")
    # Both official arms run exactly the same pinned CLI and Pipeline package.
    plan["schema"] = 2
    plan["reader"] = {"wheel_sha256": wheel, "mcp_tool": reader_tool}
    plan["products"]["official-reader"] = dict(plan["products"]["official"])
    for index, project in enumerate(spec["projects"]):
        for mode in paired.MODES:
            names = project["catalogs"][mode]
            official = json.loads((base / names["official"]).read_bytes())
            reader = json.loads((base / names["official-reader"]).read_bytes())
            # Recheck the original digest in case an input was replaced during preparation.
            if catalog_digest(official) != plan["catalogs"][index][mode]["official"]:
                raise ValueError("Official catalog changed during preparation")
            pinned = catalog_digest(reader)
            extra = {reader_tool} if mode == "mcp-only" else set()
            if reader_tool in official or set(reader) != set(official) | extra:
                raise ValueError("Reader arm must expose the same provider tools and grants")
            plan["catalogs"][index][mode]["official-reader"] = pinned

        inputs = {}
        for task in paired.TASKS:
            files = project["fixtures"][task]["raw_inputs"]
            if not isinstance(files, dict) or not files or not all(text(name) for name in files):
                raise ValueError("Freeze a nonempty inventory of common raw evidence")
            inputs[task] = {}
            for label, path in files.items():
                with (base / path).open("rb") as stream:
                    inputs[task][label] = hashlib.file_digest(stream, "sha256").hexdigest()
            _check_actor_inputs(plan, inputs[task])
        orders = []
        for case in (row for row in plan["cases"] if row["project"] == index):
            if not orders:
                orders = list(permutations(ARMS))
                SystemRandom().shuffle(orders)
            case["raw_inputs"] = inputs[case["task"]]
            case["runs"] = [
                {"arm": arm, "id": uuid4().hex, "replay_id": uuid4().hex} for arm in orders.pop()
            ]
    assign_operators(plan, spec["operators"])


def check_dispatch(plan, case, allocation, phase, evidence):
    """Bind the treatment and common packet, and reconstruct prompt-to-observation time."""
    host = evidence["host"]
    arm = allocation["arm"]
    if host["operator_id"] != allocation[phase + "_operator"]:
        raise ValueError("Dispatch operator differs from the counterbalanced allocation")
    if any(evidence["review"]["observer_id"] in roster for roster in plan["operators"].values()):
        raise ValueError("Adjudicator must be independent of study participants")
    expected_probe = (
        {key: host[key] for key in ("trial_id", "session_id")}
        | {"wheel_sha256": plan["reader"]["wheel_sha256"]}
        if arm == "official-reader"
        else None
    )
    if host["reader_probe"] != expected_probe or host["raw_inputs"] != case["raw_inputs"]:
        raise ValueError("Dispatched reader or raw evidence differs from the study")
    _check_actor_inputs(plan, host["raw_inputs"])
    times = {}
    for call in evidence["calls"]:
        times[call["dispatch"]] = call["dispatch_seconds"]
        if call["return"] is not None:
            times[call["return"]] = call["return_seconds"]
        elif call.get("return_seconds") is not None:
            raise ValueError("Pending dispatch cannot have a return timestamp")
    ordered = [times[index] for index in sorted(times)]
    if any(
        not paired.number(value) or value > host["elapsed_seconds"] for value in ordered
    ) or ordered != sorted(ordered):
        raise ValueError("Invalid broker event time since prompt delivery")
    return min(
        (times[row["return"]] for row in evidence["review"]["valid_observations"]), default=None
    )


def _summary(rows):
    actors = [row for row in rows if row["scores"] is not None]
    observed = [
        row for row in actors if all(s["first_seconds"] is not None for s in row["scores"].values())
    ]
    replayed = [row for row in actors if row["replays"] is not None]
    result = {
        "planned_triplets": len(rows),
        "matched_actors": len(actors),
        "unmatched_actors": len(rows) - len(actors),
        "matched_first_observations": len(observed),
        "matched_replays": len(replayed),
        "unmatched_replays": len(rows) - len(replayed),
        "arms": None,
    }
    if not actors:
        return result
    arm_results = {}
    result["arms"] = arm_results
    for arm in ARMS:
        scores = [row["scores"][arm] for row in actors]
        false_claims = sum(s["false_success"] for s in scores)
        successes = sum(s["passed"] for s in scores)
        replays = sum(row["replays"][arm] for row in replayed)
        first = [row["scores"][arm]["first_seconds"] for row in observed]
        deltas = [
            row["scores"][arm]["first_seconds"] - row["scores"]["official"]["first_seconds"]
            for row in observed
        ]
        arm_results[arm] = {
            "successes": successes,
            "success_rate": successes / len(actors),
            "false_successes": false_claims,
            "false_success_rate": false_claims / len(actors),
            "no_valid_observation": sum(s["first_seconds"] is None for s in scores),
            "first_seconds_median": median(first) if first else None,
            "paired_seconds_minus_official_median": median(deltas) if deltas else None,
            "teammate_successes": replays if replayed else None,
            "teammate_success_rate": replays / len(replayed) if replayed else None,
        }
    return result


def report(plan, samples, invalid):
    rows = []
    for case in plan["cases"]:
        scores, replays, reasons = {}, {}, {}
        for allocation in case["runs"]:
            arm, key, replay_key = allocation["arm"], allocation["id"], allocation["replay_id"]
            actor, replay = samples.get(key), samples.get(replay_key)
            if actor is None or key in invalid:
                reasons[arm + "/actor"] = "missing" if actor is None else "reused_evidence"
            else:
                scores[arm] = {
                    "passed": actor["passed"],
                    "false_success": actor["run"]["actor_claimed_success"] and not actor["passed"],
                    "first_seconds": actor["first_seconds"],
                    "calls_to_first_valid_observation": actor["first"],
                }
            if replay is None or replay_key in invalid:
                reasons[arm + "/replay"] = "missing" if replay is None else "reused_evidence"
            elif arm not in scores:
                reasons[arm + "/replay"] = "no_admitted_actor"
            elif replay["handoff"] != actor["handoff"]:
                reasons[arm + "/replay"] = "handoff_mismatch"
            elif replay["run"]["operator_id"] == actor["run"]["operator_id"]:
                reasons[arm + "/replay"] = "same_operator"
            else:
                replays[arm] = actor["has_handoff"] and replay["passed"]
        rows.append(
            {
                **{key: case[key] for key in ("project", "task", "language", "mode", "repetition")},
                "scores": scores if set(scores) == set(ARMS) else None,
                "replays": replays if set(replays) == set(ARMS) else None,
                "unscored_slots": reasons,
            }
        )
    return {
        "schema": 2,
        "complete": bool(rows) and all(row["replays"] is not None for row in rows),
        "overall": _summary(rows),
        "by_project": [
            _summary([row for row in rows if row["project"] == index])
            for index in range(len(plan["projects"]))
        ],
        "by_mode": {
            mode: _summary([row for row in rows if row["mode"] == mode]) for mode in paired.MODES
        },
        "triplets": rows,
    }
