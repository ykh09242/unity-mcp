# Dependency performance decisions

Measured on Windows with the existing isolated worktree, installed dependencies and package caches on 2026-10-24. Python 3.14.6, FastMCP 4.1.3, Python MCP 2.3.0, Node 26.12.1 and npm 12.2.1 were used. The [machine-independent measurements](benchmarks/dependency-20261024.json) retain repeats, digests and resource counters without local paths. These are local comparisons, not cross-platform performance guarantees.

## Discovery profiles

The production registry was registered and listed using the real in-memory SDK client. Initial listing and UTF-8 MCP schema serialization were timed; no live Editor or model inference was included.

| Profile | Initial tools | Schema bytes | Median list/serialization time |
| --- | ---: | ---: | ---: |
| Full | 119 | 377,019 | 42.45 ms |
| Core | 41 | 88,101 | 10.98 ms |
| Minimal | 4 | 3,247 | 2.97 ms |

Expanding core or minimal produced the exact full descriptors. Representative synthetic calls also preserved validation and results. This supports the opt-in minimal profile and on-demand `manage_tools` descriptions; it does not establish real-model task success or token counts. Core remains the local default and full remains available for large workflows. Registration timings in the raw record are single runs and are not startup improvement claims. The `manage_tools` description documents scope and client-refresh behavior.

## Actual diagnostic HTTP path

`DoctorHttpFactory` is used by the diagnostic and tool-description MCP clients. The [HTTP probe](../tools/experiments/dependencies/http_client_probe.py) exercises its actual bounded transport, response wrapper, cookie jar and cleanup with `MockTransport`. It compares httpx2/httpcore2 2.15.1 against 2.16.0, with three alternating process pairs, 20 warmups and 200 measured requests per workload. Each response carries 2,090 bytes and one cookie update. The heavy workloads prepopulate 256 or 2,048 matching cookies; these are stress fixtures, not observed normal user traffic.

| Prepopulated cookies | 2.15.1 median of run medians | 2.16.0 median of run medians | Change |
| --- | ---: | ---: | ---: |
| 0 | 0.238 ms | 0.232 ms | Small/no reliable practical gain |
| 256 | 2.068 ms | 1.583 ms | 23.4% lower |
| 2,048 | 14.406 ms | 10.700 ms | 25.7% lower |

Cookie-heavy runs improved in all three pairs, with different absolute gains. Outgoing cookie hashes, response bytes/hashes, headers, total byte accounting and client closure matched exactly. Existing production limits remained in force. Retain the compatible 2.16.0 lock upgrade for these existing callers; do not migrate unrelated httpx clients or attribute this network-free result to Unity RPC latency.

To reproduce one installed version from the repository root, use the existing server environment:

```powershell
$env:PYTHONPATH = (Resolve-Path Server/src).Path
Server/.venv/Scripts/python.exe tools/experiments/dependencies/http_client_probe.py
```

Run the other version in a separate package target/environment and put that target before `Server/src` on `PYTHONPATH`. Change httpx2 and httpcore2 together. Alternate order across three pairs; compare every digest, byte count and closure result before considering timing. Do not replace the shared development environment to run a baseline.

## Documentation builds

Three generated-cache-cold/warm pairs ran the complete `npm run build`, including all 30 prebuild and three route tests. Before each cold member, the existing `.docusaurus`, `node_modules/.cache` and `build` directories were moved to an owned ignored backup inside the same worktree. Installed `node_modules`, npm downloads and the OS filesystem cache were reused. Warm members immediately rebuilt the same source. This is not a cold machine or a fresh package download benchmark.

| Pair | Generated cache absent | Warm cache |
| --- | ---: | ---: |
| 1 | 83.09 s | 11.96 s |
| 2 | 28.48 s | 11.89 s |
| 3 | 28.68 s | 18.94 s |

Every run passed and emitted 198 HTML pages, 438 files and 29,684,033 bytes. The median was 28.68 s versus 11.96 s. Reuse local generated caches for related verification; a first run still costs substantially more. No bundler replacement or CI-generated-cache restoration was adopted: this experiment did not establish safe invalidation across Git history, configuration, build-time environment variables and hosted runners. Existing dependency caches remain enabled. Route tests and aggregate output equality are not byte-for-byte proof of every emitted file.

## Experimental C# SDK

Keep the historical [transport probe](../tools/experiments/transport/README.md) and its lock unchanged. Two isolated copies of the same four C# source files compared MCP 2.2.0 / Hosting 10.0.1 against MCP 2.3.0 / Hosting 10.0.13 with the shared NuGet cache. Both Release builds passed. Three alternating pairs used the existing OS-stdio harness, five warmups and 30 calls per workload; five additional alternating cached fresh-process starts measured initialization.

| Workload | Baseline median of run medians | Candidate median of run medians |
| --- | ---: | ---: |
| Small | 0.153 ms | 0.156 ms |
| State | 0.340 ms | 0.339 ms |
| 4 MiB payload | 38.792 ms | 38.463 ms |
| Job | 0.069 ms | 0.073 ms |
| Fresh process, cached files | 255.81 ms | 251.39 ms |

Descriptors, wire byte counts and semantic hashes matched, including the 4 MiB case. Session rejection and owned-process cleanup probes passed on both versions. There is no consistent latency benefit beyond local variation, so do not replace the baseline or production transport. Sampled post-large-call working sets had medians of 727.8 MiB and 602.9 MiB; private bytes were 1,027.8 MiB and 999.2 MiB. These snapshots include serializer/GC history, do not measure allocations or sustained retention, and are insufficient to claim a memory optimization. No workload size or deadline was reduced.

For a new comparison, copy the probe sources and project into two owned experiment directories, restore each version pair to the existing NuGet cache, and run `measure`/`Case` from `tools/experiments/transport/mcp_bench.py` against both resulting executables. Preserve separate lockfiles and outputs; use `startup_bench.py` for the alternating initialization experiment. Keep original probe records intact and require schema, byte, semantic, rejection and cleanup parity before changing a dependency baseline.
