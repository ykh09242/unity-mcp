# PC04 control and PC05 rejected array candidate

No runtime optimization was accepted. PC04's unchanged-production comparison
did not establish ordinary property-workload cost preservation. PC05 then
compared the same actual production method across baseline and array-candidate
source states; its first complete screen failed the medium-workload memory
preservation gate. All 4320 original arm rows are preserved below, including
favorable and adverse results.

The earlier [PC03 rejected results](../component-paging-cost-20261025/README.md)
remain unchanged. The [predeclared PC04/PC05 protocol](README.md), helpers and
source snapshots also remain immutable; this report records their later
outcome. The [manifest](control-and-rejection-manifest.json) pins source/helper
bytes, native metadata, original JSON/export hashes, row order and functional
results. Final direct-page work uses a [separate PC06 protocol](../component-paging-direct-page-20261025/README.md).

## Complete arm exports

Each file has 720 rows in original array order. Every original arm property and
value is retained, including null allocation totals and zero-only process
counters. Together these files retain 4320 rows across all six completed phases.

| Run | Protocol/source | Phase | Complete rows |
| --- | --- | --- | --- |
| 437 | PC04 unchanged baseline control | Timing | [JSONL](pc04-437-baseline-control-timing.jsonl) |
| 438 | PC04 unchanged baseline control | Allocation | [JSONL](pc04-438-baseline-control-allocation.jsonl) |
| 440 | PC05 actual production baseline | Timing | [JSONL](pc05-440-baseline-timing.jsonl) |
| 441 | PC05 actual production baseline | Allocation | [JSONL](pc05-441-baseline-allocation.jsonl) |
| 443 | PC05 actual production array candidate | Timing | [JSONL](pc05-443-candidate1-array-compaction-timing.jsonl) |
| 444 | PC05 actual production array candidate | Allocation | [JSONL](pc05-444-candidate1-array-compaction-allocation.jsonl) |

## PC04: unchanged source still showed asymmetry

Actual production and the frozen comparator compiled in `MCPForUnity.Editor`.
The actual response envelope, data and metadata-row CLR Type identity checks
passed. Both handlers retained identical baseline statements apart from the
containing class and resource registration. Native functional run 436 passed
all 43 paging, budget, read-only and parity cases.

In the unchanged 16-component/page25/properties/mixed/wire case, paired mean
wall cost was 251.401 to 276.950 us/read (+10.16%); p95 was 604.000 to 686.500
us (+13.66%), and p99 791.950 to 1347.700 us (+70.17%). Page median and cycle
median changed by only +1.30% and +1.00%. The sampled managed-heap excursion
was 6141952 to 6850560 bytes (+11.54%). The two production heap arms were
6139904 and 7561216, while both frozen controls were 6139904.

Each arm reported one collection in each generation. Released managed deltas
were 0/4096 bytes in the production arms and 4096/0 in controls; these values do
not establish a retained-object leak. The largest timing excess occurred in
the first production arm, while the largest heap excursion occurred in the
second allocation arm.

Across all 120 timing cases, the paired wall-ratio median was 1.000429, but ten
cases exceeded 1.03 and six exceeded 1.05. Pooled summaries did not waive the
adverse ordinary case. The same-assembly comparison therefore did not establish
the required precision. It did not demonstrate a cause for the previous PC03
negatives. PC04 stopped before any candidate was installed under its protocol.

## PC05: same production method across source states

PC05 used the actual `GameObjectComponentsResource.HandleCommand` for four
production arms in each session. Frozen-comparator arms were drift markers,
never offsets to subtract or variance with which to widen acceptance. The four
arms were within-session repeats; this screen was one independent matched
source block, not four independent experiments. Timing and allocation were
separate phases.

