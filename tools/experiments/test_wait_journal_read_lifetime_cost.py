#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
# How to run: use the repository's existing Server Python environment.
# python tools/experiments/test_wait_journal_read_lifetime_cost.py --help
"""Measure journal buffer lifetimes with final resource cleanup inside timing.

Reuses the previous experiment's synthetic payloads and complete disk lifecycle.
Imports, warmup, payload construction and initial admission are outside timing.
Use --check for untimed parity; obtain an exclusive CPU lease for other modes.
"""

from __future__ import annotations

import argparse
import gc
import json
from pathlib import Path
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
import tracemalloc
from typing import TypedDict

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.experiments import test_wait_journal_cost as legacy


class Observation(TypedDict, total=False):
    """One fresh-process observation, with separate optional measurement modes."""

    case: str
    mode: str
    label: str
    run: int
    round: int
    source: str
    source_sha256: str
    dependency_sha256: dict[str, str]
    imports: dict[str, str]
    python: str
    pydantic: str
    document_sha256: str
    lifecycle_wall_ns: int
    lifecycle_cpu_ns: int
    cleanup_wall_ns: int
    cleanup_cpu_ns: int
    wall_ns: int
    cpu_ns: int
    traced_current: int
    traced_peak: int
    traced_retained: int
    traced_cleanup_peak: int
    gc_collections: list[int]
    rss_before: dict[str, int]
    rss_after: dict[str, int]
    rss_released: dict[str, int]


def worker(args: argparse.Namespace) -> Observation:
    """Time actual journal work and release both payload and private storage."""
    module = legacy.load_journal(args.source, args.repository)
    import models.response_limits as limits  # pylint: disable=import-outside-toplevel
    import services.private_store_bootstrap as bootstrap  # pylint: disable=import-outside-toplevel
    import pydantic  # pylint: disable=import-outside-toplevel

    scratch = tempfile.TemporaryDirectory(prefix="journal-read-", dir=args.scratch)
    try:
        root = Path(scratch.name)
        legacy.scenario(module, root / "warm", "scalar-1k", legacy.payload_for("scalar-1k"))
        shutil.rmtree(root / "warm")
        payload = legacy.payload_for(args.case)
        assert limits.response_size(payload) is not None
        gc.collect()
        row: Observation = {
            "case": args.case,
            "mode": args.mode,
            "source": str(Path(module.__file__).resolve()),
            "source_sha256": legacy.digest(args.source),
            "dependency_sha256": {
                "limits": legacy.digest(Path(limits.__file__)),
                "bootstrap": legacy.digest(Path(bootstrap.__file__)),
                "payload_scenario_harness": legacy.digest(Path(legacy.__file__)),
                "measurement_harness": legacy.digest(Path(__file__)),
            },
            "imports": {"limits": limits.__file__, "bootstrap": bootstrap.__file__},
            "python": sys.version,
            "pydantic": pydantic.__version__,
        }
        before_gc = [entry["collections"] for entry in gc.get_stats()]
        if args.mode == "trace":
            tracemalloc.start()
        if args.mode == "rss":
            row["rss_before"] = legacy.rss_bytes()
        if args.mode == "latency":
            wall, cpu = time.perf_counter_ns(), time.process_time_ns()
        row["document_sha256"] = legacy.scenario(module, root / "measured", args.case, payload)
        if args.mode == "latency":
            lifecycle_wall, lifecycle_cpu = time.perf_counter_ns(), time.process_time_ns()
        if args.mode == "trace":
            row["traced_current"], row["traced_peak"] = tracemalloc.get_traced_memory()
        if args.mode == "rss":
            row["rss_after"] = legacy.rss_bytes()

        # These releases were outside timing in the previous experiment.
        del payload
        scratch.cleanup()
        gc.collect()

        if args.mode == "latency":
            end_wall, end_cpu = time.perf_counter_ns(), time.process_time_ns()
            row.update(
                lifecycle_wall_ns=lifecycle_wall - wall,
                lifecycle_cpu_ns=lifecycle_cpu - cpu,
                cleanup_wall_ns=end_wall - lifecycle_wall,
                cleanup_cpu_ns=end_cpu - lifecycle_cpu,
                wall_ns=end_wall - wall,
                cpu_ns=end_cpu - cpu,
            )
        if args.mode == "trace":
            row["traced_retained"], row["traced_cleanup_peak"] = tracemalloc.get_traced_memory()
            tracemalloc.stop()
        if args.mode == "rss":
            row["rss_released"] = legacy.rss_bytes()
        row["gc_collections"] = [
            after["collections"] - before for before, after in zip(before_gc, gc.get_stats())
        ]
        assert not root.exists(), "private measurement storage survived cleanup"
        return row
    finally:
        scratch.cleanup()


