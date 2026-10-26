# Resource limits for substantial workloads

Workload admission is separate from response paging and retained history. A large
project should not require returning every object, image or diagnostic in one MCP
response. These limits admit substantial work while preserving ownership,
cancellation, path containment and bounded retained memory. They are ceilings,
not preallocated buffers or a guarantee that every permitted combination fits
the machine's RAM, GPU memory or disk.

## Execution and transport

| Operation | Capacity and continuation |
| --- | --- |
| Ordinary command | 300-second default; finite explicit policies can allow up to 24 hours. Use an asynchronous job for builds, tests, generation, recordings and scenarios, then poll its ID. A longer transport wait does not change the job's selected policy. |
| Test startup and readiness | Test initialization defaults to 120 seconds and allows 1 hour explicitly. Refresh readiness allows 300 seconds; package domain reload allows 600 seconds. Connection/authentication probes remain short. |
| Command arguments | 32 MiB encoded, 1 million JSON nodes, depth 64, with a separate 256 MiB retained estimate. Pending retained budgets are 256 MiB/session, 512 MiB/user and 1 GiB globally. |
| Results | 64 MiB encoded, 1 million nodes, depth 64, with a separate 512 MiB retained estimate. Retained result budgets are 1 GiB/session, 1.5 GiB/user and 2 GiB globally. Envelopes and duplicate text/structured representations also consume the applicable response budget. |
| Legacy and Blender | Legacy framed results allow 128 MiB including framing/envelope overhead. Blender socket responses allow 64 MiB and preserve an explicit wait up to 24 hours. Connect probes stay short. |
| Native local TCP connections | Up to 64 authenticated clients, 16 simultaneous authentications and 128 commands. A new client does not evict an existing authenticated connection. |

Native local TCP input frames retain the 128 MiB maximum. Before allocating a
body, the host reserves five times its declared UTF-8 size for input buffering
and text retention. Frames larger than 8 KiB share a 1280 MiB FIFO budget;
frames at most 8 KiB use a separate 8 MiB FIFO budget so large inputs cannot
consume all small status/cancellation ingress. Request size grants no execution
authority. Original frame I/O deadlines cover admission waits.

Input reservations survive transport restarts and remain owned until actual
command settlement, including cancellation. Idle headers reserve no body bytes.
These weighted budgets are not preallocations or upper bounds on parsed JSON
graphs, plugin/output allocations or total process memory.

The optional durable test-wait journal retains a 256 MiB physical quota, including
atomic replacement reservations. It admits one worst-case 64 MiB active result;
its wait remains at most 60 seconds while preserving the outer command deadline.

Concurrent command counts remain bounded. Exhaustion returns explicit capacity
evidence; wait for existing owners to finish and retry. A timeout does not prove
that a native operation never ran, and indivisible main-thread Unity APIs are not
preempted by a larger or smaller timeout. Do not automatically replay mutations.
See [transport behavior](transport-performance.md).

## Discovery and authoring

