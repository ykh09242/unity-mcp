# Reusable large-workload cohorts

The [shared manifest](../../tools/large_workload_cohorts.json) names six optional cohorts and their existing producers. These probes extend the transport, shared-read, artifact, reference, report, reload and Editor lifecycle fixtures; they do not replace the test runner or install a benchmarking service. No CI schedule or licensed Unity execution is enabled.

## Execution and ownership

Use an owned isolated project and retain its Library and package cache. Snapshot source inputs with the existing native QA input tooling, select the exact methods in the `large-workloads` group, and require all five methods to pass with no missing or skipped cases. Stage `tools/experiments/service_optimizations/ServiceOptimizationProbes.cs` once in that project's test assembly; reuse an existing staged copy. The other four methods and `WorkloadProbe.cs` are already in the repository test project. They are explicit tests and do not run with ordinary fixture selection.

Set `UNITY_MCP_WORKLOAD_OUTPUT` to a new controller-owned report directory, `UNITY_MCP_OPTIMIZATION_OUTPUT` to the same directory, and `UNITY_MCP_WORKLOAD_INPUTS_SHA256` to the SHA-256 of the frozen native input JSON before launch. Recheck every input hash after execution. Keep the NUnit XML, input JSON, invocation identity and process-cleanup evidence together. Preserve any unrelated Editor and project state. The fixture owns its generated objects, report directory and source assets and restores its previous state.

The native group verifies 10,000 ordered hierarchy children, a complete 17 MiB asset-reference source, 64 report summaries plus all 100 pages of 10,000 detail rows, 32 visible/hidden/changed activity cycles with 32,000 idle ticks, and the existing four 32 MiB/eight 512 KiB artifact burst. All artifact lengths and digests must match, every job must finish, and staging files must be released. This finite burst does not establish sustained starvation freedom.

Run the existing Python probe with the existing server environment:

```sh
python tools/experiments/service_optimizations/shared_read.py
```

`UNITY_MCP_WORKLOAD_OUTPUT` also selects its output directory; otherwise the existing `.work/service-optimization-probes` location is used. It verifies exact ordered output, copy isolation, a single upstream read and released shared-read accounting for all 18 shape/reader/arm combinations. Python source and harness fingerprints are checked before and after measurement. No optional Python package is needed.

Run `ScriptChangeSetReloadTests.ActualDomainReload_RetainsAndRecoversInterruptedApplication` three times through the existing owned reload controller, reusing the same project and inputs. Each run must contain `inputs.json`, `invocation.json`, `process-result.json` and the controller's NUnit result (`results.xml` or `lifecycle-results.xml`). Select the latter explicitly with `--reload-results-name lifecycle-results.xml`; filenames are never guessed. The invocation identifies its unique run, selected fixture, project and reused cache; the process receipt must show success, no timeout/survivors and preserved unrelated processes. Actual interrupted-application recovery checks original file bytes and metadata after each domain replacement. Reload XML duration is not interpreted as reload latency.

## Capture and comparison

Pass all raw cohort JSON files explicitly (exclude the legacy `shared-read.json` and `opt12-mixed-queue.json`). The capture adapter verifies exact staged input fingerprints and required NUnit inventory. Repository source provenance permits only CRLF/LF checkout conversion for known text files, records those exceptions, and lists retained prepared overlays whose original sources are absent or outside the selected checkout. Overlay hashes remain mandatory; this is not a claim that the entire isolated project is an exact checkout. Tracked Git diff fingerprints hash raw bytes, including non-UTF8 content. It reads the supplied evidence; it never launches Unity, downloads anything or changes workload limits.

```sh
python tools/workload_cohorts.py capture <raw-json-files> --inputs inputs.json --nunit results.xml --reload-run reload-1 --reload-run reload-2 --reload-run reload-3 --reload-results-name lifecycle-results.xml --output capture.json
python tools/workload_cohorts.py compare baseline.json capture.json --output comparison.json
```

Without every declared variant and the three reloads, capture fails unless `--allow-partial` is supplied; partial reports remain explicitly marked. Comparison requires the same cases, workload manifest, harness, hardware, OS, Editor/backend and verified output hashes. Source revisions may differ intentionally. A changed workload, reordered/partial output, refused operation, stale native input, missing evidence or invalid timing cannot be counted as a speedup. Record comparisons only after serialized runs on an otherwise comparable host; do not interpret concurrent test load as a product regression.

## Measurement meaning

Each synchronous native case records one first call with a warm Library and six warm samples. Preparation is outside timing; query, materialization and correctness assertions are inside it. The first call is not a cold filesystem/Editor measurement. Shared reads and artifact bursts retain their existing interleaved AABB controls and sample counts. Their output verification is described by the original experiment producer.

Reports separate throughput, median/p95, synchronous main-thread work sections, native allocation bytes, Python traced peak bytes and forced-GC managed-heap deltas. These are different quantities. Heap deltas may be negative and do not establish a leak or measure process RSS. Native allocation uses a separate untimed ProfilerRecorder pass; unavailable or truncated recording remains unknown. Whole-Editor frame stalls, asynchronous worker allocations and actual reload latency are currently unmeasured (`null`), never zero. UI ticks are an isolated activity-view workload, not a rendered multi-hour Editor session.

Wall-clock regressions start as advisory: a warm median increase must exceed both 20% and 5 ms. Correctness assertions and causal counts remain hard requirements. Preserve raw samples and identities for review; this tool does not automatically tune product limits or reject large user operations.
