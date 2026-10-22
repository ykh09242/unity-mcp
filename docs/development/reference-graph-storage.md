# Reference graph storage selection

Use Python's standard-library SQLite for the automatically maintained reference
graph. It runs inside the existing MCP server, requires no Unity driver or database
service, and adds no first-run binary download. This decision applies to Windows,
macOS and Linux. The measurements below were executed on Windows; they are not
claims about unmeasured macOS/Linux performance.

## Equivalent workload

`tools/experiments/reference_graph_storage.py` compares SQLite 3.50.4 and Ladybug
0.19.1 with CPython 3.14.6. Each engine/repetition uses a new process and database,
the same UTF-8 CSV input, one execution thread and a 512 MiB configured cache/buffer
budget. This experiment budget is not a production graph-size limit. SQLite uses
WAL with `synchronous=FULL`; Ladybug uses its default WAL/checkpoint configuration.

There are three repetitions at 65,536 nodes and three at 250,000 nodes. Four
deterministic outgoing relationships per node, plus high-fan-in relationships to
node zero, produce over one million relationships in the large workload. Tests
cover direct/reverse lookup, four-hop distinct reachability including cycles, and
replacement of 1,000 sources' outgoing edges in a durable transaction.

Every run checks the complete sorted relationship SHA-256 before mutation and
after close/reopen. Each query's final timed result is checked against an independent
in-memory breadth-first oracle; earlier timed samples are not separately validated.
Small literal fixtures test the oracle's cycle/depth behavior
and catch lost/stale edges after replacement. Input generation and the oracle run
in the parent process, outside the measured worker's time and memory.

Query timings include result materialization across the Python binding, as the MCP
server must consume the rows. Five warm samples follow the first query after
reopen. **Reopen does not flush the OS filesystem cache.** Import includes schema,
indexes and durable inserts. Peak process RSS includes the binding, query results
and digest verification; it is not a database-only allocation metric.

## Windows results

Values are medians of three fresh-process runs; query values are medians of each
run's five warm samples. Raw samples are in
[`reference-graph-storage-results.json`](reference-graph-storage-results.json).

| 250,000 nodes | SQLite | Ladybug |
| --- | ---: | ---: |
| Import | 2.576 s | 2.587 s |
| Direct lookup | 0.0048 ms | 1.9946 ms |
| Reverse lookup of high-fan-in target | 4.1191 ms | 46.5860 ms |
| Four-hop distinct reachability | 0.2600 ms | 4.4420 ms |
| Replace 1,000 sources and commit | 0.0136 s | 2.3589 s |
| Peak process RSS | 126 MiB | 200 MiB |
| Closed database size | 63.2 MiB | 33.3 MiB |

| 65,536 nodes | SQLite | Ladybug |
| --- | ---: | ---: |
| Import | 0.611 s | 0.732 s |
| Direct lookup | 0.0045 ms | 0.9405 ms |
| Reverse lookup | 1.0272 ms | 11.6213 ms |
| Four-hop reachability | 0.2451 ms | 3.3206 ms |
| Replace 1,000 sources and commit | 0.0114 s | 0.8072 s |
| Peak process RSS | 53.4 MiB | 115.4 MiB |

Ladybug's smaller disk footprint is useful. SQLite wins the interactive/incremental
workload measured here and has the simpler deployment contract. These results
compare these adapters and workloads, not every possible query plan or graph shape.
Long analytical paths or a different language binding could change the tradeoff.

## Binding and query checks

The Windows Ladybug Python wheel did not find its required C API shared library.
The official `liblbug-windows-x86_64.zip` release (8,292,678 bytes) supplied a
20,230,656-byte DLL; that DLL also imports `libssl-3-x64.dll` and
`libcrypto-3-x64.dll`. For this experiment only, an existing Git installation's
OpenSSL directory was admitted with a process-local `os.add_dll_directory` handle.
This did not install a driver or change global PATH. A product using that release
would need to package and maintain these dependencies for its supported platforms.

An initial mutation query with a Cartesian MATCH exceeded the 512 MiB buffer at
250,000 nodes. A property-bound correlated UNWIND variant avoided that failure but
returned the first target for all rows in this installed 0.19.1 combination:

```cypher
UNWIND $rows AS row
MATCH (s:Asset {id:row.src}) MATCH (t:Asset {id:row.dst})
RETURN row.src,row.dst,s.id,t.id
```

For rows `(0,32768), (1,32769), (2,32770)`, its observed output was
`(0,32768,0,32768), (0,32768,1,32768), (0,32768,2,32768)`.
The complete digest check rejected it. The final experiment uses scalar-bound
inserts inside one transaction, validates every resulting edge, and discards the
earlier timings. This observation is limited to this version/build/binding; it is
not a claim that every Ladybug version has the same behavior.

## Reproduction

Run the benchmark in a separate environment with `ladybug==0.19.1`; the production
server does not depend on it. Provide the official native library if the wheel
requires it. On Windows, pass `--dll-directory` for its already provisioned native
dependencies. On macOS/Linux omit that option.

```sh
python -m unittest tools.tests.test_reference_graph_storage
python tools/experiments/reference_graph_storage.py \
  --output /absolute/path/to/new-result-directory \
  --sizes 65536 250000 --repetitions 3
```

The output directory must be new, so a rerun cannot replace prior evidence. A worker
error or result mismatch fails the run. A partial `results.json` is not a completed
comparison. Python 3.11+ is supported; SQLite-only smoke runs need no extra package.

| Input | SHA-256 |
| --- | --- |
| 65,536-node CSV | `31f701ba95b77347b59b8465e69f4b677f6e47fddcd3dfa57dfa11fbee1e0ad3` |
| 65,536-node edge CSV | `53b0268d33f7cda6548dbd5aa9eefa82f11aca51301c7b54adf3e8bbc77917a2` |
| 250,000-node CSV | `403bf822b2ea8024d14a2296d0949f7fe2f33a3ed79044fa4be3173cfa552c8b` |
| 250,000-node edge CSV | `5d4ff9cbc28037e306ac6a7578aa2b24614abf0694eb739c78f29923eb8af124` |
| Official Windows native ZIP | `865e2c8765064be76d41e4d786dfb0cd3ad0c258ddaf7b522fa3da7159ecd3ef` |

References: [Python sqlite3](https://docs.python.org/3.11/library/sqlite3.html),
[Ladybug Python API](https://docs.ladybugdb.com/client-apis/python/),
[Ladybug MATCH](https://docs.ladybugdb.com/cypher/query-clauses/match/), and
[official native release](https://github.com/LadybugDB/ladybug/releases/tag/v0.19.1).
