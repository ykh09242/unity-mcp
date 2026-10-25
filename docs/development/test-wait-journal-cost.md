# Test wait journal projection experiment

**Decision: rejected.** The production journal retains the exact beta implementation.
The candidate did not demonstrate a complete-lifecycle latency or memory improvement,
and a supported large fallback was slower in each of its three paired samples.
This is a decision not to adopt the candidate; one screen does not establish a
universal regression. No independent adoption repeat was needed or run.

The candidate reused `ResponseCopyPlan.shareable` from the required ordinary-result
admission, retained the bounded metadata projection, and attached eligible builtin
payloads without a second recursive Pydantic projection. Models, tuples, subclasses,
repeated mutable containers and record extensions kept the original projection.
There was no persistent cache, new dependency, smaller quota, reduced validation or
new disk I/O. The rejected source is retained as
[`test-wait-journal-projection.patch`](../benchmarks/test-wait-journal-projection.patch).

The added byte-contract tests pass against both exact beta and the candidate,
covering nulls, Unicode, numbers, container/key order, tuple/model/subclass fallbacks,
extension serializers and refusal cleanup. The focused lifecycle suite passed
38 tests with two native POSIX permission tests skipped on Windows. Another 76
transport tests passed on the candidate. Pydantic's
[serialization reference](https://docs.pydantic.dev/latest/concepts/serialization/)
describes the projection and exclusion behavior these tests preserve.

## Results

The exact base was `a3efdeb8ae63ad5bf80ff63936f64398e17058d9`, with Python 3.14.6
and Pydantic 2.14.1 on Windows. The exclusive screen completed 110 fresh children
in 137 seconds: three paired latency rounds and separate trace/RSS pairs across
all eleven cases. Every pair matched complete committed-document hashes and
unchanged dependency hashes. The
[sanitized raw screen](../benchmarks/test-wait-journal-cost-screen.json) preserves
all 110 observations, including unfavorable samples, source hashes and GC counts.
It contains no absolute checkout paths.

The 4 MiB late-fallback case was slower in all three pairs (+2.32%, +4.34%, +1.77%);
its process CPU median rose from 609.375 to 656.250 ms. Large exact-builtin cases
showed no reliable complete-lifecycle gain. Peak traced memory was essentially
unchanged, and post-GC traced retention was equal. The near-ceiling cases contain
large wall-time outliers, which remain in the evidence and are not attributed to
a particular implementation cause from this screen alone.

## Latency and CPU

Values are baseline / candidate. Wall values: median [min,max] milliseconds.
Pair ratios use matched round/source observations; ratios above 1 are slower.
CPU values are medians; Windows process CPU samples have 15.625 ms granularity here.

| Case | Baseline wall ms | Candidate wall ms | Paired wall ratios (all 3) | CPU medians ms B/C |
|---|---:|---:|---|---:|
| scalar-1k | 17.625 [16.862,17.666] | 18.276 [17.002,18.472] | 1.0345, 1.0955, 0.9647 | 0.000 / 0.000 |
| scalar-4m | 78.089 [76.275,79.204] | 77.697 [77.057,128.969] | 1.6283, 0.9868, 1.0186 | 46.875 / 46.875 |
| scalar-near64m | 993.708 [946.144,1031.047] | 1061.827 [949.750,3903.844] | 4.1261, 1.0299, 0.9558 | 796.875 / 781.250 |
| container-1k | 17.724 [17.646,700.782] | 18.212 [18.043,653.490] | 0.9325, 1.0321, 1.0180 | 15.625 / 15.625 |
| container-4m | 650.073 [643.250,1551.646] | 649.794 [644.600,1652.398] | 1.0649, 0.9916, 1.0102 | 625.000 / 609.375 |
| container-near64m | 2126.348 [2115.238,2187.833] | 2226.709 [2122.441,7352.068] | 3.4576, 0.9701, 1.0527 | 1953.125 / 1953.125 |
| fallback-4m | 643.495 [631.981,649.724] | 661.211 [646.659,671.436] | 1.0232, 1.0434, 1.0177 | 609.375 / 656.250 |
| aliased-4m | 643.656 [631.870,663.354] | 653.698 [653.340,655.505] | 0.9882, 1.0340, 1.0156 | 640.625 / 640.625 |
| cancel | 20.017 [19.870,20.639] | 20.851 [19.478,20.991] | 1.0102, 1.0564, 0.9731 | 0.000 / 0.000 |
| replace-failure | 21.870 [21.826,22.477] | 23.242 [22.543,51.803] | 1.0328, 2.3686, 1.0340 | 15.625 / 15.625 |
| cancel-failure | 22.374 [22.323,23.250] | 22.631 [21.387,23.496] | 1.0115, 1.0525, 0.9199 | 0.000 / 0.000 |

## Memory

Traced peak/current/postGC and native RSS are from separate non-latency passes.
Values are baseline / candidate; trace current is before the explicit final GC.

| Case | Trace peak MiB B/C | Current KiB B/C | PostGC KiB B/C | RSS peak MiB B/C | RSS before MiB B/C | RSS after MiB B/C | RSS postGC MiB B/C |
|---|---:|---:|---:|---:|---:|---:|---:|
| scalar-1k | 0.519 / 0.519 | 22.564 / 22.689 | 1.626 / 1.626 | 36.332 / 36.496 | 36.328 / 36.359 | 36.328 / 36.492 | 36.328 / 36.492 |
| scalar-4m | 16.180 / 16.180 | 26.509 / 26.634 | 1.626 / 1.626 | 56.465 / 56.406 | 40.312 / 40.258 | 40.449 / 40.387 | 36.445 / 36.383 |
| scalar-near64m | 256.164 / 256.165 | 26.509 / 26.634 | 1.626 / 1.626 | 356.520 / 356.641 | 100.387 / 100.500 | 100.516 / 100.637 | 36.516 / 36.637 |
| container-1k | 0.521 / 0.520 | 25.578 / 26.672 | 1.671 / 1.671 | 36.312 / 36.680 | 36.305 / 36.539 | 36.309 / 36.676 | 36.309 / 36.676 |
| container-4m | 22.619 / 22.619 | 29.371 / 29.762 | 1.671 / 1.671 | 72.938 / 73.051 | 42.566 / 42.645 | 50.312 / 48.117 | 41.281 / 40.082 |
| container-near64m | 262.618 / 262.618 | 29.371 / 29.762 | 1.671 / 1.671 | 376.363 / 376.988 | 103.508 / 103.641 | 110.688 / 111.078 | 40.910 / 40.047 |
| fallback-4m | 22.619 / 22.619 | 29.371 / 29.371 | 1.671 / 1.671 | 73.012 / 73.066 | 42.566 / 42.602 | 50.094 / 50.039 | 41.059 / 40.988 |
| aliased-4m | 22.619 / 22.619 | 29.371 / 29.371 | 1.671 / 1.671 | 72.977 / 72.922 | 42.637 / 42.555 | 50.121 / 49.934 | 40.832 / 40.887 |
| cancel | 0.520 / 0.520 | 14.181 / 14.181 | 1.626 / 1.626 | 36.676 / 36.469 | 36.547 / 36.465 | 36.672 / 36.465 | 36.672 / 36.465 |
| replace-failure | 0.529 / 0.529 | 21.876 / 22.001 | 3.176 / 3.176 | 36.320 / 36.336 | 36.312 / 36.332 | 36.316 / 36.332 | 36.316 / 36.332 |
| cancel-failure | 0.527 / 0.527 | 19.879 / 19.879 | 3.176 / 3.176 | 36.422 / 36.543 | 36.293 / 36.414 | 36.418 / 36.539 | 36.418 / 36.539 |


## Method and limitations

Six ordinary-result cases cover scalar-heavy and container-heavy payloads of
1 KiB, 4 MiB and 64 MiB minus 4 KiB within the unchanged admission bounds. Two
approximately 4 MiB cases put a supported list subclass or repeated mutable
container at the end of the graph, exposing tracking cost when a late fallback
still needs the full projection. Three small controls cover cancellation and
injected replacement failures during completion and cancellation.

Each sample uses a fresh process and private synthetic journal. Source order
alternates by round and case. Imports and a small real journal lifecycle warm up
before payload construction; both process startup and payload construction are
outside timing. The observed operation includes create, required admission,
terminal write, fsync/replace, close, reopen and full retrieval, plus hashing and
result/accounting checks. Wall time uses `perf_counter_ns`; process CPU uses
`process_time_ns`. CPU medians of zero in small controls reflect the observed
clock granularity; an undefined relative CPU ratio is reported as JSON null.

Latency and CPU end before the final payload deletion and forced cyclic GC.
Consequently this experiment does **not** establish a cleanup-inclusive timing
win. Admission's closure cycles can defer work until cyclic GC. Current traced
allocation and post-GC retained allocation, RSS before/after/post-GC, and collection
counts are recorded in separate non-latency passes. RSS peak includes process and
payload-construction high water. The original measured harness completed every
child but encountered a zero-denominator error in its final summary; raw rows
were preserved. A summary-only guard was verified by replaying all preserved
rows with no measured work, leaving absolute values unchanged.

There is one screen, not two independent runs. There is no native POSIX permission
or directory-fsync evidence from this Windows run. These limits reinforce the
rejected adoption decision; they are not grounds to claim a proven speedup or a
universal slowdown.

## Reproduction in a disposable checkout

Use the existing server interpreter. Prepare a **disposable checkout** of the
pinned beta commit above; keep the normal checkout unchanged. From the checkout
containing this experiment, substitute the three local paths below. Copy the
original baseline before applying the candidate patch, and keep runtime output
private because it includes actual source paths.

```powershell
$python = './Server/.venv/Scripts/python.exe'
$experiment = (Get-Location).Path
$checkout = '<disposable-checkout-at-pinned-beta>'
$evidence = '<private-scratch-directory>'
New-Item -ItemType Directory -Path $evidence -Force | Out-Null
Copy-Item -LiteralPath "$checkout/Server/src/services/test_wait_journal.py" `
    -Destination "$evidence/test_wait_journal.py"
git -C $checkout apply "$experiment/docs/benchmarks/test-wait-journal-projection.patch"
& $python "$experiment/tools/experiments/test_wait_journal_cost.py" `
    --repository $checkout `
    --candidate "$checkout/Server/src/services/test_wait_journal.py" `
    --baseline "$evidence/test_wait_journal.py" `
    --scratch "$evidence/synthetic" `
    --output "$evidence/readiness.json" --check --runs 1
```

`--check` performs untimed parity checks. For the same bounded screen, reserve an
exclusive CPU window, remove `--check`, keep `--runs 1`, and choose a fresh output
filename. The default `--runs 2` is available for a proposed adoption requiring an
independent repeat. Do not compare overlapping test/build/benchmark workloads.
The baseline snapshot and both unchanged dependencies must match the recorded
pinned hashes; complete output guards are mandatory. The runner fixes only the
summary's undefined-ratio handling relative to the measured harness.
