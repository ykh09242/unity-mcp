# Component paging alternative experiments

Both additional runtime algorithms were rejected. Paging production source is
restored exactly to baseline SHA256
`1de68ed4a6b11ebcb0fdb99b4ba8c069334d82a9be7338eb959fdcd152da7198`,
whose measured input commit was `233957665fa9d23de64714532c82f3ca9350c776`.
This unit delivers one meaningful regression case, opt-in native helpers and
complete positive/negative evidence. It ships no runtime paging optimization.
Server journal work is a separate unit.

| Experiment | Outcome | Complete evidence |
| --- | --- | --- |
| PC04: unchanged production versus same-assembly frozen baseline | Ordinary property timing/heap asymmetry persisted; no candidate installed under this protocol | [Control report and 1440 rows](../benchmarks/component-paging-alternatives-20261025/control-and-rejection-report.md) |
| PC05: retain Unity array, compact live entries and directly index pages | Reject first matched source screen: 256/page100/property mixed/wire sampled heap +8.17% mean, with every candidate repeat above the baseline maximum | [Rejection report and 2880 rows](../benchmarks/component-paging-alternatives-20261025/control-and-rejection-report.md) |
| PC06: retain the exact full filtered list and remove only the page list | Reject first matched source screen: 2048/page25/property mixed/construction wall +10.39% mean and +8.98% median, with every candidate repeat above the baseline maximum | [Final report and 2880 rows](../benchmarks/component-paging-direct-page-20261025/results-report.md) |

The follow-up preserves all 7200 original arm rows across ten completed phases.
The earlier [PC03 rejected experiments](component-paging-cost.md) and their 5760
rows remain separate and unchanged. No unfavorable row or tail observation was
discarded. Favorable metadata and heavy-workload observations did not waive the
preservation gate. Each rejected algorithm stopped after one independent source
block; four repeats inside a source session are not four independent blocks.
The planned three-block confirmation applied only to a promising first screen,
and neither candidate reached that gate. No further algorithm was attempted.

PC04 asserted actual response CLR type and assembly identity but still observed
unchanged-code cost differences. That result did not identify the cause of
earlier negatives. PC05/PC06 therefore timed the same actual production method
across frozen baseline/candidate source states. Exact helper bytes were shared
within each source pair, while a fresh baseline was required when helper/marker
identity changed. Staged source SHA256, loaded assembly MVID, actual method IL
hash and explicit native recompilation/input receipts identify those states.
Frozen-comparator arms served only as drift markers; their offset was never
subtracted and their variance never widened an acceptance threshold.

The retained regression test covers a getter destroying a later selected
component and adding new components. The first response retains the initial
count/order and the serializer's null destroyed slot; later reads see additions.
Existing missing-script, reentrant-getter, cursor, shared-budget, detached-result
and read-only checks remain in place. Both candidate algorithms and their fresh
baselines passed the focused 43 native cases while failing the cost gate. After
restoring production, final native run 451 again passed 43/43 with zero skips.

Allocated-byte counters remained uncalibrated/null; zero-only working-set
counters were unavailable. Reports distinguish sampled managed heap from
allocation, native/process memory and retained leaks, retain signed released
deltas and quantization, and report GC generations separately. No allocation-
byte or native-memory saving claim is made.

Opt-in helpers and source snapshots live under `docs/benchmarks`, outside normal
Unity source/test discovery. The comparator has no resource registration or
NUnit dependency; measurement helpers belong only in the existing isolated test
assembly. Repository Unity metadata/GUIDs, public API, budgets, packages and
dependencies are unchanged. The [final closure receipt](../benchmarks/component-paging-direct-page-20261025/final-restoration-summary.json)
confirms all original 2002 input hashes, runner, launcher and empty filters were
restored. Exactly six verified owned additions were removed; caches/evidence
were retained, no owned process survived and protected identities were preserved.
That closure is separate from performance evidence.
