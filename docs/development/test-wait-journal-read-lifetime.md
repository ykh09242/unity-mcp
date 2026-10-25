# Test wait journal read-buffer lifetime experiment

**Decision: both candidates rejected.** Production is restored byte-for-byte to
beta. Shorter input lifetimes consistently reduced large-record peak memory, but
the near-ceiling container workload repeatedly took longer, including process
CPU. A raw-only diagnostic preserved that tradeoff. No runtime optimization or
source-pin update is delivered.

## Design and adoption gate

The candidate shortens two temporary input-buffer lifetimes in `WaitJournal._read`.
The original implementation retains raw UTF-8 bytes and decoded JSON text while
Pydantic parses the record and ordinary-result admission validates its graph.
The candidate releases raw bytes after bounded text scanning, then releases the
text after parsing. It retains fresh disk reads for every operation.

Candidate A implements both releases. Candidate B removes only the long-lived raw
bytes local and retains decoded text until return. Their reproducible patches are
[candidate A](../benchmarks/test-wait-journal-read-lifetime-a.patch) and
[raw-only B](../benchmarks/test-wait-journal-read-lifetime-raw-only.patch).

This differs from the rejected [projection-sharing experiment](test-wait-journal-cost.md).
It leaves Pydantic projection and custom serializers, JSON encoding and exact
committed bytes, writer buffering, admission, limits, quotas, atomic replacement,
fsync, security checks, ownership, failure cleanup and cancellation unchanged.
It adds no persistent cache, dependency or speculative fast path.

Three alternatives were considered before measurement:

1. Shorten read-buffer lifetimes: selected because large documents currently have
   redundant simultaneous representations with no later use.
2. Batch writer chunks: deferred because it introduces a new buffer and changes
   the timing of byte/quota checks and encoding failures.
3. Consolidate repeated startup reads: deferred because reuse can change the
   observation/order of corruption, retention, quota and restart recovery.

The predeclared gate requires three independent paired blocks with alternating
source order. A reproducible memory improvement must accompany no material
cleanup-inclusive latency or CPU regression beyond baseline/baseline noise.
Repeated regressions above 3%, or a single observation above 5%, require
investigation. Those triggers do not authorize a smaller real regression.
Absolute small differences and Windows CPU-clock resolution must remain visible.
Missing evidence is not evidence of equivalence.

## Measurement contract

[`test_wait_journal_read_lifetime_cost.py`](../../tools/experiments/test_wait_journal_read_lifetime_cost.py)
reuses the previous experiment's deterministic payloads and complete byte-checked
journal scenario, with a new worker and measurement boundary. Each sample has a
fresh process and private synthetic storage. Imports, warmup, payload construction
and initial payload admission precede measurement. The measured lifecycle includes
create, admission, terminal write, fsync/replace, close, reopen, full result
retrieval, complete-document hashing and result/accounting assertions.

Total wall/process CPU also include payload release, deletion of private journal
storage and forced cyclic GC. Lifecycle and final-cleanup wall/CPU are reported
separately. Trace and native Windows RSS passes are separate from latency passes;
they record peak/current/post-cleanup allocation, working set and GC collections.
RSS high water includes imports and payload construction, so it is not a precise
isolated allocation measure. Timing excludes process startup and payload
construction. Forced GC is a conservative, explicit cleanup cost rather than a
claim about when normal application GC occurs.

The cases cover 1 KiB, 4 MiB and near-64 MiB scalar/container results; late
subclass fallback and aliased containers; cancellation; terminal replacement
failure; and cancellation replacement failure. Every paired child checks the
complete committed-document hash and unchanged dependency/helper hashes. Raw
rows preserve unfavorable samples. The previous experiment's data remain
historical evidence and are not reused as new measurements.

## Correctness evidence before measurement

The strict focused lifecycle suite passed 41 tests against candidate A and again
against restored beta, with two native POSIX permission tests skipped on Windows.
New regression cases replace committed bytes after a
successful retrieval and verify current results or fail-closed malformed
UTF-8/JSON behavior. Existing tests cover exact bytes, Unicode, nulls, numbers,
container/key order, tuple/model/subclass fallbacks, extension serializers,
ceiling-sized payloads, ownership, deadlines, quota, failure and cancellation.
Untimed readiness passes all eleven baseline/candidate scenario pairs.

The source base is beta `233957665fa9d23de64714532c82f3ca9350c776`.
Baseline source SHA-256:
`8c6a9fd1666a27dd0667e959f9a0e34b91f020c882fd75d5f6a04bb7035c239e`.
Candidate source SHA-256:
`2950063ffe45293a70673e1b3d6376800548f2ed289d465d22b2ebd7c4905633`.
Raw-only B source SHA-256:
`c16c450495221d888398f719168e96888af81b68773200973a65dde590a1c87d`.

## Results

