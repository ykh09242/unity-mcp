# Same-process Player soak contract

Version 1 of soak evidence extends Player request schema 2. Bundles remain schema 2.
Normal requests retain repeat_count 1-10 and timeout_seconds 1-1800. An optional
`soak` object is accepted only in schema 2, requires repeat_count 1, and contains
exactly `iterations` (integer 2-1000), `max_runtime_seconds` (integer 1-86400),
`failure_policy` (`stop` or `continue`), and `retain_reports` (integer 1-64).
Each engine executes one complete setup/main/cleanup iteration in the same native
Player PID. Its local ledger remains iteration 1. The outer wall-clock deadline
is separate from each engine timeout. Cancellation, outer timeout, runner errors,
and evidence persistence errors always stop the session. When the outer deadline
expires, the coordinator requests engine cancellation to execute cleanup while
the aggregate status is timed_out; the local native status remains unchanged.
The continue policy applies only to completed native failed iterations. Native
cancelled and timed_out iterations always stop after cleanup, including a
per-iteration timeout. Every engine releases its host.

## Files and identity

`iterations.jsonl` is UTF-8 without BOM, LF terminated, append only, at most 1000
rows, 16384 bytes per complete row including LF, and 16384000 bytes total. Each
row has schema_version 1, job_id, definition_hash, build_id,
build_source_revision, payload_hash, process_id, iteration (global recorded ordinal, contiguous
from 1), status (the native run status), started_unix_ms, finished_unix_ms,
runner_resources_released, failure_code (nullable), error (nullable, capped at
512 characters), cleanup_error (nullable, capped at 512 characters),
unexpected_log_count, query_counts (native query-count object or null), metrics
(native snapshots with global iteration attribution), and report_file
(`iteration-NNNN.json`). Counts only include durably appended complete rows.
The timestamps are the actual local iteration ledger timestamps, nullable when
the engine terminates before first evaluation. Such a row is recorded but never
attempted or passed. A no-start row always stops the session. Rows are compact
evidence; full step/timeline/log detail is intentionally absent.
Missing optional measurements remain null, never fabricated zero. A row exceeding
the cap fails persistence; it is never silently truncated. Report files may later
be pruned, so report_file alone does not establish retention.

`soak.json` is an atomically replaced checkpoint, at most 65536 bytes. It has
schema_version 1 and the same identity fields, process_scope (exactly `same-process`), requested_iterations,
attempted_iterations, passed_iterations, failed_iterations, recorded_iterations,
skipped_iterations, first_failure_iteration
(nullable), started_unix_ms, deadline_unix_ms, finished_unix_ms (nullable),
status (`running`, `succeeded`, `failed`, `cancelled`, `timed_out`),
finalization_state (`running` or `completed`), failure_policy, retain_reports,
journal_file (`iterations.jsonl`), journal_rows, journal_bytes,
journal_sha256 (null at checkpoints; lowercase SHA-256 of exact persisted bytes at finalization), journal_complete
(boolean), runner_resources_released (boolean), selected_iteration (nullable),
retained_report_files (bounded list of current iteration report filenames),
error (nullable), and metrics_summary (nullable object, defined below).
Attempted rows require a ledger start. Passed rows additionally require native
succeeded status, release true, null error/cleanup_error/failure_code and zero
unexpected_log_count. Failed is attempted minus passed. Recorded is journal_rows,
including no-start rows. Skipped is requested minus attempted, including planned
iterations that were never reached. First failure selects only an attempted
nonpassed row. Otherwise the last actually started iteration is selected;
selected_iteration is null if no engine ever started evaluation. Retention pins
the selected actual report (first actual failure, otherwise last actually started
iteration) and keeps the newest remaining reports up to retain_reports. An
unstarted later row cannot displace the selected report even when retain_reports
is 1. When no iteration has actually started, retention keeps the newest
unstarted report. A final checkpoint records the
reason early execution stopped. A running checkpoint never proves success.

Each retained iteration report includes the normal completed native report fields,
local exit_code, player_schema_version 2 and reproduction metadata.
`run.json` remains the compatible full native report of selected_iteration, with
its original status, timestamps, timeout and local iteration-1 ledger. It adds
`soak` equal to the completed soak.json object. The report's exit_code is the
whole session result: 0 only when every requested row passed, journal integrity
was verified, all host/subscription release succeeded, and final persistence
succeeded; 1 for execution failure/cancellation/timeout; 2 for runner or evidence
persistence error. The launcher additionally requires natural child exit 0 and
matching PID/identity, exact ordered journal counts/hash, and compatible selected
report. If selected_iteration is null, run.json preserves the current preflight or
unstarted engine failure; it cannot prove success. A selected report with status
succeeded cannot erase a failed soak. Release and report errors always stop
even under continue policy.

When storage fails, the runner preserves the first cause in an additive, failure-only
`run.json.persistence_error` object when the final report can still be saved. Its fields
are `schema_version: 1`, `stage`, `exception_type` (at most 128 UTF-16 units), `error`
(at most 512 UTF-16 units, without splitting surrogate pairs), and `iteration` (global
ordinal or null). Stages are `iteration_report`, `journal_append`, `checkpoint_replace`,
`report_retention`, `journal_verify`, and `final_report`. The original scenario failure
remains separate. Later cleanup or final-save errors cannot replace the first cause.

A storage failure stops admission and prevents another append attempt, including under
`continue`. The runner attempts the distinct final report once, but never labels an
incomplete checkpoint complete to save it. When that final write is also unavailable,
the process still exits 2 and logs the first bounded persistence cause. Missing final
evidence remains incomplete; inspect preserved journal rows and the Player log.

Progress schema stays 1. Its launch identity, sequence, main_loop_sequence and
elapsed_ms are session-wide and monotonic even when engine timestamps reset.
`iteration` is global during soak. Normal progress retains its existing meaning.

## Unknown and interrupted evidence

An interrupted file write, stale output, missing row, mismatched identity, invalid
hash, unfinished checkpoint, unknown schema or absent final report cannot prove
success. Attempted/passed/failed values are derived from the complete persisted
journal. Unstarted requested iterations are not counted as attempted. Compact
error text is bounded diagnostic context; full retained reports provide detail.
Metrics are opt-in through the frozen definition; null means unavailable or
disabled. Metrics/resource sampling does not establish a leak.

metrics_summary is null when metrics are disabled. Otherwise it has sample_count
(integer 0-1000), usable_samples (integer 0-1000), warmup_iterations (integer
0-9), bounded_samples (at most 10 native metric snapshots attributed to global
iteration), warnings (at most 3 strings of at most 128 characters), and summary
(string at most 128 characters). sample_count counts persisted row snapshots;
usable_samples excludes warmup and error samples. A missing boundary sample or
measurement breaks its increasing sequence. Trend counters cover the entire
session while retained snapshots use a rolling window. Warned trends are
diagnostic only and do not change native iteration status. Local report snapshots
keep their original iteration-1 attribution; only journal snapshots are remapped.
