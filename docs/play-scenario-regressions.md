# Scenario regression evidence

Use saved evidence to compare known baselines, inspect intermittent failures and export a portable
report. The offline commands below do not launch Unity, retry failed runs, discover history or
change the original scenario result. Start with [scenario authoring](play-scenarios.md),
[standalone execution](play-scenario-execution.md) and
[Player assurance](play-scenario-player-assurance.md).

See [repeatable scenario workflows](play-scenario-workflows.md) for saved regression profiles,
complete soak/session history analysis, semantic UI snapshots, bounded input, interaction drafts
and reviewed scene edits.

Examples use explicit artifact paths. Replace them with the actual job directories returned by the
launcher; a parent output directory is not a saved soak. Create the selected output parent
directories before exporting. Use a new filename for each output: analysis, visual artifacts and
HTML export refuse to overwrite existing evidence.

## Observe step performance and named screenshots

Add optional `performance` and `screenshot` objects to setup, main or cleanup steps. For example,
save this definition after replacing its scene and hierarchy paths with verified project values:

```json
{
  "name": "menu-regression",
  "setup_steps": [
    {
      "name": "Load menu",
      "action": "load_scene",
      "scene": "Assets/Scenes/Menu.unity"
    }
  ],
  "metrics": { "enabled": true, "warmup_iterations": 1 },
  "steps": [
    {
      "name": "Menu remains ready",
      "action": "wait_object",
      "target": "Canvas/Ready",
      "count": 1,
      "active": true,
      "stable_for_ms": 1000,
      "timeout_seconds": 10,
      "performance": {
        "max_duration_ms": 5000,
        "max_frame_time_p95_ms": 50.0,
        "max_gc_allocated_bytes_per_frame": 65536,
        "sample_limit": 2048
      },
      "screenshot": {
        "name": "menu-ready",
        "width": 1280,
        "height": 720
      }
    }
  ]
}
```

These limits are examples, not recommended budgets for a game. Omit unused performance limits;
explicit null limits are invalid. Duration is bounded to 0-120,000 ms, frame p95 to the same finite
range, and GC bytes per frame to 0-2,147,483,647. `sample_limit` is 1-4,096 and defaults to 4,096.

Duration uses actual elapsed step time. Selected frame budgets collect samples once per real Unity
update, using unscaled frame duration and the `GC Allocated In Frame` counter when available.
Condition polls are not frame samples. Frame p95 uses the sorted observed frame durations; GC uses
the maximum observed bytes in a frame. A step that finishes before a complete frame is observed
cannot pass a selected frame budget. Unsupported counters and missing selected measurements
remain explicit errors. A missing measurement is never converted to zero.

Results record `duration_ms`, `frame_sample_count`, `gc_sample_count`, `observed_frame_count`,
`sample_limit_exceeded`, available frame/GC values, `passed` and an optional `error`. Once the sample
limit is exceeded, retained samples stay bounded and the observed count continues. A selected
frame or GC budget fails because its complete evidence is unavailable; it does not pass using a
truncated prefix. Duration-only budgets do not require a per-frame counter. Budget failures use
`performance_budget_failed`; cleanup failures remain cleanup failures.

A named screenshot captures the composited rendered frame after the step condition completes.
It waits at most two seconds for a rendered frame, without repeating the step action. Capture names
use the scenario name pattern `[a-z0-9][a-z0-9_-]{0,63}`. Both dimensions are required, each 1-4,096;
the image may contain at most 8,388,608 pixels and its PNG at most 4 MiB. Set the game's actual
rendered resolution to the requested dimensions. Capture does not resize the image. A resolution
mismatch is a failure.

The step result records the name, requested dimensions, status and, for `captured`, the artifact
path, actual dimensions and SHA-256. `unavailable` and `failed` preserve their reason. An unsuccessful
requested checkpoint fails the step with `screenshot_checkpoint_failed`. Existing action failures
are retained when observation is aborted. Optional observations do not change old definitions that
omit them.

Batch/headless execution cannot capture these checkpoints. Use a graphical Editor in Play Mode or
opt into a graphical standalone launch:

```sh
unity-mcp --format json play-scenario player-run <build-directory> \
  --output-dir reports/graphical-run --require-verified-payload --graphical
unity-mcp --format json play-scenario player-soak <build-directory> \
  --output-dir reports/graphical-soak --iterations 30 --graphical
unity-mcp --format json play-scenario player-session <build-directory> \
  --output-dir reports/graphical-session --mode shared-batches --iterations 30 --graphical
```

`--graphical` omits the launcher's `-batchmode` argument and applies to each owned session
process. It does not establish that a frame was rendered or captured successfully. Default launches
remain batch/headless; their checkpoint results are explicitly unavailable. Build verification,
timeouts, cleanup and natural process-exit requirements still apply.

## Register and pin saved soak baselines

A baseline registry stores immutable named references to existing complete, successful Player
soaks. It does not copy or update their evidence. Select a local registry file explicitly, or select
an existing directory to use only its `baselines.json` file. The registry parent must already exist.

