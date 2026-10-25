# Reference graph path discovery cost

Adopted as a bounded discovery peak-allocation, GC and CPU optimization after
independent correctness and diagnostic review. Representative large-query wall
time and p95 improved in the repeated measurements below. This does not promise
an improvement for every small query, every maximum-page sample, or generic
retained memory.

Path discovery uses the existing SQLite breadth-first queue and parent links. Its
page limit bounds examined relationships, including duplicates and cycles. Once
the target is found, a separate bounded loop materializes the selected route in
target-to-start order.

Previously discovery retained a complete evidence dictionary for every examined
relationship, added `depth` and `via`, then discarded the entire list in path
mode. The candidate counts examined relationships independently and immediately
discards discovery evidence in path mode. Impact still materializes every
examined relationship. Route reconstruction is unchanged.

The candidate deliberately still calls the same `_evidence` decoder for each
examined relationship. Malformed JSON and nonmapping payload/provenance values
must fail at the same point, even outside the eventual route. Skipping these
decodes would change the contract and is outside this experiment. There are no
SQL, schema, transaction, cache, traversal budget, or production limit changes.

## Reproducible experiment

`tools/experiments/reference_graph_path_cost.py` creates disposable, scoped SQLite
fixtures under a caller-supplied scratch directory. It loads separately frozen
baseline and candidate query sources against the same store implementation.
Baseline is beta `a3efdeb8ae63ad5bf80ff63936f64398e17058d9`; its query source SHA256
is `5cc20bf5c551e32912b2a2614dfa21b965b7d9ceacd4475abba121276c76c72e`.

Verification compares every public field on every page, including opaque ids and
continuation cursors, by supplying the same deterministic query id in isolated
databases. It restarts the query object before every continuation and replays
each consumed cursor, including the terminal response. Route length is also
checked against the known fixture. Corruption cases compare exception type and
message and the committed traversal state after failure. Temporary transcripts
are compared line by line; persistent JSONL contains their complete-page digest,
page count, fixture parameters, and source hashes.

The representative screen is intentionally smaller than the supported matrix:

| Case index | Graph | Nodes | Page size | Provenance padding | Purpose |
| --- | --- | ---: | ---: | ---: | --- |
| 1 | Broad unsuccessful, outgoing | 1,000 | 1 | 48 bytes | Small-page work accounting |
| 3 | Broad unsuccessful, outgoing | 1,000 | 100 | 48 bytes | Normal page control |
| 17 | Deep chain, outgoing | 1,000 | 1,000 | 48 bytes | Full route reconstruction |
| 22 | Equal routes, cycles, parallel evidence, incoming | 1,000 | 1,000 | 48 bytes | BFS ordering and incoming traversal |
| 24, 25 | Corrupt payload / provenance | 32 | 1 | 48 bytes | Failure after committed progress |
| 29 | Broad unsuccessful, outgoing | 50,000 | 1,000 | 8,192 bytes | Retention pressure and queue draining |
| 41 | Broad late target, outgoing | 250,000 | 1,000 | 8,192 bytes | Large complete search with late discovery |

The tool additionally supports both directions, page sizes 1/100/1,000, 50,000
node chains, 250,000 node unsuccessful searches, and 1 KiB provenance. Large
provenance is stored once on the broad root and repeated by the unchanged SQL
join. This produces realistic per-page decoded retention pressure while keeping
fixture storage proportional to node/edge count instead of allocating several
gigabytes of redundant provenance. This is a fixture choice, not a production
cap. The screen does not claim coverage of the full Cartesian product.

Run verification before acquiring the exclusive performance measurement lease:

```powershell
$python = './Server/.venv/Scripts/python.exe'
$baseline = './.work/path-cost/baseline/queries.py' # Save the exact beta source here first.
& $python tools/experiments/reference_graph_path_cost.py `
  --baseline-source $baseline --scratch .work/path-cost/scratch `
  --output .work/path-cost/verify-screen.jsonl `
  --case-index 1 --case-index 3 --case-index 17 --case-index 22 `
  --case-index 24 --case-index 25 --case-index 29 --case-index 41
```

