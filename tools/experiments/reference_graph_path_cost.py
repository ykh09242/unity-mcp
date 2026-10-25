"""Compare frozen query sources using isolated synthetic SQLite graphs.

Run with the existing Server Python environment; no optional dependencies.
Verification never records timings. --measure additionally requires the caller's
exclusive CPU lease. Output is append-only JSONL with raw per-page CPU samples.
"""

from __future__ import annotations

import argparse
import ctypes
from dataclasses import asdict, dataclass
import gc
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
import tracemalloc

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "Server" / "src"))

from services.reference_graph.store import GraphScope, GraphStore  # noqa: E402


SCOPE = GraphScope("synthetic", "path-cost", "isolated")


@dataclass(frozen=True, slots=True)
class Case:
    shape: str
    nodes: int
    limit: int
    provenance_bytes: int = 48
    direction: str = "outgoing"


def cases(quick: bool) -> list[Case]:
    selected = [
        Case(shape, 1000, limit, direction=direction)
        for shape in ("wide-miss", "wide-late", "chain", "routes")
        for limit in (1, 100, 1000)
        for direction in ("incoming", "outgoing")
    ]
    selected += [Case(shape, 32, 1) for shape in ("corrupt-payload", "corrupt-provenance")]
    if not quick:
        selected += [
            Case(shape, nodes, limit, size)
            for nodes in (50000, 250000)
            for shape in ("wide-miss", "wide-late")
            for limit in (100, 1000)
            for size in (48, 8192)
        ]
        selected += [Case("chain", 50000, limit) for limit in (100, 1000)]
        selected += [Case("wide-late", 50000, 1000, 1024, "incoming")]
    return selected


def load_queries(source: Path):
    spec = importlib.util.spec_from_file_location("frozen_path_queries", source)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load query source: {source}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    # Identical opaque query ids make every public field, including cursors,
    # directly comparable. Each run uses its own database and only one query.
    module.uuid = SimpleNamespace(uuid4=lambda: SimpleNamespace(hex="0" * 32))
    return module.GraphQueries


def build_fixture(root: Path, case: Case, source: Path) -> Path:
    store = GraphStore.open(root, SCOPE)
    load_queries(source)(store)
    provenance = json.dumps({"path": "Assets/Synthetic.cs", "padding": "x" * case.provenance_bytes})

    def relationships():
        for index in range(1, case.nodes):
            owner = f"n{index - 1:06}" if case.shape == "chain" else "n000000"
            target = f"n{index:06}"
            if case.shape == "routes":
                # Equal shortest routes, a cycle, and parallel evidence.
                owner = "n000000" if index < case.nodes // 2 else f"n{index % 2 + 1:06}"
                target = f"n{index:06}" if index < case.nodes // 2 else "destination"
            if case.direction == "incoming":
                owner, target = target, owner
            payload = json.dumps({"target": target, "kind": "synthetic", "evidence": "fixture"})
            yield (f"e{index:09}", owner, "code", target, "synthetic", payload)
        if case.shape == "routes":
            yield ("e999999999", "n000001", "code", "n000000", "synthetic", "{}")

    with store._connect(write=True) as db:
        db.executemany(
            "INSERT INTO nodes VALUES (?,?,'asset',1)",
            ((f"n{i:06}", f"Assets/Node{i}.cs") for i in range(case.nodes)),
        )
        db.executemany("INSERT INTO edges VALUES (?,?,?,?,?,?)", relationships())
        # Wide outgoing scans repeat the same provenance in every joined row,
        # giving 8 KiB retention pressure without a multi-gigabyte fixture.
        db.execute("INSERT INTO observations VALUES ('n000000','code','fixture',1,'ready',NULL)")
        db.execute("INSERT INTO evidence_context VALUES ('n000000','code',1,'{}',?)", (provenance,))
        if case.shape == "corrupt-payload":
            db.execute("UPDATE edges SET payload='{' WHERE id='e000000002'")
        if case.shape == "corrupt-provenance":
            db.execute("INSERT INTO evidence_context VALUES ('n000001','code',1,'{}','{')")
            db.execute("UPDATE edges SET owner='n000001' WHERE id='e000000002'")
    return store.path


def arguments_for(case: Case) -> tuple[str, str]:
    target = f"n{case.nodes - 1:06}"
    if case.shape.startswith("wide-miss") or case.shape.startswith("corrupt"):
        target = "missing"
    if case.shape == "routes":
        target = "destination"
    return "n000000", target