| Area | Capacity |
| --- | --- |
| Scene/asset discovery | 250,000 roots or asset rows and 256 loaded scenes. Asset metadata snapshots share a 64 MiB aggregate, four entries and a 30-minute lifetime; hierarchy/search continuations last 5 minutes. |
| Reference diagnostics | Explicit `max_assets`/`max_objects` up to 250,000 and `max_work` up to 4 million; ordinary defaults remain 1,024 objects, 10,000 asset work units or 100,000 serialized-property work units. Source revision hashing streams up to 8 GiB, with 16 MiB metadata. |
| Batch and scene plans | Default batch allowance 100 commands, absolute maximum 1,000; a lower explicit Editor setting remains effective. Scene edits allow 1,000 operations/4 Mi characters; structural plans allow 256 operations and 10,000 scoped objects. |
| Asset labels | 1,024 targets and labels, 8 MiB cumulative observed/planned label text; metadata reads are streamed with 16 MiB/file and 256 MiB cumulative budgets. |
| Source changes | 2 MiB/source, 256 edits; combined preview 8 MiB. Change sets allow 256 files, 32 MiB original/candidate storage and a 1 MiB manifest; server candidate text is limited to 16 MiB. Semantic context allows 4,096 sources, 2,048 references, 64 MiB and 60 seconds. |
| Inline code | 512 Ki UTF-16 code units per snippet. Replay history retains at most 50 entries and 8 Mi code units in aggregate. Eviction does not undo execution. |
| Input actions/localization | Input files: 16 MiB, 256 maps, 8,192 actions, 32,768 bindings. Localization: 100,000 keys/rows, 16 MiB source, 64 Ki-character values, 256 exact tables and 32 fallback entries. |
| Prefabs | Plans: 10,000 plain nodes, 4,096 dependencies, 256 MiB streamed hashes. Override inspection: 100,000 nodes, 4 million properties and 250,000 rows. |
| ProBuilder | 65,535 vertices (the installed package's engine limit), 65,536 faces, 1,048,576 total indices and 262,144 edges. A face can expose 196,608 indices; a page is limited to 4 MiB. Reduce `page_size` when several complex faces exceed it. |
| Shader Graph/UI source | Shader graphs: 16 MiB, 65,536 objects, 16,384 nodes, 32,768 edges and 1 million JSON tokens. UI source: 8 MiB. |

Use cursors for hierarchy, search, serialized properties, console and asset
inspection. Their row/work/preview caps bound one response, not the whole
project. A stale cursor requires a fresh observation. Reference scans separately
report whether the requested scope is complete; raise their explicit work budget
or narrow the folder/scene when needed. A page alone is not proof of completeness.

UI diagnostics remain focused observations: typically 1,000 nodes with explicit
depth, character and evaluation budgets. Select a smaller subtree for detailed
layout, raycast or text evidence. Terrain mutations operate on bounded regions
or selected tree indices (256 KiB requests), not an entire terrain's raw arrays.
Serialized-array growth and recursive reflection retain finite per-operation
guards; split large edits into reviewed chunks.

Localization revision digests now use streamed, versioned composition. Refresh
an old observation before submitting a revision-guarded localization mutation.

Automatic reference graphs have separate operator-owned collection-attempt and
durable-state budgets. Paging does not remove these limits; saved work is
preserved on exhaustion. See [graph limits and recovery](../REFERENCE_GRAPH.md#operator-budgets).

## Images, artifacts and builds

| Area | Capacity and memory behavior |
| --- | --- |
| Textures/captures | Texture dimensions up to 8,192, 512 MiB encoded input. Single captures allow 64 Mi pixels/frame and 8,192 pixels/side after supersampling; orbit captures allow 128 shots. Tool-owned batch cameras render at output tile resolution with 33,554,432 rendered pixels/batch. Retained tiles and contact sheets each have a separate 33,554,432-pixel budget; sheets also respect the side limit. Actual device and encoder support still applies. |
| Recording | Explicit duration up to 3,600 seconds, 60 fps and 4,096-pixel dimensions, with a 20-trillion source/encoded pixel-frame budget. Defaults are unchanged; frames are encoded incrementally. |
| Provider downloads | 8 GiB/artifact, 1-hour download deadline and 2-hour asynchronous job lifetime. Downloads use an owned project-contained temporary file, 64 KiB buffers and incremental hashing. Cancellation, failure and reload release temporary ownership. |
| Archives/model bundles | ZIP: 16,384 entries, 8 GiB per file/total, 200:1 expansion ratio and 16 MiB directory metadata. Model bundles: 8,192 files, 65,536 references, 16 GiB combined bytes, 64 MiB JSON, 16 MiB/MTL, 128 MiB aggregate metadata and 256 KiB text lines. Candidate list: 1,024. |
| Build cleanup/Player inventory | Cleanup: 250,000 entries and 120 seconds. Player payload: 16,384 files, 32,768 directories and 128 GiB, fingerprinted by streaming; inventory 32 MiB, outer manifest 96 MiB including repeated inventory representations. |
| Profiler | Snapshot file admission 8 GiB; selected-column reads 64 MiB/column and 128 MiB total, with 1 million native rows. Live aggregates default to 250,000 objects and allow 1 million explicitly, with 256 roots/4 million visits/4,096 types. |

Asset-generation image metadata, JSON structure and local model copies also
pass independent admission checks. See [asset admission and local-copy contracts](../asset-gen-download-security.md#metadata-and-local-copy-admission)
for defaults, Editor-process overrides and format limitations.

Large files are not automatically loaded into same-sized byte arrays. However,
image decoding still requires pixel memory, and final asset publication/import
can perform synchronous disk/Unity work. An 8 GiB accepted file is not a promise
of a responsive frame during native import. Failed final publication can retain
partial destination evidence for recovery; temporary download ownership is
separate and is released.

Retained format/provider rules include GLB's 32-bit container length, provider
image-upload caps (32 MiB generally and 20,000,000 bytes for Tripo), archive
expansion guards, supported extensions and path containment. Native snapshot
reading still requires supported Memory Profiler ABI/format, at most 32 MiB
metadata and 1 million dynamic entries; some very large captures can exceed
those reader constraints despite fitting the file-size limit.

Build-report artifacts are bounded summaries: 512 KiB, up to 512 steps and 2,048
packed-asset rows. They preserve the authoritative build summary and mark omitted
details incomplete; those limits do not stop a larger build. Sprite inline image
previews retain 4 MiB/8,000-side caps and report omission; the original asset
remains accessible by path.

## Scenario execution and evidence

| Area | Capacity |
| --- | --- |
| Saved definitions | 1,000 scenarios and 256 suites, 1 MiB/definition. A scenario has up to 128 main, 32 setup and 32 cleanup steps; a suite has 64 children. Frozen suite definitions share a 16 MiB budget. |
| Repetition/time | 100 ordinary repeats, up to 24 hours/run or suite, 1 hour/step and 30 minutes/cleanup. Ordinary defaults remain 300-second runs, 30-second steps and 30-second cleanup. Same-process soak retains its separate 1,000-iteration/24-hour policy. |
| Parameters/scene lookup | 64 parameters, 256 bindings, 64 datasets/values; 256 referenced/loaded scenes. Editor and Player target searches and loaded-object metrics inspect up to 250,000 objects. Recording all initially loaded scenes is limited to the 32 setup slots. |
| Input/recording | Explicit recording allowance 127 events/1 hour (defaults remain 31/300 seconds). Input holds/waits permit 36,000 frames; stability windows permit 10 minutes. Active device updates renew a 30-second inactivity lease, with an absolute 1-hour lifetime. A stalled update loop still releases devices. |
| Reports/screenshots | 19,200 step-result rows and 16 MiB/report. Checkpoints allow 8,192-side/64 Mi-pixel images, 320 MiB/PNG and 8 GiB PNG files/run. |

Offline visual comparison processes 256-row strips and closes source and
temporary image buffers on every exit. Two decoded source images remain needed;
optional output images add their own RGB storage. PNG output is encoded directly
to owned files, with cleanup after partial failure. HTML reports retain only
4 MiB per inline preview, 12 MiB total and 16 images, and identify omissions;
full-resolution artifacts remain separate. User-selected performance, leak and
visual assertion thresholds are not silently relaxed.

## Limits intentionally retained

- Dynamic compilation allows 256 unique loaded identities per scripting domain.
  Mono cannot unload an individual generated assembly. Reuse parameterized
  helpers; deliberate domain reload is the recovery path when new identities
  exhaust the budget. See [resource lifetimes](resource-lifetimes.md).
- Logs, UI Undo, completed jobs, receipt results, schemas, default materials and
  metadata caches retain count/byte/expiry bounds. Eviction limits retained
  history; it does not cap future independent operations.
- Diagnostic event/raycast/physics buffers, custom state/reset registrations and
  per-step event marks remain focused bounded instruments. Split scenarios or
  narrow the registered scope; loss/truncation evidence must not be treated as
  a complete measurement.
- Identifier lengths, numeric representations, JSON depth, provider protocol
  limits, filesystem component limits and extension/path rules remain enforced.
  Increasing time or bytes never grants permission to escape the selected root.

Regression coverage includes real 100,001-object scene lookup, an 8K-edge image,
large fragmented Blender responses, 100-repeat report persistence, synthetic
multi-GiB length/seek admission, bounded 16 MiB download streaming,
cancellation/ownership cleanup and old-boundary-plus-one cases. These checks establish the exercised paths, not every maximum combination
or a measured whole-project memory ceiling. Native validation uses Windows Unity
6000.0.69f1; Linux/macOS native execution is outside this change's validation scope.