```sh
unity-mcp --format json play-scenario baseline --registry reports/baselines.json \
  register menu-stable reports/baseline/<job-id>
unity-mcp --format json play-scenario baseline --registry reports/baselines.json \
  pin menu-stable --reason "Reviewed successful menu soak"
unity-mcp --format json play-scenario baseline --registry reports/baselines.json list
unity-mcp --format json play-scenario baseline --registry reports/baselines.json history
unity-mcp --format json play-scenario baseline --registry reports/baselines.json \
  compare reports/candidate/<job-id> --name menu-stable
```

Registration admits the matching request, frozen definition, native final/soak reports, complete
journal, client summary, process receipt and retained iteration reports. The process must have ended
naturally with exit 0. The registry stores the absolute root and `soak.json` path, registration time,
identity/provenance, file hashes and an aggregate evidence hash. Reusing a name is rejected, including
attempts to replace its reference with another successful soak. To review new evidence, register a
new name and explicitly change the pins.

Named comparisons and budget checks select only pinned references. They reuse the existing
`compare-soak-metrics` and `check-soak-budget` computations, require compatible experiment
conditions and known supported recorded environment fields, and revalidate the registered evidence
on use. Changed files, unknown environments or incompatible definitions/options are refused. Several
names may be pinned; omitting `--name` succeeds only when exactly one compatible pinned entry exists.
No arbitrary latest baseline is selected.

Use the existing [soak budget policy](play-scenario-player-assurance.md) to gate selected metrics.
For example, save this as `reports/memory-budget.json`:

```json
{
  "schema_version": 1,
  "metrics": {
    "managed_bytes": {
      "max_net_change_increase": 1048576,
      "max_slope_increase_per_iteration": 4096,
      "min_paired_samples": 10,
      "min_consecutive_samples": 10
    }
  }
}
```

```sh
unity-mcp --format json play-scenario baseline --registry reports/baselines.json \
  check reports/candidate/<job-id> --name menu-stable --policy reports/memory-budget.json
unity-mcp --format json play-scenario baseline --registry reports/baselines.json \
  unpin menu-stable --reason "Replaced after reviewing the next build"
```

Pin/unpin operations require a reason and retain revision, action, timestamp and prior/resulting pin
sets. Unpinning preserves the registration and history. Registry schema 1 contains `revision`,
`entries`, `pins` and `history`, with at most 256 entries, 4,096 audited mutations, 16 JSON nesting
levels and 2 MiB total. Manifest hashes cover sorted unique `filename:sha256` lines, each followed by
a newline. Paths, strict fields, hashes and the audit chain are validated when reading.

Writers use a five-second exclusive sidecar lock, revision protection and atomic file replacement.
Contention or an observed lost update fails without replacing the registry. A lock left by an
interrupted writer is not reclaimed automatically; verify that writer has ended before removing its
explicit `.lock` file. Keep registries local and avoid hand-editing identities or pin history.

In **Compare saved Player soaks**, expand **Named local baseline**, choose/load the registry, select
a registered name and click **Use selected baseline**. Then import the baseline and candidate
snapshots through the existing two-source view. The selector validates file hashes before selection
and again during baseline import; changing the registry source clears its choices and selected
baseline. It performs no broad directory scan and launches no Python process. This Editor view
remains native snapshot diagnostics and does not issue the CLI process-exit or budget verdict.

## Analyze intermittent outcomes

Select between 1 and 64 explicit ordinary `run.json` files:

```sh
unity-mcp --format json play-scenario analyze-flakiness \
  reports/attempt-a/run.json reports/attempt-b/run.json \
  --output reports/flakiness.json
```

The analyzer groups compatible known definition, configuration, execution and reproduction evidence.
Unknown metadata stays isolated and makes the analysis incomplete. Duplicate run identities are
rejected. It counts passed, failed, timed-out, cancelled, skipped, unknown and unfinished iteration
slots from saved evidence. Missing native ledgers are explicitly identified as legacy inference;
they do not establish complete repeat history.

`intermittent` means the compatible group contains both an observed pass and failure/timeout.
The failure rate is `(failed + timed_out) / (passed + failed + timed_out + cancelled)`; skipped,
unknown, pending and running slots are excluded. Failure groups align by stage, step index, name,
code and status, retaining at most 64 groups with an omitted count. Available native timeout durations
and original outcomes, first failures and cleanup failures remain visible. A later pass never erases
an earlier failure, and diagnostic rates are not a retry policy or a scenario success verdict.

Soak parent `run.json` files, session parent reports and session JSONL are unsupported and produce
incomplete evidence. An ordinary retained iteration report describes only that selected iteration;
it cannot establish the outcome of its entire soak or session. The analyzer neither searches those
parents for children nor reruns unsupported inputs.

