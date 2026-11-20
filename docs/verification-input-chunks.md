# Chunked Player candidate inputs

Use a version 2 candidate registration to verify a larger explicit selection.
The Player bundle and the build source revision use the same format as a version
1 registration. For example, 150 scripts and their 150 metadata files can be
captured as three groups of 100 paths. Sort the complete project-relative paths
in Unicode code point order and split them into consecutive groups.

With `UNITY_MCP_VERIFICATION_ROOT` set to an absolute operator-controlled
directory, run the offline capture once per group. Repeat `--path` for every
selected file, including each desired metadata file.

```sh
unity-mcp --no-project-context verification-candidate input-chunks capture --candidate-id lighting-v2 --project /workspace/Game --path Assets/A.cs --path Assets/A.cs.meta
unity-mcp --no-project-context verification-candidate input-chunks capture --candidate-id lighting-v2 --project /workspace/Game --path Assets/B.cs --path Assets/B.cs.meta
unity-mcp --no-project-context verification-candidate input-chunks index --candidate-id lighting-v2 --chunk FIRST_SHA256 --chunk SECOND_SHA256
```

Capture writes an immutable `input-chunks/<sha256>.json` below the candidate
directory and prints its digest, entry count and byte length. An identical
capture reuses the file. Rerun capture for a changed group and pass its new
digest with the other original digests to `index`. This step reads the
registered chunks. Use the resulting `source_revision` when building the
Player. Include the printed `schema_version`, `source_revision` and
`input_chunks` in `candidate.json` alongside the ordinary project, scenario,
build identity and optional checks. Omit the version 1 `inputs` field.
Publish that registration after all referenced chunks and the matching build
are present. Keep the previous build and registration under their own candidate
ID when historical replay is needed.

Each chunk is a UTF-8 JSON object containing one `inputs` map with 1 to 128
paths and their lowercase SHA-256 values. Each registration lists 1 to 8
chunks, with a maximum of 1,024 paths in total. Each chunk and registration
is limited to 64 KiB. Paths from different chunks must belong to disjoint,
ordered ranges. Duplicate JSON keys, case aliases and a complete inventory
whose sorted source revision disagrees with the registration are rejected.
Each source read is limited to 4 MiB. The 32 MiB budget applies to the entire
live observation, including a baseline's paths.

Call `manage_verification` with `action="prepare"` or `action="run"` and
the existing `candidate_id` option. The selected Editor supplies the project
root on the server. Each observation reads all chunk identities and live source
bytes, then rechecks sampled file identity, size and timestamps for concurrent
changes. These observations occur at preparation and execution boundaries.
An unrelated edit between two samples that restores identical bytes may be
unobservable.

`comparison.input_inventory` retains the prepared evidence. A completed run
also records `latest_input_inventory` with the latest successful observation.
For each selected candidate or baseline, inspect the ordered chunk identities,
declared and verified path counts, global source revision, registration state,
live count, live revision and live state. The live state is `matched`, `stale`,
`missing` or `unknown`. An unavailable, changed or missing registered chunk
leaves other valid groups visible and prevents dispatch even in frozen mode.
An unchanged historical build in frozen mode may have live differences, which
remain visible in its report. A change since preparation prevents a passing
verdict in either source mode.

When selected check explanations exceed 64 KiB, the comparison retains every
test, scenario and fallback profile ID. It records a bounded preview with each
row's `reason_count` and `reasons_truncated`, plus `check_reason_count`,
`unmapped_path_count`, `unmapped_paths_truncated` and `evidence_truncated`
for the selection. Mapping coverage and outstanding work retain their original
meaning. If the identities and counts alone exceed the selection limit,
preparation returns `candidate_selected_check_ids_limit` before creating a
handle or starting a check.

For Player registration, permissions and the preparation lifecycle, see
[verification profiles](verification-profiles.md).