The exclusive Windows run used Python 3.14.6 and Pydantic 2.14.1. The
[portable evidence and invocation manifest](../benchmarks/test-wait-journal-read-lifetime-cost.json)
contains all 448 measured observations: 66 initial baseline/baseline controls,
330 candidate-A observations in three independent blocks, 12 contemporaneous
control/diagnostic observations, and 40 raw-only-B screen observations. Another
22 untimed readiness observations are explicitly separate. Every command exited
successfully and every pair matched complete document and dependency hashes.
Only location metadata is replaced with portable identifiers; all measurements,
source/helper hashes, adverse observations and original private artifact hashes
are retained. The measurement helper SHA-256 is
`5dd9e39bf7c38a425e05b977f2cbb9af368f32ddbf7e1b93d101aaad666a48df`.

### Import repair and historical runner provenance

The [exact measured runner snapshot](../benchmarks/test-wait-journal-read-lifetime-measured.py.txt)
retains that original hash. The current reproduction runner has SHA-256
`cd48b504cd201b6b388cc33c8b8364c9bcff595614a062272545b0700923d7db`.
Its only source change bootstraps the repository root for direct script execution
and imports the shared helper through `tools.experiments`. This fixes canonical
Pylint import resolution and supports module execution. The earlier local lint
command included an extra experiments search path that masked the error; the
canonical path reproduced `E0401` before this repair and passes afterward.

All function and class AST bodies match the measured snapshot, and the shared
scenario, response-limit and bootstrap helper hashes are unchanged. Import setup
runs before the measurement boundary. Script/module help, direct script and
file-based import from another working directory, and 22 new untimed children
covering all eleven baseline/candidate-A pairs pass. The manifest records those
new readiness observations separately. The original 448 measured observations
and original 22 readiness observations are unchanged; no performance samples were
rerun or relabeled after this import repair.

### Candidate A: complete lifecycle and cleanup

Wall values below are medians of three fresh children per arm per block, in
milliseconds. CPU values are medians across all three blocks; individual CPU
observations and undefined relative ratios remain in the raw evidence. Windows
CPU samples have 15.625 ms granularity here, which limits small-control inference.

| Case | Block 1 wall ms B/C | Block 2 wall ms B/C | Block 3 wall ms B/C | CPU medians ms B/C |
| --- | ---: | ---: | ---: | ---: |
| scalar-1k | 23.860 / 24.349 | 22.352 / 23.253 | 22.188 / 23.667 | 15.625 / 15.625 |
| scalar-4m | 85.647 / 84.466 | 82.701 / 86.480 | 82.921 / 82.418 | 62.500 / 62.500 |
| scalar-near64m | 959.736 / 927.819 | 940.565 / 960.818 | 915.634 / 941.496 | 781.250 / 781.250 |
| container-1k | 24.636 / 24.535 | 23.264 / 24.193 | 22.937 / 22.815 | 15.625 / 15.625 |
| container-4m | 663.697 / 659.899 | 639.953 / 649.958 | 644.274 / 639.703 | 625.000 / 625.000 |
| container-near64m | 2136.175 / 2181.664 | 2162.954 / 2201.907 | 2095.223 / 2113.351 | 1968.750 / 2000.000 |
| fallback-4m | 654.247 / 657.104 | 643.912 / 642.381 | 646.209 / 639.726 | 625.000 / 609.375 |
| aliased-4m | 647.457 / 647.735 | 638.153 / 639.271 | 638.453 / 636.134 | 625.000 / 609.375 |
| cancel | 26.354 / 25.921 | 25.107 / 25.774 | 25.236 / 25.151 | 15.625 / 15.625 |
| replace-failure | 28.768 / 28.266 | 27.280 / 28.744 | 27.424 / 26.975 | 15.625 / 15.625 |
| cancel-failure | 27.737 / 29.462 | 27.704 / 27.718 | 28.024 / 27.838 | 15.625 / 15.625 |

The near-ceiling container was slower in seven of nine matched pairs. Its wall
ratios, preserving block boundaries, were:

- Block 1: 1.0122, 1.0213, 0.9948.
- Block 2: 1.0180, 1.1089, 0.9649.
- Block 3: 1.0334, 1.0011, 1.0082.

The block median increases were 2.13%, 1.80% and 0.87%. The 10.89% adverse pair
also increased CPU by 10.4%. Final cleanup was about 16–24 ms and generally equal
or lower for the candidate; the increase occurred in the actual journal
lifecycle. Identical collection counts do not explain it. No concrete external
confound was demonstrated, and the pooled median cannot erase these results.

The scalar 1 KiB block medians rose 2.05%, 4.03% and 6.67%, or approximately
0.49, 0.90 and 1.48 ms. Small baseline/baseline controls were noisy: scalar 1 KiB
had a +7.99% pair, container 1 KiB reached +17.23%, and scalar 4 MiB ranged from
0.8028 to 1.1314. These controls limit attribution for small cases; they do not
establish that candidate latency is equal. Other adverse block values, including
replacement failure and cancellation failure, remain visible in the table and
raw rows.

