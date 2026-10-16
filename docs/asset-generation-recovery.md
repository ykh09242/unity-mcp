# Generation recovery after an Editor domain reload

Generation jobs can continue from a recorded provider task after scripts reload in the same running Unity Editor. Keep polling the original local `job_id`; a restored job does not submit the original request again. The original 600-second budget includes reload time. Closing or crashing the Editor loses this session history; this is not restart recovery.

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

The session checkpoint contains bounded task receipts, continuation options, original timing and saved-file identity. It excludes provider keys, prompts/request bodies, input images, raw provider errors and signed download URLs. An explicitly retained sanitized output filename can derive from the prompt; it remains an artifact identifier. Persisted failure text is a fixed safe summary, so detailed in-memory provider diagnostics are not preserved after reload. Existing historical records are not retroactively claimed free of diagnostics.

There are at most 64 active jobs and 100 retained terminal records. Active jobs are not silently evicted. Recovery records are bounded (16 KiB checkpoint, 256 KiB record, 16 KiB index, JSON depth 16); invalid or unsupported records cannot trigger a request/import. Failure to store a required checkpoint stops further side-effect admission.