After the lease is granted, screen CPU with three paired runs in alternating
baseline/candidate order. Existing verification rows may be reused only when the
case and both query source hashes match; the caller must also preserve the
fixture definition when reusing evidence.

```powershell
& $python tools/experiments/reference_graph_path_cost.py `
  --baseline-source $baseline --scratch .work/path-cost/scratch `
  --output .work/path-cost/cpu-screen.jsonl `
  --verified-path .work/path-cost/verify-screen.jsonl `
  --measure --passes cpu --repeats 3 `
  --case-index 1 --case-index 3 --case-index 17 --case-index 22 `
  --case-index 29 --case-index 41
```

The current runner also records high-resolution per-page wall time and complete
continuation-loop wall/CPU time. Use `--baseline-control` for a paired comparison
between independent copies running the identical baseline source. Such rows use
the `baseline_control` variant and record the actual measured source hash. This
control estimates environmental variation; it does not establish candidate
performance. The initial runner lacked wall telemetry, so its page CPU
percentiles must not be interpreted as request latency.

Run traced memory, process memory, and GC in separate child-process passes using
`--passes trace process gc --repeats 1`, initially on cases 29 and 41. Repeat any
winning CPU result independently before accepting it. Each measurement process
starts with a fresh database copy; imports, fixture construction, and copying are
outside query timing. Raw rows retain per-page CPU samples plus total CPU,
p50/p95/max page CPU, traced peak/retained bytes, Windows working set/private
bytes and peak working set, and per-generation GC statistic deltas. Query CPU
and memory passes are separated so tracemalloc does not distort the CPU result.
Process peak includes interpreter startup and therefore must be interpreted
alongside start/end working set and private memory. OS caches are warmed by
verification; this is not a cold-storage loading benchmark.

## Verification and acceptance

Focused query/provenance/service/operations tests passed: 90 tests. New
regressions cover incoming/outgoing malformed and nonmapping payload/provenance
outside the final route and committed continuation state after a decode failure.
The changed source/test/benchmark scope passed the repository formatter check,
correctness Pylint configuration, and `git diff --check`.

The first exclusive screen completed with the candidate query SHA256
`cdbd0e3d7df47d3261f815adcaadcd5cd00dba0e2caf836749c386ccd654fb9e`
and runner SHA256
`10abbb9286c9b6e1ace0d7ef570cd98f4fcbef2d3e73ada3cc8e808d2d33ab8e`.
Three alternating paired CPU samples produced these medians:

| Case | Baseline CPU ms | Candidate CPU ms | Change |
| --- | ---: | ---: | ---: |
| 1: 1K unsuccessful, page 1 | 5,640.625 | 6,015.625 | +6.65% |
| 3: 1K unsuccessful, page 100 | 125.000 | 140.625 | +12.50% |
| 17: 1K chain | 93.750 | 93.750 | 0% |
| 22: 1K incoming routes | 15.625 | 15.625 | 0% |
| 29: 50K unsuccessful | 3,859.375 | 3,734.375 | -3.24% |
| 41: 250K late target | 8,171.875 | 7,062.500 | -13.58% |

The small-page regression is present in all three case 1 pairs; it is not omitted
from the result. Case 29 includes one unfavorable pair. Windows CPU samples were
quantized in 15.625 ms steps, which limits short-case and per-page interpretation.
The initial screen did not record wall latency.

Separate single-pair memory and GC passes recorded:

| Metric | 50K baseline | 50K candidate | 250K baseline | 250K candidate |
| --- | ---: | ---: | ---: | ---: |
| Traced peak bytes | 11,867,100 | 4,783,564 | 11,867,989 | 4,784,077 |
| Traced retained bytes | 104,370 | 104,402 | 111,024 | 114,400 |
| Process peak working set bytes | 38,813,696 | 32,432,128 | 39,628,800 | 38,858,752 |
| Final process private bytes | 18,378,752 | 17,276,928 | 19,918,848 | 18,427,904 |
| GC collections by generation | 46 / 4 / 0 | 0 / 0 / 0 | 229 / 20 / 1 | 0 / 0 / 0 |