### Candidate A: memory

Peak traced values below repeated exactly in all three independent memory pairs.
Post-cleanup traced retention matched within each case: 537 bytes for scalar
cases and 615 bytes for container/fallback/alias cases. This is temporary peak
reduction, with no demonstrated retained-memory improvement.

| Case | Peak traced bytes baseline | Candidate A | Reduction |
| --- | ---: | ---: | ---: |
| scalar-4m | 16,966,259 | 12,759,284 | 24.80% |
| scalar-near64m | 268,608,115 | 201,490,676 | 24.99% |
| container-4m | 23,718,387 | 19,566,973 | 17.50% |
| container-near64m | 275,375,601 | 208,298,365 | 24.36% |
| fallback-4m | 23,718,387 | 19,566,973 | 17.50% |
| aliased-4m | 23,718,321 | 19,566,909 | 17.50% |

Native RSS high water independently reproduced an approximately one-document
reduction: near-ceiling scalar baseline 373.57–373.78 MB versus candidate
306.54–306.74 MB; container baseline 394.58–395.00 MB versus candidate
327.63–328.17 MB (decimal MB). Small-case RSS differences were mixed and do not
establish a memory benefit. These process high-water values include import and
payload preparation, as disclosed above.

### Contemporaneous controls and raw-only diagnostic

Because the repeated heavy-case slowdown was larger than the initial heavy
baseline/baseline range, a bounded contemporaneous check ran three new same-source
pairs and then three unchanged candidate-A pairs for the near-ceiling container.
The fresh control wall ratios were 0.9670, 1.0184 and 0.9598; candidate-A ratios
were 1.0099, 1.0342 and 1.0448, with CPU ratios 1.0080, 1.0323 and 1.0488.
This diagnostic did not resolve the preservation concern.

Candidate B then retained decoded text until return to test whether its earlier
release introduced an unnecessary free-order cost. A bounded screen covered both
1 KiB controls and both near-ceiling shapes, with three timing pairs and separate
trace/RSS pairs. The near-ceiling container was slower in every pair: wall ratios
1.0328, 1.0191 and 1.0289; CPU ratio 1.016 in all three. Median wall time rose
from 2091.543 to 2157.244 ms (+3.14%). Scalar 1 KiB rose from 23.599 to 25.435 ms
with a noisy 1.2913 outlier; container 1 KiB and near-ceiling scalar medians fell
2.86% and 0.91%. The memory improvement remained: near-ceiling peak traces were
201,502,787 and 208,319,421 bytes for scalar and container.

Removing the early text release did not remove the heavy-case cost. No further
full matrix or speculative variants were run. The evidence supports rejection
under the requested preservation condition; it does not establish a universal
slowdown on every machine or workload. Native POSIX permissions/directory-fsync
were not exercised on this Windows host.

## Reproduction

Use a disposable checkout at the pinned base and the existing server interpreter;
keep normal checkout/runtime data unchanged. Copy the baseline journal source
before applying one of the patches above. Source and dependency/helper hashes
must match the manifest's measured or reproduction identity as appropriate.
Runtime output belongs in private scratch storage.

```powershell
$python = '<existing-server-python>'
$checkout = '<disposable-checkout-at-pinned-base>'
$evidence = '<private-scratch-directory>'
Copy-Item -LiteralPath "$checkout/Server/src/services/test_wait_journal.py" `
    -Destination "$evidence/baseline_test_wait_journal.py"
git -C $checkout apply '<experiment-checkout>/docs/benchmarks/test-wait-journal-read-lifetime-a.patch'
& $python '<experiment-checkout>/tools/experiments/test_wait_journal_read_lifetime_cost.py' `
    --repository $checkout --baseline "$evidence/baseline_test_wait_journal.py" `
    --candidate "$checkout/Server/src/services/test_wait_journal.py" `
    --scratch "$evidence/synthetic" --output "$evidence/readiness.json" --check --runs 1
```

After reserving exclusive CPU time, use the portable manifest's exact per-dataset
argument lists. The main candidate run uses `--runs 3 --rounds 3`; initial
baseline/baseline controls use `--runs 1 --rounds 3 --modes latency` and supply the
baseline source for both arms. Apply the raw-only patch to a separate clean
disposable checkout for that diagnostic. Do not combine concurrent test, build,
Unity or benchmark work with measured children.

For literal historical-runner reproduction, copy the measured `.py.txt` snapshot
over the runner path in a disposable experiment checkout and invoke it as a
script. Its historical bare helper import supports that invocation but fails
canonical lint and module execution. Use the repaired runner for current work;
its distinct hash remains visible in any new output.
