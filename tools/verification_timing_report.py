"""Compare exported verification trials with the same inputs and declared starting conditions."""

import argparse
from collections import Counter, defaultdict
from hashlib import sha256
import json
from math import isfinite
from pathlib import Path
import re

if __package__:
    from .phase_timing_report import NATIVE, SERVER, normalized, percentile, records
else:
    from phase_timing_report import NATIVE, SERVER, normalized, percentile, records

TOKEN = re.compile(r"[0-9a-f]{32}")
COMMAND_PHASES = (*SERVER, "scheduler_queue")
RUN_PHASES = ("scheduler_queue", "task_dispatch", "orchestration", "child_poll_sleep")
PLAYER_PHASES = (
    "process_spawn",
    "launch_to_first_observed_progress",
    "process_evidence_write",
    "final_report_read",
)


def trial(report_path):
    with Path(report_path).open("rb") as stream:
        raw = stream.read(2 * 1024 * 1024 + 1)
    if len(raw) > 2 * 1024 * 1024:
        raise ValueError("verification report exceeds the input limit")
    report = json.loads(raw)
    report = report.get("data", report)
    if report.get("schema") != "verification_report@1" or not TOKEN.fullmatch(
        str(report.get("run_id", ""))
    ):
        raise ValueError("expected an exported verification report with its run ID")
    timing = report.get("timing") or {}
    if timing.get("schema") != "verification_timing@1" or report.get("state") == "running":
        raise ValueError("expected a terminal report with phase diagnostics enabled")
    return report


def fingerprint(report):
    # Generation, process ID and run ID change across cold starts. Keep the
    # observed source, selections, profile, package and project identity instead.
    installation = report.get("installation") or {}
    comparison = report.get("comparison") or {}
    freshness = next(
        (s["evidence"] for s in report.get("stages", []) if s["name"] == "freshness"), {}
    )
    source = (
        comparison.get("manifest_sha256")
        or freshness.get("source_digest")
        or report.get("source_token")
    )
    if not source or not report.get("project_scope"):
        raise ValueError("trial lacks a comparable source or project identity")
    package = installation.get("package")
    server = installation.get("server") or {}
    controls = [
        report["project_scope"],
        source,
        comparison.get("baseline_manifest_sha256"),
        report.get("baseline_id"),
        report.get("profile_id"),
        report.get("profile"),
        report.get("profile_hash"),
        report.get("selection"),
        installation.get("unity_version"),
        package,
        server.get("version"),
        server.get("source_commit"),
        report["timing"]["entry"],
        report.get("state"),
        report.get("native_settled"),
    ]
    return sha256(json.dumps(controls, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def distribution(values):
    values = sorted(values)
    return {
        "samples": len(values),
        "median": percentile(values, 0.5) if values else None,
        "p95": percentile(values, 0.95) if values else None,
    }


def observe(metrics, name, value):
    values = metrics[name]
    if value is not None:
        if type(value) not in (int, float) or not isfinite(value) or value < 0:
            raise ValueError("invalid local duration")
        values.append(value)


def summarize_trials(trials, editor_logs, *, operations=False):
    groups = defaultdict(list)
    runs, tokens = set(), Counter()
    for condition, setup, path in trials:
        if condition not in {"cold", "warm"} or not setup.strip():
            raise ValueError("each trial requires cold or warm and a controlled setup label")
        report = trial(path)
        if report["run_id"] in runs:
            raise ValueError("duplicate run ID across trials")
        runs.add(report["run_id"])
        groups[condition, setup, fingerprint(report)].append(report)
        for command in report["timing"]["commands"]:
            tokens[command["correlation"]] += 1
    native = {}
    for path in editor_logs:
        for row in records(Path(path)):
            parsed = normalized(row)
            if parsed is not None and parsed[1] == "native" and parsed[0] in tokens:
                token, _, phases = parsed
                native[token] = None if token in native else phases
    result = []
    for (condition, setup, identity), reports in sorted(groups.items()):
        metrics = defaultdict(list)
        samples = []
        matched = missing = ambiguous = omitted = 0
        for report in reports:
            timing = report["timing"]
            commands = []
            omitted += timing["omitted_commands"]
            for phase in RUN_PHASES:
                observe(metrics, "server." + phase, timing["phases_ms"].get(phase))
            observe(metrics, "client.report_roundtrip", timing.get("client_report_roundtrip_ms"))
            for phase in PLAYER_PHASES:
                name = "player." + phase
                metrics.setdefault(name, [])
                for phases in timing["players_ms"].values():
                    observe(metrics, name, phases.get(phase))
            for command in timing["commands"]:
                token = command["correlation"]
                joined = None
                if tokens[token] != 1 or token in native and native[token] is None:
                    ambiguous += 1
                elif command["native_requested"] and token in native:
                    joined = native[token]
                    matched += 1
                else:
                    missing += 1
                if operations:
                    commands.append(
                        {
                            "command": command["command"],
                            "correlation": token,
                            "server_ms": {
                                name: command[name + "_ms"]
                                for name in COMMAND_PHASES
                                if name + "_ms" in command
                            },
                            "editor_ms": {
                                name: joined[name + "_ms"]
                                for name in NATIVE
                                if name + "_ms" in joined
                            }
                            if joined is not None
                            else None,
                        }
                    )
                for side, names, phases in (
                    ("server", COMMAND_PHASES, command),
                    ("editor", NATIVE, joined or {}),
                ):
                    for name in names:
                        observe(
                            metrics, f"{command['command']}.{side}.{name}", phases.get(name + "_ms")
                        )
            if operations:
                samples.append(
                    {
                        "run_id": report["run_id"],
                        "phases_ms": timing["phases_ms"],
                        "players_ms": timing["players_ms"],
                        "commands": commands,
                    }
                )
        installation = reports[0].get("installation") or {}
        server = installation.get("server") or {}
        result.append(
            {
                "condition": condition,
                "setup": setup,
                "input_fingerprint": identity,
                "profile_id": reports[0].get("profile_id"),
                "unity_version": installation.get("unity_version"),
                "server_version": server.get("version"),
                "server_source_commit": server.get("source_commit"),
                "state": reports[0]["state"],
                "trials": len(reports),
                "matched_commands": matched,
                "commands_without_native_record": missing,
                "ambiguous_commands": ambiguous,
                "omitted_commands": omitted,
                "phase_ms": {
                    name: distribution(values) for name, values in sorted(metrics.items())
                },
                **({"operations": samples} if operations else {}),
            }
        )
    return {
        "groups": result,
        "clock_offset_ms": None,
        "one_way_client_delivery_ms": None,
        "interpretation": "Server phases sample runs, Player phases sample launches and named commands sample invocations. Each process uses its own monotonic clock. Windows can overlap. Missing phases have zero samples. Cold/warm and the setup label are operator observations.",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--trial",
        action="append",
        nargs=3,
        required=True,
        metavar=("COLD_OR_WARM", "SETUP", "REPORT"),
    )
    parser.add_argument("--editor-log", action="append", default=[], type=Path)
    parser.add_argument(
        "--operations",
        action="store_true",
        help="Include individual run, command and Player job IDs and durations.",
    )
    args = parser.parse_args()
    try:
        print(
            json.dumps(
                summarize_trials(args.trial, args.editor_log, operations=args.operations), indent=2
            )
        )
    except (
        OSError,
        ValueError,
        KeyError,
        TypeError,
        AttributeError,
        RecursionError,
        OverflowError,
    ) as error:
        parser.exit(2, f"Cannot compare verification trials: {error}\n")


if __name__ == "__main__":
    main()