def process_memory() -> dict[str, int]:
    if sys.platform == "win32":

        class Counters(ctypes.Structure):
            _fields_ = [
                ("cb", ctypes.c_ulong),
                ("faults", ctypes.c_ulong),
                *[
                    (name, ctypes.c_size_t)
                    for name in (
                        "peak_working",
                        "working",
                        "peak_paged",
                        "paged",
                        "peak_nonpaged",
                        "nonpaged",
                        "pagefile",
                        "peak_pagefile",
                        "private",
                    )
                ],
            ]

        counters = Counters()
        counters.cb = ctypes.sizeof(counters)
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.GetCurrentProcess.restype = ctypes.c_void_p
        api = ctypes.WinDLL("psapi", use_last_error=True).GetProcessMemoryInfo
        api.argtypes = [ctypes.c_void_p, ctypes.POINTER(Counters), ctypes.c_ulong]
        if not api(kernel.GetCurrentProcess(), ctypes.byref(counters), counters.cb):
            raise ctypes.WinError(ctypes.get_last_error())
        return {
            "working_set_bytes": counters.working,
            "peak_working_set_bytes": counters.peak_working,
            "private_bytes": counters.private,
        }
    resource = importlib.import_module("resource")
    multiplier = 1 if sys.platform == "darwin" else 1024
    return {
        "peak_working_set_bytes": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * multiplier
    }


