"""Pair exact-beta and candidate journal finish/reopen costs with synthetic data.

Run with the existing Server interpreter; no packages or journal data are installed.
Use --check for untimed readiness. Obtain an exclusive CPU lease before measurement.
"""

from __future__ import annotations

import argparse
import ctypes
import gc
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import statistics
import subprocess
import sys
import tempfile
import time
import tracemalloc
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[2]
SIZES = {"1k": 1024, "4m": 4 * 1024 * 1024, "near64m": 64 * 1024 * 1024 - 4096}
CASES = [f"{shape}-{size}" for shape in ("scalar", "container") for size in SIZES] + [
    "fallback-4m",
    "aliased-4m",
    "cancel",
    "replace-failure",
    "cancel-failure",
]


class PayloadList(list):
    """Supported list subtype that requires the compatibility projection."""


def digest(path: Path) -> str:
    """Hash a complete committed document without another whole-file buffer."""
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def payload_for(case: str) -> dict:
    """Keep every large synthetic graph inside unchanged wire/retention bounds."""
    if case in ("fallback-4m", "aliased-4m"):
        payload = payload_for("container-4m")
        rows = payload["items"]
        # Place the unsupported sharing/projection shape last: admission tracks
        # nearly the entire large graph before selecting the original fallback.
        if case == "fallback-4m":
            rows[-1]["value"] = PayloadList(rows[-1]["value"])
        else:
            rows[-1] = rows[0]
        return payload
    if "-" not in case or case.endswith("failure"):
        return {"content": [], "structuredContent": {"value": "control"}}
    shape, size = case.split("-")
    target = SIZES[size]
    if shape == "scalar":
        payload = {"content": [{"type": "text", "text": ""}]}
        overhead = len(json.dumps(payload, ensure_ascii=False).encode())
        payload["content"][0]["text"] = "x" * (target - overhead)
        return payload
    count = 8 if size == "1k" else 8192
    rows = [{"value": [""], "index": index, "optional": None} for index in range(count)]
    payload = {"items": rows}
    overhead = len(json.dumps(payload, ensure_ascii=False).encode())
    width, extra = divmod(target - overhead, count)
    for index, row in enumerate(rows):
        row["value"][0] = "x" * (width + (index < extra))
    return payload


def load_journal(source: Path, repository: Path):
    """Load the selected journal with the worker tree's unchanged dependencies."""
    sys.path.insert(0, str(repository / "Server" / "src"))
    spec = importlib.util.spec_from_file_location("pc01_journal", source)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    module.uuid4 = lambda: SimpleNamespace(hex="a" * 32)
    return module


def operation(module):
    """Use deterministic nonsecret metadata for complete byte comparisons."""
    return module.WaitOperation(
        owner="bench",
        project="synthetic",
        instance="synthetic@project",
        session_id="synthetic-session",
        job_id="synthetic-job",
        wait=module.DeadlineCheckpoint.capture(20, now=1000.0),
        outer=module.DeadlineCheckpoint.capture(10, now=1000.0),
    )


def scenario(module, root: Path, case: str, payload: dict) -> str:
    """Exercise actual admission, fsync/replace, close, reopen and full result read."""
    journal = module.WaitJournal(root, clock=lambda: 1000.0)
    try:
        record = journal.create(operation(module))
        path = root / f"{record.task_id}.json"
        if case.endswith("failure"):
            before = digest(path)
            charged = journal.charged_bytes
            replace = module.os.replace

            def refuse(*_args):
                raise OSError("synthetic replacement refusal")

            module.os.replace = refuse
            try:
                try:
                    if case == "cancel-failure":
                        journal.request_cancel(record.task_id, "bench")
                    else:
                        journal.finish(record.task_id, "completed", result=payload)
                except module.JournalUnavailable:
                    pass
                else:
                    raise AssertionError("replacement refusal did not propagate")
            finally:
                module.os.replace = replace
            assert digest(path) == before
            assert journal.charged_bytes == charged
            assert not list(root.glob("*.stage"))
        if case in ("cancel", "cancel-failure", "replace-failure"):
            assert journal.request_cancel(record.task_id, "bench").cancel_requested
            journal.finish(record.task_id, "cancelled")
        else:
            journal.finish(record.task_id, "completed", result=payload)
        committed = digest(path)
    finally:
        journal.close()
    with module.WaitJournal(root, clock=lambda: 1001.0) as reopened:
        restored = reopened.get(record.task_id, "bench")
        if case in ("cancel", "cancel-failure", "replace-failure"):
            assert restored.status == "cancelled" and restored.result is None
        else:
            assert restored.status == "completed" and restored.result == payload
        assert reopened.charged_bytes == sum(path.stat().st_size for path in root.iterdir())
    return committed


def rss_bytes() -> dict[str, int]:
    """Read native Windows process working set; this pass has no latency clock."""
    if os.name != "nt":
        raise RuntimeError("RSS pass currently requires Windows GetProcessMemoryInfo")

    class Counters(ctypes.Structure):
        _fields_ = [
            ("cb", ctypes.c_ulong),
            ("PageFaultCount", ctypes.c_ulong),
            ("PeakWorkingSetSize", ctypes.c_size_t),
            ("WorkingSetSize", ctypes.c_size_t),
            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
            ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
            ("PagefileUsage", ctypes.c_size_t),
            ("PeakPagefileUsage", ctypes.c_size_t),
        ]

    counters = Counters()
    counters.cb = ctypes.sizeof(counters)
    current = ctypes.windll.kernel32.GetCurrentProcess
    current.restype = ctypes.c_void_p
    read = ctypes.windll.psapi.GetProcessMemoryInfo
    read.argtypes = [ctypes.c_void_p, ctypes.POINTER(Counters), ctypes.c_ulong]
    if not read(current(), ctypes.byref(counters), counters.cb):
        raise ctypes.WinError()
    return {"working": counters.WorkingSetSize, "peak": counters.PeakWorkingSetSize}