No retained-memory improvement is claimed: the candidate retained 32 and 3,376
additional traced bytes in those single observations. The large traced-peak
reduction and lower GC activity need independent confirmation alongside wall
latency and baseline/baseline controls. Adoption was pending at that stage.
Accept only reproducible improvements with unchanged page semantics and no
material large-query latency or memory regression. Preserve unfavorable raw
measurements and report unstable results as inconclusive.

## Independent wall-time repeat and bounded diagnostic

An independent three-pair repeat used runner SHA256
`1c194cabc557ad9351ae43671f43cb0b3d101b25e9a2c11c41836de9781078d4`
with the same frozen baseline and candidate. Median complete-loop wall times were:

| Case | Baseline ms | Candidate ms | Change |
| --- | ---: | ---: | ---: |
| 1: 1K unsuccessful, page 1 | 9,671.280 | 9,329.559 | -3.53% |
| 3: 1K unsuccessful, page 100 | 153.027 | 157.103 | +2.66% |
| 17: 1K chain | 93.807 | 89.370 | -4.73% |
| 22: 1K incoming routes | 13.557 | 13.531 | -0.19% |
| 29: 50K unsuccessful | 4,312.059 | 4,001.607 | -7.20% |
| 41: 250K late target | 9,176.918 | 8,169.134 | -10.98% |

The 50K paired wall changes ranged from -12.23% to +5.38%, including an unfavorable
pair. The 250K pairs ranged from -15.69% to -3.08%. Case 3 was slower in all three
pairs, from +1.73% to +5.95%; its identical-baseline controls also varied widely,
including a preserved +382.18% total-wall outlier. This is not evidence of a
reliable small-query speedup. Case 1 CPU changed by +1.23% in this repeat, compared
with +6.65% initially, while its wall-time pairs all improved.

The 250K median per-run maximum page time worsened from 737.773 to 921.472 ms
(+24.90%) in this repeat. Its identical-baseline controls also had large maxima,
up to 1,422.967 ms. Rather than discard either result, one final bounded diagnostic
interleaved three baseline/candidate pairs with three independent pairs running
the identical baseline source. It used runner SHA256
`3441587791bac79ba4cc31ae3981b2df46135cb2f40c70765d2c3a2fd81b68b6`.
The same frozen 250K case was the only timed workload; no production changes or
additional matrix expansion were made.

| Final diagnostic metric | Baseline median | Candidate median | Change |
| --- | ---: | ---: | ---: |
| Complete-loop wall ms | 8,592.850 | 8,097.949 | -5.76% |
| Query CPU ms | 6,875.000 | 6,421.875 | -6.59% |
| Page wall p95 ms | 39.3731 | 35.9195 | -8.77% |
| Terminal-page wall ms | 709.0860 | 668.5451 | -5.72% |
| Terminal-page CPU ms | 421.875 | 406.250 | -3.70% |
| Per-run maximum page wall ms | 722.7580 | 668.5451 | -7.50% |
| Nonterminal page wall p95 ms | 38.0775 | 35.8363 | -5.89% |
| Nonterminal maximum page wall ms | 316.7321 | 431.4333 | +36.21% |

All three diagnostic complete-loop wall pairs improved (-8.54% to -3.43%), as did
all terminal-wall pairs (-26.97%, -4.47%, -2.34%). The overall maximum had one
unfavorable +5.30% pair. Nonterminal maxima remained noisy and unfavorable by the
median: baseline samples were 48.1668, 722.7580 and 316.7321 ms; candidate samples
were 431.4333, 761.0995 and 220.1337 ms. Interleaved identical-baseline controls
had nonterminal maxima of 563.9132, 751.3793 and 415.6563 ms versus 320.0767,
312.6874 and 313.3812 ms. Their terminal-wall paired changes ranged from -3.99%
to +39.37%, and complete-loop wall changes ranged from -9.07% to +2.33%.
These observations support representative whole-query and p95 improvements;
they do not establish a worst-case latency guarantee or eliminate every noisy
maximum-page regression.