For complete parent ledgers, use [analyze-history](play-scenario-workflows.md#analyze-complete-soak-and-session-history).
This adds source-local complete history analysis without changing `analyze-flakiness` admission.

## Compare visual checkpoints

Save an explicit policy such as `reports/visual-policy.json`:

```json
{
  "schema_version": 1,
  "width": 1280,
  "height": 720,
  "channel_threshold": {
    "red": 8,
    "green": 8,
    "blue": 8,
    "alpha": 0
  },
  "max_changed_pixel_ratio": 0.01,
  "ignored_rectangles": [
    { "x": 1100, "y": 0, "width": 180, "height": 80 }
  ]
}
```

```sh
unity-mcp --format json play-scenario compare-visual \
  reports/baseline/menu.png reports/candidate/menu.png \
  --policy reports/visual-policy.json --output-dir reports/visual-diff \
  > reports/visual-diff/result.json
unity-mcp --format json play-scenario compare-visual-reports \
  reports/baseline/run.json reports/candidate/run.json \
  --checkpoint menu-ready --stage main --step-index 0 --iteration 1 \
  --policy reports/visual-policy.json --output-dir reports/checkpoint-diff \
  > reports/checkpoint-diff/result.json
```

Create the redirection destination's parent first. `compare-visual` accepts only the two explicitly
selected PNGs and reports their SHA-256 hashes; it does not infer scenario identity from filenames.
`compare-visual-reports` additionally requires matching known definition/version/environment and
step name/action, one exact stage/index/iteration, the requested checkpoint name, `captured` status,
matching dimensions and bytes matching the reported SHA-256. Image paths must remain below each
selected report directory. It never chooses a latest or positional screenshot automatically.

Both images must exactly match the policy dimensions. There is no resize or image alignment.
Policies require every shown field and reject unknown fields. RGBA thresholds are integers 0-255;
a pixel changes when any channel's absolute difference exceeds its threshold. Ignored rectangles use
half-open pixel coordinates, stay inside the image and number at most 64. Overlap is counted once;
masking every pixel is invalid. The changed ratio uses only unmasked pixels and passes at or below
`max_changed_pixel_ratio` (0-1).

Each single-frame PNG is at most 4 MiB, with dimensions 1-4,096 and at most 8,388,608 pixels. Animated,
corrupt or oversized inputs are refused. Optional new `heatmap.png` and `diff.png` outputs retain
fingerprinted artifact metadata; each output is bounded to 32 MiB. Missing captures and unavailable
headless checkpoints are not matching images and cannot pass comparison.

## Export one portable HTML report

```sh
unity-mcp --format json play-scenario export-html reports/attempt-a/run.json \
  --output reports/run-report.html --analysis reports/flakiness.json \
  --visual reports/checkpoint-diff/result.json
unity-mcp --format json play-scenario export-html reports/suite/suite.json \
  --output reports/suite-report.html
unity-mcp --format json play-scenario export-html reports/candidate/<job-id> \
  --baseline reports/baseline/<job-id> --output reports/soak-report.html
```

The source is a saved run/suite JSON file or a complete successful saved soak directory. Directory
export admits the full journal and charts its history instead of presenting the final selected
iteration as the entire soak. `--baseline SOAKDIR` adds compatible full soak curves and computes the
existing diagnostic comparison; it requires a soak directory source and cannot combine with
`--comparison`. Unknown environment labels remain visibly unverified in diagnostic comparisons.

Alternatively, attach an explicitly saved comparison JSON with `--comparison`, a flakiness JSON
with `--analysis`, or a visual result JSON with `--visual`. These options display saved results and
do not execute their generating commands. The report includes identities/environment, native
outcomes, iteration/step details, first/cleanup failures, available performance values, screenshots,
memory curves and supplied comparison/analysis sections.

The HTML is a standalone file with embedded styles, charts and admitted PNG data, without a server
or external network resources. Text is escaped. Images must be local, under the relevant source or
visual-result directory, and match their recorded hashes/dimensions. Embedding allows at most 16
images, each at most 4 MiB and 12 MiB total; missing or oversized attachments are visibly unavailable.
Copying the exported HTML does not require copying a directory of browser assets.

Export success means that a new report file was written. Failed source outcomes remain failed in
the report. An HTML file, flakiness summary or visual pass cannot establish native execution success,
natural process exit or a passed memory budget. Keep the original JSON, journals, process receipts
and logs for machine gates and detailed evidence.

## Offline command exit codes

| Command | Exit 0 | Exit 1 | Exit 2 |
| --- | --- | --- | --- |
| `baseline compare` | Diagnostic metrics compared | - | No supported comparison |
| `baseline check` | Within selected memory budget | Budget exceeded | Not comparable |
| `analyze-flakiness` | Analysis complete, including observed failures | - | Incomplete or invalid inputs |
| `compare-visual`, `compare-visual-reports` | Within visual budget | Visual budget exceeded | Not comparable or invalid inputs |
| `export-html` | New HTML exported | - | Not exported |

Registry register/list/pin/unpin/history use ordinary CLI errors for invalid inputs. Preserve the
actual process exit and JSON status when collecting command outputs; shell redirection alone does
not establish a passing gate. Invalid CLI option/argument syntax uses the usual usage-error exit 2.