def worker(args) -> dict:
    """Warm imports and a small real lifecycle outside the measured operation."""
    module = load_journal(args.source, args.repository)
    import models.response_limits as limits  # pylint: disable=import-outside-toplevel
    import services.private_store_bootstrap as bootstrap  # pylint: disable=import-outside-toplevel
    import pydantic  # pylint: disable=import-outside-toplevel

    with tempfile.TemporaryDirectory(prefix="pc01-", dir=args.scratch) as folder:
        root = Path(folder)
        scenario(module, root / "warm", "scalar-1k", payload_for("scalar-1k"))
        payload = payload_for(args.case)
        assert limits.response_size(payload) is not None
        gc.collect()
        row = {
            "case": args.case,
            "mode": args.mode,
            "source": str(Path(module.__file__).resolve()),
            "source_sha256": digest(args.source),
            "imports": {"limits": limits.__file__, "bootstrap": bootstrap.__file__},
            "dependency_sha256": {
                "limits": digest(Path(limits.__file__)),
                "bootstrap": digest(Path(bootstrap.__file__)),
            },
            "python": sys.version,
            "pydantic": pydantic.__version__,
        }
        before_gc = [entry["collections"] for entry in gc.get_stats()]
        if args.mode == "trace":
            tracemalloc.start()
        if args.mode == "rss":
            row["rss_before"] = rss_bytes()
        if args.mode == "latency":
            wall, cpu = time.perf_counter_ns(), time.process_time_ns()
        row["document_sha256"] = scenario(module, root / "measured", args.case, payload)
        if args.mode == "latency":
            row["wall_ns"] = time.perf_counter_ns() - wall
            row["cpu_ns"] = time.process_time_ns() - cpu
            row["wall_seconds"] = row["wall_ns"] / 1_000_000_000
            row["cpu_seconds"] = row["cpu_ns"] / 1_000_000_000
        if args.mode == "trace":
            row["traced_current"], row["traced_peak"] = tracemalloc.get_traced_memory()
        if args.mode == "rss":
            row["rss_after"] = rss_bytes()
        del payload
        gc.collect()
        row["gc_collections"] = [
            after["collections"] - before for before, after in zip(before_gc, gc.get_stats())
        ]
        if args.mode == "trace":
            row["traced_retained"] = tracemalloc.get_traced_memory()[0]
            tracemalloc.stop()
        if args.mode == "rss":
            row["rss_released"] = rss_bytes()
        return row


def paired(args) -> dict:
    """Alternate source order and keep independent runs and memory passes distinct."""
    rows = []
    modes = ["check"] if args.check else ["latency", "trace", "rss"]
    for run in range(args.runs):
        for mode in modes:
            rounds = args.rounds if mode == "latency" else 1
            for round_number in range(rounds):
                for case_number, case in enumerate(args.cases):
                    pair = {}
                    labels = (
                        ("baseline", "candidate")
                        if (run + round_number + case_number) % 2 == 0
                        else ("candidate", "baseline")
                    )
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
                        row = json.loads(result.stdout)
                        row.update(label=label, run=run, round=round_number)
                        rows.append(row)
                        pair[label] = row
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
                    args.output.write_text(
                        json.dumps({"rows": rows}, indent=2) + "\n", encoding="utf-8"
                    )
    summary = {}
    for case in args.cases:
        summary[case] = {}
        for metric in ("wall_seconds", "cpu_seconds", "traced_peak", "traced_retained"):
            values = {
                label: [
                    row[metric]
                    for row in rows
                    if row["case"] == case and row["label"] == label and metric in row
                ]
                for label in ("baseline", "candidate")
            }
            if values["baseline"]:
                medians = {label: statistics.median(entries) for label, entries in values.items()}
                summary[case][metric] = {
                    **medians,
                    "candidate_over_baseline": (
                        medians["candidate"] / medians["baseline"] if medians["baseline"] else None
                    ),
                }
    return {"rows": rows, "summary": summary}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", type=Path, default=ROOT)
    parser.add_argument("--baseline", type=Path)
    parser.add_argument(
        "--candidate", type=Path, default=ROOT / "Server/src/services/test_wait_journal.py"
    )
    parser.add_argument("--scratch", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--runs", type=int, default=2)
    parser.add_argument("--rounds", type=int, default=3)
    parser.add_argument("--cases", nargs="+", choices=CASES, default=CASES)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--worker", action="store_true")
    parser.add_argument("--source", type=Path)
    parser.add_argument("--case", choices=CASES)
    parser.add_argument("--mode", choices=["check", "latency", "trace", "rss"])
    args = parser.parse_args()
    if not args.worker and (args.baseline is None or args.output is None):
        parser.error("paired runs require --baseline and --output")
    if args.worker and (args.source is None or args.case is None or args.mode is None):
        parser.error("workers require --source, --case and --mode")
    args.scratch.mkdir(parents=True, exist_ok=True)
    if args.worker:
        print(json.dumps(worker(args)))
    else:
        result = paired(args)
        args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(result["summary"], indent=2))


if __name__ == "__main__":
    main()