Baseline and candidate functional runs 439 and 442 each passed 43 cases.
The identical helper SHA256 was
`21fd1f2c32e301b11ef054acd081123bcb0a3daed1652190ec42aeeaf76cc178`.
Baseline source was `1de68ed4a6b11ebcb0fdb99b4ba8c069334d82a9be7338eb959fdcd152da7198`;
candidate source was `59c7834e59afab00d3b56de5df1e053791c16d3c8b4793c1d65889146c8a923c`.
Staged source pins, actual loaded assembly MVIDs and distinct production-method
IL hashes were recorded. QA retained sealed input/recompile evidence; a file
hash alone was not used to prove the loaded method's source.

### Rejection case

256 components/page100/properties/mixed/wire:

| Sampled managed-heap excursion, bytes | Baseline | Array candidate |
| --- | ---: | ---: |
| Production a | 7188480 | 7802880 |
| Production b | 7258112 | 7720960 |
| Production c | 7602176 | 8327168 |
| Production d | 7241728 | 7831552 |
| Mean | 7322624 | 7920640 |
| Median | 7249920 | 7817216 |

The mean increased 598016 bytes (+8.17%); the median increased 7.82%. Every
candidate repeat exceeded the maximum baseline repeat. Frozen marker values
were 7233536/7245824 in the baseline session and 7368704/7360512 in the
candidate session. These are separate diagnostic context, with no numerical
correction applied.

Every production repeat reported five collections in each generation. Released
managed deltas averaged 7168 versus 9216 bytes, with the same 8192-byte median.
That small released delta is distinct from the sampled excursion and is not
evidence of a retained-object leak. Managed bytes before release were lower in
the candidate; this did not cancel the larger sampled excursion.

The screen failed preservation and the array candidate was rejected. No further
confirmation blocks were run for it. One block does not establish universal
causality; the conservative decision is that the required preservation was not
demonstrated by the complete screen.

### Favorable and adverse timing retained

All 60 metadata cases improved mean wall cost, with ratio median 0.830921 and
largest ratio 0.970024. Across all 120 timing cases the wall-ratio median was
0.906428. The 2048/page1/metadata/full wall ratios were 0.740588 for construction
and 0.773887 for wire. Reported collections fell from 33 to 11 per construction
arm and from 35–36 to 13 per wire arm, counting generations separately. Sampled
heap ratios for those large complete traversals were 0.801815 and 0.794299.

Adverse cases remain equally visible. In 16/page25/properties/mixed/wire, mean
wall ratio was 0.998445 but median wall ratio was 1.031792, p95 mean ratio
1.132984 and p99 mean ratio 1.174984 (median p99 ratio 1.346673). The one-component
property middle/wire mean wall ratio was 1.170105, driven by one high repeat;
its median ratio was 1.007610. The 2048/page100/property full/construct mean wall
ratio was 1.058661 with one high repeat, while its median ratio was 0.994628.
None of these observations was dropped to obtain a favorable pooled result.

## Counter scope and packaging checks

All six phases had failed allocation calibration with delta zero. Allocated
byte totals are null/unavailable, not zero allocation. Working-set and lifetime
peak counters are zero-only and unavailable. CPU samples are coarse and often
zero; zero-bearing ratios support no speed claim. Sampled managed heap can miss
peaks and is distinct from allocated bytes, native memory, process memory and
retained-object leaks. Identical GC generation deltas are never summed.

97/120 PC05 allocation cases had a nonzero baseline sampled excursion and a
usable ratio. The largest ratio, 1.8, represented a 4096-byte mean difference
from a 5120-byte baseline in a tiny full/wire case. Similar 4096-byte steps were
retained with their absolute values, rather than confused with the 598016-byte
medium mixed-workload increase. Signed released-delta ranges were -45056 to
12288 bytes in both source sessions; zero/negative denominators do not support
meaningful ratios.

Packaging checked all 4320 exported rows against original parsed values,
property order and row order. Original/export SHA256s and exact executed source
and helper pins are recorded in the manifest. The public record uses relative
paths and excludes private project paths, host identity and Editor logs.
Packaging does not rerun native tests or convert a failed screen into a runtime
optimization.
