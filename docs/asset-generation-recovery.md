# Generation recovery after an Editor reload or restart

Generation jobs can continue from a recorded provider task after scripts reload or the Editor restarts. Keep polling the original local `job_id`; a restored job does not submit the original request again. The execution budget assigned at admission includes reload time and time spent outside the Editor. Restarting never grants a fresh budget. If the wall clock moves backwards across restart, recovery reports `checkpoint_clock_uncertain` and does not resume unverifiable work.

A single-phase task has one initial generation POST. Meshy text generation with textures has a preview POST and a separate refine POST. Each phase records intent before submission and its task ID after the response. A response can be lost before its receipt is recorded: that outcome remains unknown, and the job does not automatically submit that phase again. This is not an exactly-once payment guarantee.

## Status evidence

The existing `generate_model`, `generate_image`, and `generate_audio` status actions add:

| Field | Meaning |
|---|---|
| `recovery_state` | Whether recovery is available/resuming, not submitted, unavailable, terminal, or outcome unknown. |
| `recovery_code` | A bounded reason such as `resumed`, `submission_outcome_unknown`, `refine_outcome_unknown`, `import_outcome_unknown`, or `saved_asset_identity_changed`. |
| `provider_task_id` | A safe provider task identifier when recorded; never a signed download URL. |
| `provider_phase` | The last relevant submit/poll/preview/refine/download/saved/import phase. |

Existing asset path, GUID, import-stage and archive-selection evidence remains available. A retained file or registered GUID does not prove the model is usable. The model pipeline still requires its usable-mesh evidence and preserves its existing normalization/archive policies.

## Supported continuations

- Tripo reconnects to a recorded task ID. Fal model/image/audio reconnect to a supported queue response receipt. Meshy restores its endpoint, output format and preview/refine state.
- Fal recovery accepts the exact supported `https://queue.fal.run/<owner>/<app>/requests/<id>` route. Unsupported receipt forms remain nonresumable; the implementation does not repair or reinterpret provider URLs.
- Downloads can be repeated after polling the same task for a fresh result URL. Sketchfab resolves its recorded model UID again with GET. Signed URLs are not retained.
- OpenRouter currently returns a synchronous result without a reusable provider task ID. A result lost before it is saved locally cannot reconnect and is not resubmitted.
- Saved local files can enter the importer once if their bytes, metadata and registered identity still match the checkpoint. This continuation does not read a provider key or contact the provider, including for saved OpenRouter output.
- An import already admitted before reload is not replayed. Inspect its retained output before starting another job, especially for archive extraction or generated normalization variants.

Network continuations reacquire the provider key from the existing secure-store mechanism. If the key is unavailable, the job fails with `reconnect_key_unavailable`; it does not resubmit or retry credential lookup indefinitely.

## Output ownership and cancellation

Output allocation treats existing files, directories, metadata, links and registered identities as occupied. It uses a fresh slot and never adopts another asset's GUID. Writes use exclusive creation; a partial file can remain after failure so its outcome is not disguised by a second write.

Saved recovery compares both the file and its `.meta` identity. If metadata/GUID was absent at the checkpoint but appeared during reload, its origin cannot be established, even if Unity auto-import might have created it. Recovery conservatively refuses the import and retains output. Changing, deleting or substituting saved bytes or metadata also refuses import.

Cancellation records the local terminal state immediately and prevents later provider completions from staging/importing output. It does not forcibly stop remote generation or guarantee a refund. A synchronous Unity import already admitted may finish; cancellation is not asset rollback. Stale completions from the previous domain cannot overwrite a restored job.

## Stored information and limits

The checkpoint contains bounded task receipts, continuation options, original timing and saved-file identity. It excludes provider keys, prompts/request bodies, input images, raw provider errors and signed download URLs. An explicitly retained sanitized output filename can derive from the prompt; it remains an artifact identifier. Persisted failure text is a fixed safe summary, so detailed in-memory provider diagnostics are not preserved after reload. Existing historical records are not retroactively claimed free of diagnostics.

There are at most 64 active jobs and 100 retained terminal records. Active jobs are not silently evicted. Recovery records are bounded (16 KiB checkpoint, 1 MiB record, 16 KiB index, JSON depth 16); invalid or unsupported records cannot trigger a request/import. Failure to store a required checkpoint stops further side-effect admission.

## Durable journal and instance ownership

Validated continuation records live under `Library/MCPForUnity/AssetGen/<owner-prefix>/`. The owner derives from the project, machine and user; records verify the complete owner identity. The shorter directory prefix avoids inflating paths on Windows Editors. No private absolute path or user name is stored in the journal. A process instance identifier survives domain reload in SessionState and changes after a full restart.

An exclusive lock prevents two cooperating instances from resuming the same journal. Windows uses exclusive file sharing; Linux and macOS additionally require a nonblocking OS file lock, with no unlocked fallback. The OS releases the handle after process termination. These are local ownership controls, not protection against a process deliberately deleting live journal files. Do not remove the live journal directory while the Editor owns it. See the [Linux](https://man7.org/linux/man-pages/man2/flock.2.html) and [macOS](https://developer.apple.com/library/archive/documentation/System/Conceptual/ManPages_iPhoneOS/man2/flock.2.html) file-lock contracts.

Index and record publication uses adjacent flushed files and atomic replacement. The index is published before the first job record, so an interrupted initial write remains visible as unavailable evidence. No submission, file write or import is admitted until its required checkpoint succeeds. Unknown owners, damaged records and replaced evidence are retained. A damaged index blocks new admission until the retained work is verified. Individual damaged records do not hide other confirmed jobs.

Progress-only updates stay in SessionState; they do not rewrite an unchanged continuation checkpoint. The existing active-job and terminal-history bounds remain unchanged. Only verified evicted terminal records are removed; generated assets are never removed by retention. Legacy same-process history remains readable, while only validated job identities become durable records.

## Verification scope

Native Unity fixtures exercise receipt-based continuation without duplicate POSTs, ambiguous submission/import states, elapsed budgets, cancellation, corrupted and foreign records, storage failure, long project paths and existing task/memory lifetimes. The standalone `generation_journal` harness compiles the production journal, checkpoint validation, publication and lease sources. It verifies real competing processes and kills only its newly created writer at six persisted phases before restarting recovery. Its Unity-facing DTOs are stubs; it does not force-crash a user's Editor or claim power-loss transaction guarantees. The same harness runs on Windows, Linux and macOS CI without additional runtime packages or drivers.