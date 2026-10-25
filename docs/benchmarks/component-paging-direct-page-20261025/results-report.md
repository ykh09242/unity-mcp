# PC06 direct-page candidate rejected

The final candidate was rejected at its first complete matched source screen.
Production was restored exactly to baseline SHA256
`1de68ed4a6b11ebcb0fdb99b4ba8c069334d82a9be7338eb959fdcd152da7198`.
No runtime optimization is shipped. No remaining confirmation blocks or further
algorithms were attempted. This unit contains regression coverage and evidence;
server journal work is a separate unit.

The [predeclared protocol](README.md) and [input manifest](manifest.json) remain
frozen. The [results manifest](results-manifest.json) records the final outcome,
exact source/helper hashes, loaded assembly/method provenance, functional
results, original JSON hashes and complete portable exports.

## Complete rows and correctness

| Run | Actual production source | Phase | Rows |
| --- | --- | --- | --- |
| 446 | Baseline | Timing | [720 JSONL rows](pc06-446-baseline-timing.jsonl) |
| 447 | Baseline | Allocation | [720 JSONL rows](pc06-447-baseline-allocation.jsonl) |
| 449 | Direct-page candidate | Timing | [720 JSONL rows](pc06-449-candidate2-direct-page-timing.jsonl) |
| 450 | Direct-page candidate | Allocation | [720 JSONL rows](pc06-450-candidate2-direct-page-allocation.jsonl) |

All 2880 original arm rows, values, property order and row order are preserved.
Each phase has the full 120-case matrix, six arms, ordinary/heavy cohorts,
property/metadata modes, all cursor workloads, construction and complete wire
output. Four production arms are repeats within a source session. This was
one independent source block, not four independent experiments. The frozen
comparator's two arms are diagnostic drift markers; no offset subtraction or
acceptance-band widening was applied.

Native baseline functional run 445 and candidate functional run 448 each passed
43/43 with zero skips. Source guards, actual response CLR type/assembly parity,
sealed input hashes and explicit recompilation evidence identify the actual
production method under each source. Helper SHA256 is
`fe48519457f9c932bea04334598f067625c864aa0c42ba205c024db53d5c5d0d`;
rejected candidate source is
`9c517f31965ef0292e778846678805ad60d4aa47113fdd76963a72d6843f58ad`.
The candidate snapshot remains immutable after production restoration.

## Adverse timing screen

2048 components/page25/properties/mixed/construction, actual wall us/read:

| Within-session repeat | Baseline | Direct-page candidate |
| --- | ---: | ---: |
| Production a | 380.528 | 425.810 |
| Production b | 382.105 | 444.439 |
| Production c | 388.830 | 405.309 |
| Production d | 367.886 | 401.640 |
| Mean | 379.837 | 419.300 |
| Median | 381.316 | 415.560 |

Every candidate repeat exceeded the maximum baseline repeat. Mean cost
increased 10.39% and median cost 8.98%. Frozen timing markers were
367.791/403.528 versus 371.749/388.053; these remain separate context.

The 2048/page25/properties/full/wire mean wall ratio was 1.061255 and median
ratio 1.058538. In the ordinary 16/page25/properties/mixed/wire case, mean wall
ratio was 1.031396, median wall ratio 1.057513, p99 mean ratio 1.092994, and
cycle maximum mean ratio 1.137780. These observations failed the no-material-
regression requirement; no universal causal attribution is claimed from one
source block.

The complete timing wall-ratio median was 0.980565, with 11 cases above 1.03
and seven above 1.05. Metadata median was 0.964865 and property median 0.990150.
Pooled favorable medians did not erase adverse cases. The largest metadata
wall ratio, 1.350684 in 2048/page100/beyond/wire, had two high candidate repeats;
all rows remain retained.

The required 2048/page1/metadata/full workload had only small mean wall changes:
ratios 0.992796 construction and 0.996328 wire. Timing collection counts were
33 in every construction arm and approximately 35 in wire arms. This screen
did not demonstrate a robust large full-paging benefit sufficient for adoption.

## Complete memory observations

Memory results do not waive the timing rejection. The 2048/page25/property
mixed/construction sampled-heap mean ratio was 1.036721, with median 1.004219.
The ordinary 16/page25/property mixed/wire sampled-heap ratio was 0.994743;
post-release candidate deltas were all zero. The 256/page100/property
mixed/wire sampled excursion increased from 7372800 to 7751680 bytes
(+5.14% mean, +5.36% median). Its post-release delta changed by only 1024 bytes
in the mean, with the same 4096-byte median; that is not a retained-leak claim.

For 2048/page1/metadata/full, sampled-heap ratios were 1.020585 construction
and 0.991203 wire. Across allocation cases, 96/120 ratios had nonzero baseline
excursions; their median was 0.997403. Eighteen were above 1.03 and eleven above
1.05. Tiny 4096-byte steps and zero/negative released-delta denominators remain
recorded in their absolute units rather than being presented as reliable ratios.

Allocation-byte calibration was false with delta zero in all four phases, so
byte totals are null/unavailable. Working-set counters are zero-only and
unavailable. Coarse zero CPU observations support no ratio claim. Sampled managed
heap can miss peaks and is distinct from allocated bytes, native/process memory
and retained-object leaks. GC generations are reported separately, never summed.

## Packaging and closure

All 2880 exported rows were compared with original parsed values, property order
and array order. Original/export hashes, exact source/helper/test pins, compiled
method provenance and native functional summaries are retained in the manifest.
Public files use relative paths and exclude private project/host paths and logs.
Final restored-baseline run 451 passed all 43 cases with zero skips and the
original baseline loaded method IL/MVID. The [portable closure receipt](final-restoration-summary.json)
confirms all original 2002 input hashes, runner, launcher and empty filters were
restored. Exactly six verified owned additions were removed; caches/evidence
were retained, no owned process survived and protected identities were preserved.
This closure is separate from candidate performance. Packaging is not a new
native test run.
