# Incremental reference analysis and large wrapper graphs

The analyzer keeps local call plans shared between callers. It binds arguments
while walking those plans with an explicit stack, rather than copying every
transitive expression into every wrapper summary. Unknown bindings collapse to
one symbolic state per method. Concrete state memoization is evictable; eviction
recomputes work and never truncates references, depth or valid input size.
Small unknown-input outcomes are shared as well, so visiting many call sites in
a deep chain does not repeat the entire chain for every symbolic input. Larger,
concrete or recursive outcomes stay on the lazy traversal path.

The index tracks reverse dependencies and candidate symbol lookups, including
missing and ambiguous names. Adding, deleting or changing a declaration updates
its symbols and affected callers. A newly added symbol can therefore invalidate
a formerly unresolved caller. Unchanged immutable facts remain in the scoped
index. Reconciliation reads a compact document manifest, then hydrates changed
facts and selected publications in pages; it does not JSON-decode every document
after a one-file edit. Rule changes rebuild the index, and catalog changes replace
catalog data at its revision boundary.

Small analyses keep the existing grouped publication path. Large owners stream
events into private SQLite staging in 256-row batches. Generation and worker
fences protect every batch; an atomic final promotion replaces visible evidence.
Failure or cancellation preserves the previous visible evidence as stale and
removes private staging. Restart/acquisition removes abandoned workers' staging.
The batch and memo sizes are working buffers, not maximum job or result sizes.
Query pagination continues to expose all committed results.

Index construction, binding, traversal and publication check cancellation. An
interrupted in-place index update is discarded. The cache belongs to one scoped
reconciler, can be explicitly cleared while idle, and expires after five minutes
without analysis use. Expiry is checked at reconciliation boundaries; active work
has no cache-expiry deadline. Durable facts allow reconstruction after eviction.

## Component measurements

Measured on Windows, Python 3.14.6, using owned synthetic facts and the same helper
against beta `c08b8c11` and the incremental candidate. `tracemalloc` is enabled
during each measured operation. No Unity process, importer, database manifest
read, or transport latency is included in these component timings.

| Workload | Before | After | Peak additional Python allocation before / after |
| --- | --- | --- | --- |
| Build a depth-13 branching wrapper index | 1.113 s | 0.00154 s | 25,982,614 / 41,953 bytes |
| Analyze that wrapper with unknown inputs | 0.401 s | 0.000819 s | 4,938,024 / 9,760 bytes |
| Edit one constant among 10,000 documents plus its caller, median of three | 0.199 s | 0.000290 s | about 7.9 MB / 9.4 KB |
| Edit one constant among 50,000 documents plus its caller, median of three | 1.066 s | 0.000313 s | about 42.2 MB / 9.4 KB |

For each changing constant, the affected set includes both the declaration and
its caller; full analysis output digests match the control. The wrapper output
digests also match. These figures compare a full rebuild with an incremental
index update, not complete edit-to-query latency. The manifest still performs
an O(document count) metadata scan, and a warm index retains immutable facts until
eviction. Large concrete fanout still requires work proportional to its output;
streaming avoids retaining the whole publication in Python memory.

The existing 50,000-caller/250,000-asset workload also verified all 50,000 edges
and 50,001 affected owners. A separate single cold run measured index creation at
1.061 / 1.117 s and resolution at 0.693 / 0.814 s (control / candidate), while
reverse-dependency fanout fell from 0.040 / 0.018 s. This is an incremental-update
and peak-allocation improvement, not a claim that every cold path is faster.

Regression coverage includes 1,100-level call chains, 512 distinct concrete keys,
dynamic inputs with constant side loads, missing/duplicate/new symbols, type
shadowing, cancellation, atomic promotion, and cache eviction/reconstruction.

Reproduce with the installed development Python environment:

```sh
python tools/experiments/reference_graph_incremental.py /absolute/path/to/unity-mcp /owned/output/control.json --analysis-ref c08b8c11
python tools/experiments/reference_graph_incremental.py /absolute/path/to/unity-mcp /owned/output/candidate.json
```

The control option loads the analyzer from the selected local Git revision; use
only a trusted revision. No extra package, service, compiler or download is needed.
