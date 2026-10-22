"""Reproducible embedded storage experiment; Ladybug is benchmark-only.

Run with an isolated environment containing ladybug==0.19.1. Supply any native
library through LBUG_C_API_LIB_PATH and optional dependency directory through
--dll-directory. No downloads, machine configuration changes or product imports.
The first query after reopen is NOT an operating-system cold-cache measurement.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import platform
import sqlite3
import statistics
import subprocess
import sys
import time
from collections import deque
from pathlib import Path


def targets(source: int, size: int) -> list[int]:
    values = {(source * 17 + offset + 1) % size for offset in range(4)}
    if source % 16 == 0:
        values.add(0)
    return sorted(values)


def generate(root: Path, size: int) -> None:
    root.mkdir(parents=True, exist_ok=True)
    with (root / "nodes.csv").open("w", newline="", encoding="utf-8") as nodes:
        writer = csv.writer(nodes)
        writer.writerow(["id", "kind", "path"])
        writer.writerows(
            (i, "asset", f"Assets/Group{i // 1000}/Asset{i}.asset") for i in range(size)
        )
    with (root / "edges.csv").open("w", newline="", encoding="utf-8") as edges:
        writer = csv.writer(edges)
        writer.writerow(["src", "dst", "kind"])
        writer.writerows((i, j, "code") for i in range(size) for j in targets(i, size))


def reachable(adjacency: dict[int, list[int]], root: int, depth: int) -> list[int]:
    seen = {root}
    queue = deque([(root, 0)])
    while queue:
        node, distance = queue.popleft()
        if distance >= depth:
            continue
        for target in adjacency.get(node, []):
            if target not in seen:
                seen.add(target)
                queue.append((target, distance + 1))
    return sorted(seen - {root})


def digest(values) -> str:
    return hashlib.sha256(",".join(map(str, sorted(values))).encode("ascii")).hexdigest()


def edge_digest(rows) -> str:
    result = hashlib.sha256()
    for source, target in rows:
        result.update(f"{source},{target}\n".encode("ascii"))
    return result.hexdigest()


class SQLiteGraph:
    def __init__(self, path: Path):
        self.connection = sqlite3.connect(path)
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA synchronous=FULL")
        self.connection.execute("PRAGMA cache_size=-524288")
        self.connection.execute("PRAGMA threads=1")

    def ingest(self, root: Path) -> None:
        connection = self.connection
        connection.executescript(
            "CREATE TABLE asset(id INTEGER PRIMARY KEY, kind TEXT, path TEXT);"
            "CREATE TABLE ref(src INTEGER, dst INTEGER, kind TEXT, PRIMARY KEY(src,dst));"
        )
        with connection:
            for name, table in (("nodes", "asset"), ("edges", "ref")):
                with (root / f"{name}.csv").open(newline="", encoding="utf-8") as stream:
                    rows = csv.reader(stream)
                    next(rows)
                    connection.executemany(f"INSERT INTO {table} VALUES (?,?,?)", rows)
            connection.execute("CREATE INDEX reverse_ref ON ref(dst,src)")

    def query(self, kind: str, node: int) -> list[int]:
        queries = {
            "direct": "SELECT dst FROM ref WHERE src=? ORDER BY dst",
            "reverse": "SELECT src FROM ref WHERE dst=? ORDER BY src",
            "depth4": "WITH RECURSIVE walk(id,depth) AS (SELECT ?,0 UNION "
            "SELECT ref.dst,walk.depth+1 FROM ref JOIN walk ON ref.src=walk.id "
            "WHERE walk.depth<4) SELECT DISTINCT id FROM walk WHERE depth>0 AND id<>? ORDER BY id",
        }
        params = (node, node) if kind == "depth4" else (node,)
        return [row[0] for row in self.connection.execute(queries[kind], params)]

    def replace(self, changes: dict[int, list[int]]) -> None:
        with self.connection:
            self.connection.executemany("DELETE FROM ref WHERE src=?", ((i,) for i in changes))
            self.connection.executemany(
                "INSERT INTO ref VALUES (?,?,'code')",
                ((source, target) for source, values in changes.items() for target in values),
            )

    def fingerprint(self) -> str:
        return edge_digest(self.connection.execute("SELECT src,dst FROM ref ORDER BY src,dst"))

    def close(self) -> None:
        self.connection.close()


class LadybugGraph:
    def __init__(self, path: Path):
        import ladybug  # pylint: disable=import-error  # Isolated benchmark dependency.

        self.database = ladybug.Database(path, buffer_pool_size=512 * 1024**2, max_num_threads=1)
        self.connection = ladybug.Connection(self.database, num_threads=1)

    def execute(self, query: str, parameters=None):
        result = self.connection.execute(query, parameters)
        try:
            while result.has_next():
                yield result.get_next()
        finally:
            result.close()

    def command(self, query: str, parameters=None) -> None:
        for _ in self.execute(query, parameters):
            pass

    def ingest(self, root: Path) -> None:
        self.command("CREATE NODE TABLE Asset(id INT64, kind STRING, path STRING, PRIMARY KEY(id))")
        self.command("CREATE REL TABLE Ref(FROM Asset TO Asset, kind STRING)")
        for name, table in (("nodes", "Asset"), ("edges", "Ref")):
            path = (root / f"{name}.csv").as_posix().replace("'", "''")
            self.command(f"COPY {table} FROM '{path}' (HEADER=true)")

    def query(self, kind: str, node: int) -> list[int]:
        queries = {
            "direct": "MATCH (s:Asset)-[:Ref]->(t:Asset) WHERE s.id=$id RETURN t.id ORDER BY t.id",
            "reverse": "MATCH (s:Asset)-[:Ref]->(t:Asset) WHERE t.id=$id RETURN s.id ORDER BY s.id",
            "depth4": "MATCH (s:Asset)-[:Ref*1..4]->(t:Asset) WHERE s.id=$id AND t.id<>$id "
            "RETURN DISTINCT t.id ORDER BY t.id",
        }
        return [int(row[0]) for row in self.execute(queries[kind], {"id": node})]

    def replace(self, changes: dict[int, list[int]]) -> None:
        self.command("BEGIN TRANSACTION")
        try:
            self.command(
                "UNWIND $ids AS id MATCH (s:Asset {id:id})-[r:Ref]->() DELETE r",
                {"ids": list(changes)},
            )
            # 0.19.1 returned the first target for every row when two correlated
            # MATCH clauses consumed an UNWIND struct list. Scalar bindings are
            # checked against the complete post-reopen edge digest below.
            for source, values in changes.items():
                for target in values:
                    self.command(
                        "MATCH (s:Asset {id:$src}), (t:Asset {id:$dst}) "
                        "CREATE (s)-[:Ref {kind:'code'}]->(t)",
                        {"src": source, "dst": target},
                    )
            self.command("COMMIT")
        except BaseException as failure:
            try:
                self.command("ROLLBACK")
            except RuntimeError as rollback_error:
                failure.add_note(f"Rollback: {rollback_error}")
            raise

    def fingerprint(self) -> str:
        return edge_digest(
            self.execute("MATCH (s:Asset)-[:Ref]->(t:Asset) RETURN s.id,t.id ORDER BY s.id,t.id")
        )

    def close(self) -> None:
        self.connection.close()
        self.database.close()


def peak_rss_bytes() -> int:
    if os.name != "nt":
        import resource  # pylint: disable=import-error  # POSIX standard library.

        value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return int(value if sys.platform == "darwin" else value * 1024)
    import ctypes
    from ctypes import wintypes

    class Counters(ctypes.Structure):
        _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD)] + [
            (name, ctypes.c_size_t)
            for name in (
                "PeakWorkingSetSize",
                "WorkingSetSize",
                "QuotaPeakPagedPoolUsage",
                "QuotaPagedPoolUsage",
                "QuotaPeakNonPagedPoolUsage",
                "QuotaNonPagedPoolUsage",
                "PagefileUsage",
                "PeakPagefileUsage",
            )
        ]

    counters = Counters()
    counters.cb = ctypes.sizeof(counters)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    psapi = ctypes.WinDLL("psapi", use_last_error=True)
    psapi.GetProcessMemoryInfo.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(Counters),
        wintypes.DWORD,
    ]
    if not psapi.GetProcessMemoryInfo(
        kernel.GetCurrentProcess(), ctypes.byref(counters), counters.cb
    ):
        raise ctypes.WinError(ctypes.get_last_error())
    return counters.PeakWorkingSetSize


def worker(engine: str, root: Path, size: int, run: str) -> dict:
    factory = SQLiteGraph if engine == "sqlite" else LadybugGraph
    path = root / f"{engine}-{run}.db"
    if path.exists():
        raise FileExistsError(path)
    started = time.perf_counter()
    graph = factory(path)
    graph.ingest(root)
    result = {"engine": engine, "nodes": size, "import_s": time.perf_counter() - started}
    graph.close()
    graph = factory(path)
    result["queries"] = {}
    for kind, node in (("direct", 1), ("reverse", 0), ("depth4", 1)):
        times = []
        values = []
        for _ in range(6):
            started = time.perf_counter()
            values = graph.query(kind, node)
            times.append(time.perf_counter() - started)
        result["queries"][kind] = {
            "first_after_reopen_s": times[0],
            "warm_median_s": statistics.median(times[1:]),
            "warm_samples_s": times[1:],
            "rows": len(values),
            "sha256": digest(values),
        }
    result["before_sha256"] = graph.fingerprint()
    changes = {i: [(i + size // 2) % size] for i in range(min(size, 1000))}
    started = time.perf_counter()
    graph.replace(changes)
    result["replace_1000_s"] = time.perf_counter() - started
    graph.close()
    graph = factory(path)
    result["after_reopen_sha256"] = graph.fingerprint()
    graph.close()
    result["peak_process_rss_bytes"] = peak_rss_bytes()
    result["database_bytes"] = sum(p.stat().st_size for p in root.glob(path.name + "*"))
    result["python"] = platform.python_version()
    result["platform"] = platform.platform()
    if engine == "sqlite":
        result["version"] = sqlite3.sqlite_version
    else:
        import ladybug  # pylint: disable=import-error  # Isolated benchmark dependency.

        result["version"] = ladybug.__version__
    return result


def validate(result: dict, size: int) -> None:
    adjacency = {i: targets(i, size) for i in range(size)}
    expected = {
        "direct": adjacency[1],
        "reverse": [i for i, values in adjacency.items() if 0 in values],
        "depth4": reachable(adjacency, 1, 4),
    }
    for kind, values in expected.items():
        if result["queries"][kind]["sha256"] != digest(values):
            raise AssertionError(f"Incorrect {kind} results from {result['engine']}")
    before = edge_digest((i, j) for i, values in adjacency.items() for j in values)
    for i in range(min(size, 1000)):
        adjacency[i] = [(i + size // 2) % size]
    after = edge_digest((i, j) for i, values in adjacency.items() for j in values)
    if result["before_sha256"] != before or result["after_reopen_sha256"] != after:
        raise AssertionError(f"Lost or extra relationships in {result['engine']}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--sizes", nargs="+", type=int, default=[65536, 250000])
    parser.add_argument("--repetitions", type=int, default=3)
    parser.add_argument(
        "--engines", nargs="+", choices=["sqlite", "ladybug"], default=["sqlite", "ladybug"]
    )
    parser.add_argument("--dll-directory", type=Path)
    parser.add_argument("--worker", choices=["sqlite", "ladybug"])
    parser.add_argument("--run", default="0")
    args = parser.parse_args()
    if any(size < 4 for size in args.sizes) or args.repetitions < 1:
        parser.error("Use at least four nodes and one repetition.")
    if args.dll_directory and os.name != "nt":
        parser.error("--dll-directory applies only to Windows.")
    dll_handle = os.add_dll_directory(str(args.dll_directory)) if args.dll_directory else None
    try:
        if args.worker:
            print(json.dumps(worker(args.worker, args.output, args.sizes[0], args.run)))
            return
        args.output.mkdir(parents=True, exist_ok=False)
        results = []
        for size in args.sizes:
            root = args.output / str(size)
            generate(root, size)
            for repetition in range(args.repetitions):
                for engine in args.engines:
                    command = [
                        sys.executable,
                        __file__,
                        "--worker",
                        engine,
                        "--output",
                        str(root),
                        "--sizes",
                        str(size),
                        "--run",
                        str(repetition),
                    ]
                    if args.dll_directory:
                        command.extend(["--dll-directory", str(args.dll_directory)])
                    completed = subprocess.run(command, capture_output=True, text=True)
                    if completed.returncode:
                        sys.stderr.write(completed.stderr)
                        completed.check_returncode()
                    result = json.loads(completed.stdout)
                    validate(result, size)
                    result["repetition"] = repetition
                    results.append(result)
                    (args.output / "results.json").write_text(
                        json.dumps(results, indent=2), encoding="utf-8"
                    )
                    print(
                        f"{engine} nodes={size} run={repetition}: validated import={result['import_s']:.3f}s",
                        flush=True,
                    )
    finally:
        if dll_handle is not None:
            dll_handle.close()


if __name__ == "__main__":
    main()