def summarize(rows: list[Observation], cases: list[str]) -> dict[str, dict]:
    """Keep absolute values and pair ratios, including zero-resolution CPU values."""
    summary = {}
    metrics = (
        "wall_ns",
        "cpu_ns",
        "lifecycle_wall_ns",
        "lifecycle_cpu_ns",
        "cleanup_wall_ns",
        "cleanup_cpu_ns",
        "traced_peak",
        "traced_retained",
        "traced_cleanup_peak",
    )
    for case in cases:
        summary[case] = {}
        for metric in metrics:
            entries = {
                label: [
                    row[metric]
                    for row in rows
                    if row["case"] == case and row["label"] == label and metric in row
                ]
                for label in ("baseline", "candidate")
            }
            if not entries["baseline"]:
                continue
            medians = {label: statistics.median(values) for label, values in entries.items()}
            pairs = []
            for baseline in rows:
                if (
                    baseline["case"] != case
                    or baseline["label"] != "baseline"
                    or metric not in baseline
                ):
                    continue
                candidate = next(
                    row
                    for row in rows
                    if row["case"] == case
                    and row["label"] == "candidate"
                    and row["mode"] == baseline["mode"]
                    and row["run"] == baseline["run"]
                    and row["round"] == baseline["round"]
                )
                pairs.append(candidate[metric] / baseline[metric] if baseline[metric] else None)
            summary[case][metric] = {**medians, "paired_candidate_over_baseline": pairs}
    return summary


def paired(args: argparse.Namespace) -> list[Observation]:
    """Alternate source order across three independent blocks without parallel work."""
    rows: list[Observation] = []
    modes = ["check"] if args.check else args.modes
    for run in range(args.runs):
        for mode in modes:
            for round_number in range(args.rounds if mode == "latency" else 1):
                for case_number, case in enumerate(args.cases):
                    labels = ("baseline", "candidate")
                    if (run + round_number + case_number) % 2:
                        labels = labels[::-1]
                    pair = {}
                    for label in labels:
                        source = args.baseline if label == "baseline" else args.candidate
                        command = [
                            sys.executable,
                            str(Path(__file__).resolve()),
                            "--worker",
                            "--source",
                            str(source),
                            "--repository",
                            str(args.repository),
                            "--scratch",
                            str(args.scratch),
                            "--case",
                            case,
                            "--mode",
                            mode,
                        ]
                        result = subprocess.run(command, check=True, capture_output=True, text=True)
                        row: Observation = json.loads(result.stdout)
                        row.update(label=label, run=run, round=round_number)
                        rows.append(row)
                        pair[label] = row
                        # Preserve every completed child even if a later guard fails.
                        args.output.write_text(
                            json.dumps({"rows": rows}, indent=2) + "\n", encoding="utf-8"
                        )
                        print(
                            f"{run + 1}/{args.runs} {mode} {round_number + 1} {case} {label}",
                            flush=True,
                        )
                    assert (
                        pair["baseline"]["document_sha256"] == pair["candidate"]["document_sha256"]
                    )
                    assert (
                        pair["baseline"]["dependency_sha256"]
                        == pair["candidate"]["dependency_sha256"]
                    )
    return rows


def main() -> None:
    """Run untimed guards or explicitly selected exclusive measurement passes."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", type=Path, default=legacy.ROOT)
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--candidate", type=Path)
    parser.add_argument("--scratch", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--rounds", type=int, default=3)
    parser.add_argument("--cases", nargs="+", choices=legacy.CASES, default=legacy.CASES)
    parser.add_argument(
        "--modes",
        nargs="+",
        choices=["latency", "trace", "rss"],
        default=["latency", "trace", "rss"],
    )
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--worker", action="store_true")
    parser.add_argument("--source", type=Path)
    parser.add_argument("--case", choices=legacy.CASES)
    parser.add_argument("--mode", choices=["check", "latency", "trace", "rss"])
    args = parser.parse_args()
    if not args.worker and (args.baseline is None or args.candidate is None or args.output is None):
        parser.error("paired runs require --baseline, --candidate and --output")
    if args.worker and (args.source is None or args.case is None or args.mode is None):
        parser.error("workers require --source, --case and --mode")
    if args.runs <= 0 or args.rounds <= 0:
        parser.error("runs and rounds must be positive")
    args.scratch.mkdir(parents=True, exist_ok=True)
    if args.worker:
        print(json.dumps(worker(args)))
    else:
        rows = paired(args)
        args.output.write_text(
            json.dumps({"rows": rows, "summary": summarize(rows, args.cases)}, indent=2) + "\n",
            encoding="utf-8",
        )


if __name__ == "__main__":
    main()