One separate baseline/candidate trace pair kept the original end-current and
peak measurements, then released the completed response and the same query,
store, type and cursor locals before explicitly collecting garbage. Cleanup and
collection were outside all query timing. The post-cleanup measurements below
are new observations, not a reinterpretation of the earlier retained field:

| Trace diagnostic metric | Baseline bytes | Candidate bytes |
| --- | ---: | ---: |
| Original peak | 11,867,985 | 4,784,014 |
| Original end-current, response still owned | 111,024 | 114,341 |
| Current after local-reference release | 100,968 | 104,285 |
| Current after explicit GC | 7,336 | 7,277 |

Both runs collected 67 objects during the explicit diagnostic collection. The
3,317-byte end-current difference did not persist after identical cleanup and
collection; no sustained traced-memory growth was demonstrated in this case.
The production query does not force GC. There is no separately measured
post-GC peak: the original peak remains the peak of the whole query pass.

The bounded evidence supports reduced discovery peak allocation and GC work,
repeated favorable CPU and representative large-query timing, and no persistent
traced-memory increase after cleanup. Keep the small-case regressions and noisy
maxima visible when assessing adoption; do not generalize this to an across-the-
board latency or retained-memory improvement.

## Published evidence and reproduction

The [portable provenance manifest](../benchmarks/reference-graph-path-cost-20261025/manifest.json)
identifies the exact baseline, candidate, runner versions, phase boundaries and
all raw cohorts. The evidence directory contains every performance sample used
in the decision, including unfavorable rows and outliers:

- [Initial CPU screen](../benchmarks/reference-graph-path-cost-20261025/cpu-screen.jsonl)
  and [separate memory/GC screen](../benchmarks/reference-graph-path-cost-20261025/memory-screen.jsonl).
- [Independent CPU/wall repeat](../benchmarks/reference-graph-path-cost-20261025/cpu-wall-repeat.jsonl),
  [identical-baseline controls](../benchmarks/reference-graph-path-cost-20261025/baseline-controls.jsonl),
  and [trace repeat](../benchmarks/reference-graph-path-cost-20261025/trace-repeat.jsonl).
- [Interleaved terminal diagnostic](../benchmarks/reference-graph-path-cost-20261025/terminal-diagnostic.jsonl)
  and [separate cleanup diagnostic](../benchmarks/reference-graph-path-cost-20261025/cleanup-diagnostic.jsonl).
- [Full-page verification](../benchmarks/reference-graph-path-cost-20261025/verify-screen.jsonl),
  [initial summary](../benchmarks/reference-graph-path-cost-20261025/initial-summary.json),
  [repeat/control summary](../benchmarks/reference-graph-path-cost-20261025/followup-summary.json),
  and [diagnostic summary](../benchmarks/reference-graph-path-cost-20261025/diagnostic-summary.json).

Runner snapshots are preserved beside the data as UTF-8 LF text. To reproduce a
historical phase in an isolated checkout, copy the selected snapshot back to
`tools/experiments/reference_graph_path_cost.py` and use its recorded query source
identities and case parameters. Initial verification did not record a runner
hash at execution time: its snapshot is explicitly labeled as a later
reconstruction with five metadata-only additions removed. This provenance limit
does not change the recorded query source hashes or page comparisons.

With the diagnostic runner, the final bounded CPU command adds `--diagnostic
--case-index 41 --passes cpu --repeats 3` to the common measurement arguments.
This produces twelve interleaved samples: three candidate pairs and three
independent identical-baseline pairs. The separate trace command uses
`--diagnostic --case-index 41 --passes trace --repeats 1`, producing two samples.
The CPU command records terminal and nonterminal metrics from the existing page
timers; the trace command adds explicit cleanup only after the original
end-current/peak measurement. Neither command changes production SQL, schema,
traversal behavior or GC policy.

Historical summary recommendation fields are retained as recorded, including
their then-pending status. The final decision is adoption with the bounded claims
at the start of this report, based on the complete evidence and independent
review rather than removal of unfavorable samples.