def run_query(
    database: Path,
    source: Path,
    case: Case,
    mode: str,
    transcript: Path | None = None,
    *,
    diagnostic: bool = False,
) -> dict:
    queries_type = load_queries(source)
    store = GraphStore(database, SCOPE)
    queries = queries_type(store)
    start, target = arguments_for(case)
    cursor, pages, route_edges = None, 0, 0
    page = None
    samples = []
    wall_samples = []
    page_digest = hashlib.sha256()
    gc.collect()
    gc_before = gc.get_stats()
    memory_before = process_memory() if mode == "process" else {}
    if mode == "trace":
        tracemalloc.start()
    failure = None
    with (
        transcript.open("w", encoding="utf-8")
        if transcript
        else tempfile.TemporaryFile(mode="w+") as output
    ):
        loop_wall_started = time.perf_counter_ns() if mode == "cpu" else 0
        loop_cpu_started = time.process_time_ns() if mode == "cpu" else 0
        while True:
            previous = cursor
            wall_started = time.perf_counter_ns() if mode == "cpu" else 0
            started = time.process_time_ns() if mode == "cpu" else 0
            try:
                page = queries.path(
                    start, target, direction=case.direction, limit=case.limit, cursor=cursor
                )
            except (ValueError, TypeError, AttributeError) as error:
                failure = {"type": type(error).__name__, "message": str(error)}
                break
            if mode == "cpu":
                samples.append(time.process_time_ns() - started)
                wall_samples.append(time.perf_counter_ns() - wall_started)
            pages += 1
            route_edges += len(page["edges"])
            cursor = page["next_cursor"]
            if mode == "verify":
                # Restart before every continuation; replay the same input cursor
                # immediately after advancing it, including the terminal page.
                queries = queries_type(GraphStore(database, SCOPE))
                if previous is not None:
                    assert (
                        queries.path(
                            start,
                            target,
                            direction=case.direction,
                            limit=case.limit,
                            cursor=previous,
                        )
                        == page
                    )
                serialized = json.dumps(page, sort_keys=True, separators=(",", ":"))
                output.write(serialized + "\n")
                page_digest.update(serialized.encode())
            if not cursor:
                break
        loop_cpu_ns = time.process_time_ns() - loop_cpu_started if mode == "cpu" else 0
        loop_wall_ns = time.perf_counter_ns() - loop_wall_started if mode == "cpu" else 0
        if failure is not None:
            with store._connect() as db:
                state = {
                    "job": [
                        tuple(row)
                        for row in db.execute(
                            "SELECT id,sequence,last,finished,visited FROM graph_queries"
                        )
                    ],
                    "nodes": [
                        tuple(row)
                        for row in db.execute("SELECT * FROM graph_query_nodes ORDER BY node")
                    ],
                    "queue": [
                        tuple(row)
                        for row in db.execute("SELECT * FROM graph_query_queue ORDER BY seq")
                    ],
                }
            output.write(json.dumps({"failure": failure, "state": state}, sort_keys=True) + "\n")
    result = {"pages": pages, "route_edges": route_edges, "failure": failure}
    if mode == "verify":
        if case.shape.startswith("corrupt"):
            assert failure is not None, case
        else:
            expected_edges = {
                "wide-miss": 0,
                "wide-late": 1,
                "chain": case.nodes - 1,
                "routes": 2,
            }[case.shape]
            assert failure is None and route_edges == expected_edges, (case, result)
        result["page_digest"] = page_digest.hexdigest()
    if mode == "cpu":
        ordered = sorted(samples)
        wall_ordered = sorted(wall_samples)
        result.update(
            {
                "cpu_ns": sum(samples),
                "page_cpu_ns": samples,
                "page_p50_ns": ordered[(len(ordered) - 1) // 2],
                "page_p95_ns": ordered[min(len(ordered) - 1, int(len(ordered) * 0.95))],
                "page_max_ns": max(samples),
                "wall_ns": sum(wall_samples),
                "page_wall_ns": wall_samples,
                "page_wall_p50_ns": wall_ordered[(len(wall_ordered) - 1) // 2],
                "page_wall_p95_ns": wall_ordered[
                    min(len(wall_ordered) - 1, int(len(wall_ordered) * 0.95))
                ],
                "page_wall_max_ns": max(wall_samples),
                "loop_cpu_ns": loop_cpu_ns,
                "loop_wall_ns": loop_wall_ns,
            }
        )
        if diagnostic:
            nonterminal = sorted(wall_samples[:-1])
            result.update(
                {
                    "terminal_page_index": pages - 1,
                    "terminal_page_cpu_ns": samples[-1],
                    "terminal_page_wall_ns": wall_samples[-1],
                    "max_page_index": wall_samples.index(max(wall_samples)),
                    "terminal_page_is_max": wall_samples[-1] == max(wall_samples),
                    "nonterminal_page_wall_p95_ns": nonterminal[
                        min(len(nonterminal) - 1, int(len(nonterminal) * 0.95))
                    ]
                    if nonterminal
                    else None,
                    "nonterminal_page_wall_max_ns": max(nonterminal, default=None),
                }
            )
    if mode == "trace":
        retained, peak = tracemalloc.get_traced_memory()
        if diagnostic:
            # Capture the original end-current/peak with the completed response
            # still owned. Release the same harness locals for both sources;
            # cleanup and explicit collection are outside all query timing.
            del page, previous, cursor, queries, store, queries_type
            after_release = tracemalloc.get_traced_memory()[0]
            collected = gc.collect()
            post_gc = tracemalloc.get_traced_memory()[0]
        tracemalloc.stop()
        result.update({"traced_retained_bytes": retained, "traced_peak_bytes": peak})
        if diagnostic:
            result.update(
                {
                    "traced_end_current_bytes": retained,
                    "traced_after_release_current_bytes": after_release,
                    "traced_post_gc_current_bytes": post_gc,
                    "diagnostic_gc_collected": collected,
                }
            )
    if mode == "process":
        result.update({"process_before": memory_before, "process_after": process_memory()})
    if mode == "gc":
        result["gc_stats_delta"] = [
            {key: after[key] - before[key] for key in before}
            for before, after in zip(gc_before, gc.get_stats())
        ]
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-source", type=Path, required=True)
    parser.add_argument(
        "--candidate-source",
        type=Path,
        default=Path(__file__).resolve().parents[2]
        / "Server/src/services/reference_graph/queries.py",
    )
    parser.add_argument("--scratch", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--quick", action="store_true")
    parser.add_argument("--measure", action="store_true")
    parser.add_argument(
        "--diagnostic",
        action="store_true",
        help="Interleave CPU baseline controls and diagnose terminal pages and trace cleanup",
    )
    parser.add_argument(
        "--baseline-control",
        action="store_true",
        help="Pair two identical baseline sources to estimate run-to-run variation",
    )
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument(
        "--passes",
        nargs="+",
        choices=("cpu", "trace", "process", "gc"),
        default=["cpu", "trace", "process", "gc"],
    )
    parser.add_argument(
        "--verified-path", type=Path, help="Reuse exact-source, exact-case verification rows"
    )
    parser.add_argument("--case-index", type=int, action="append")
    parser.add_argument("--worker", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--source", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--case-json", help=argparse.SUPPRESS)
    parser.add_argument("--mode", choices=("cpu", "trace", "process", "gc"), help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.worker:
        print(
            json.dumps(
                run_query(
                    args.worker,
                    args.source,
                    Case(**json.loads(args.case_json)),
                    args.mode,
                    diagnostic=args.diagnostic,
                )
            )
        )
        return 0
    if args.repeats < 1:
        parser.error("repeats must be positive")
    if args.diagnostic and args.baseline_control:
        parser.error("diagnostic mode already interleaves its own baseline controls")
    args.scratch.mkdir(parents=True, exist_ok=True)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    sources = {
        "baseline": args.baseline_source.resolve(),
        "candidate": args.candidate_source.resolve(),
    }
    hashes = {name: hashlib.sha256(path.read_bytes()).hexdigest() for name, path in sources.items()}
    measurement_sources = (
        {"baseline": sources["baseline"], "baseline_control": sources["baseline"]}
        if args.baseline_control
        else sources
    )
    benchmark_hash = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    verified = []
    if args.verified_path:
        verified = [
            json.loads(row) for row in args.verified_path.read_text(encoding="utf-8").splitlines()
        ]
    with args.output.open("a", encoding="utf-8") as output:
        for index, case in enumerate(cases(args.quick)):
            if args.case_index is not None and index not in args.case_index:
                continue
            with tempfile.TemporaryDirectory(prefix="path-cost-", dir=args.scratch) as temporary:
                root = Path(temporary)
                fixture = build_fixture(root / "fixture", case, args.baseline_source)
                reuse = next(
                    (
                        row
                        for row in verified
                        if row.get("mode") == "verify"
                        and row.get("case") == asdict(case)
                        and row.get("source_sha256") == hashes
                    ),
                    None,
                )
                transcripts, results = [], []
                for variant, source in [] if reuse else sources.items():
                    database = root / f"verify-{variant}.sqlite"
                    shutil.copyfile(fixture, database)
                    transcript = root / f"{variant}.jsonl"
                    results.append(run_query(database, source, case, "verify", transcript))
                    transcripts.append(transcript)
                if reuse:
                    results = [reuse]
                else:
                    assert results[0] == results[1], (case, results)
                    with (
                        transcripts[0].open(encoding="utf-8") as before,
                        transcripts[1].open(encoding="utf-8") as after,
                    ):
                        for page_number, (left, right) in enumerate(
                            zip(before, after, strict=True)
                        ):
                            assert left == right, (case, page_number)
                record = {
                    "case_index": index,
                    "case": asdict(case),
                    "source_sha256": hashes,
                    "mode": "verify",
                    "fixture_bytes": fixture.stat().st_size,
                    "benchmark_sha256": benchmark_hash,
                    "python": sys.version,
                    **results[0],
                }
                output.write(json.dumps(record) + "\n")
                output.flush()
                print(json.dumps(record), flush=True)
                if not args.measure or case.shape.startswith("corrupt"):
                    continue
                for mode in args.passes:
                    mode_sources = (
                        {
                            **sources,
                            "control_baseline": sources["baseline"],
                            "baseline_control": sources["baseline"],
                        }
                        if args.diagnostic and mode == "cpu"
                        else measurement_sources
                    )
                    for repeat in range(args.repeats):
                        order = (
                            list(mode_sources) if repeat % 2 == 0 else list(reversed(mode_sources))
                        )
                        for variant in order:
                            database = root / "measurement.sqlite"
                            shutil.copyfile(fixture, database)
                            command = [
                                sys.executable,
                                str(Path(__file__).resolve()),
                                "--baseline-source",
                                str(args.baseline_source),
                                "--scratch",
                                str(args.scratch),
                                "--output",
                                str(args.output),
                                "--worker",
                                str(database),
                                "--source",
                                str(mode_sources[variant]),
                                "--case-json",
                                json.dumps(asdict(case)),
                                "--mode",
                                mode,
                            ]
                            if args.diagnostic:
                                command.append("--diagnostic")
                            completed = subprocess.run(
                                command, check=True, text=True, capture_output=True
                            )
                            record = {
                                "case_index": index,
                                "case": asdict(case),
                                "source_sha256": hashes,
                                "mode": mode,
                                "repeat": repeat,
                                "variant": variant,
                                "comparison": "baseline_control"
                                if args.baseline_control
                                or variant in {"control_baseline", "baseline_control"}
                                else "candidate",
                                "measured_source_sha256": hashlib.sha256(
                                    mode_sources[variant].read_bytes()
                                ).hexdigest(),
                                "benchmark_sha256": benchmark_hash,
                                "python": sys.version,
                                **json.loads(completed.stdout),
                            }
                            output.write(json.dumps(record) + "\n")
                            output.flush()
                            database.unlink()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
