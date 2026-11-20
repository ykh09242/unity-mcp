# Draft verification inputs from Git

From a trusted Git checkout, select the Unity project directory, a baseline revision and the side of the change to review.

```sh
unity-mcp --no-project-context verification-candidate draft --project . --baseline HEAD --selection working-tree --output working.json
unity-mcp --no-project-context verification-candidate draft --project . --baseline HEAD~1 --selection staged --output staged.json
unity-mcp --no-project-context verification-candidate draft --project . --baseline origin/main --candidate-ref review-head --selection committed --output review/draft.json
```

The command writes a new review file or prints JSON to stdout when `--output` is omitted. The baseline resolves once to the commit recorded in the draft. A project can occupy a subdirectory of a larger Git checkout.

* `staged` compares Git blobs from the baseline and the index. Partial staging is preserved.
* `working-tree` compares baseline blobs with exact on-disk bytes for staged paths, Git-reported modifications and nonignored untracked paths. A working edit that undoes a staged change disappears from this comparison. Git ignore rules, sparse checkouts and index flags affect which paths Git reports. Line-ending conversions can produce distinct staged and working byte identities.
* `committed` resolves both refs and compares the head Git tree with its unique merge base with the requested baseline. The draft records `base_commit` for the requested base tip, `baseline_commit` for the merge base, and `candidate_commit` for the head tip. The index, untracked files and working source bytes do not participate. Supply refs available in a full local checkout. Shallow history, unrelated commits and multiple best merge bases produce an error.

The command selects supported sources in `Assets` and `Packages` and includes each changed asset's source and metadata when available. An unsupported changed path contributes to the skip count. Unsafe metadata, links, unresolved index conflicts, path collisions and resource-limit failures stop the draft when encountered on a selected input. Supported inputs use the same file types as [Player verification registrations](verification-profiles.md#verify-a-registered-standalone-player).

`changed_paths` lists added, modified and deleted files. A GUID found under a different asset path appears in `asset_moves`. A metadata GUID change at the same path appears in `asset_replacements`. An asset removed with no surviving GUID match in the selection appears in `asset_deletions`. Missing GUID evidence leaves the file-level change visible. Exact allowlisted project configuration files use raw hashes, with package-lock comparison also recording whether the dependency resolution changed. Keep the index and working files stable when capturing a staged or working-tree draft.

## Large drafts and registration inputs

Each side keeps its inline `inputs` map when it has at most 128 inputs and fits the 64 KiB registration budget with room for build fields. Larger sides use ordered `input_chunks`. The command selects up to 1,024 paths per side, including metadata, subject to eight chunks of 128 paths and 64 KiB each. File bytes are limited to 4 MiB per file and 32 MiB per side. Long names can reach the chunk byte limit with fewer paths. Split the staging selection or use smaller commit ranges when a selection exceeds the limits.

A chunked draft uses schema version 2. It saves digest-addressed JSON files in `input-chunks` beside the draft by default. Supply `--chunk-directory` to place that folder under another directory and to allow a large draft on stdout. The source revision is computed over all selected path/digest pairs and is independent of chunk boundaries.

Keep the JSON and `input-chunks` folder together when transferring a draft. With `UNITY_MCP_VERIFICATION_ROOT` set to your registration directory, import each side you need under its own candidate ID.

```sh
unity-mcp --no-project-context verification-candidate draft-inputs --draft review/draft.json --side baseline --candidate-id review-base
unity-mcp --no-project-context verification-candidate draft-inputs --draft review/draft.json --side candidate --candidate-id review-head
```

The command validates ordered chunks and their combined revision, copies the chunks into the candidate's registration directory, and prints the input fields for that side. Use the same `--chunk-directory` on import if you stored the chunks outside the draft directory. Retrying reuses matching chunk files. A chunked registration can include a GUID for every source and metadata pair within the selected limits.

Review both sections before using the input fields for a project-owned build. An empty candidate input map can represent deletion of the entire selected set. Select remaining project inputs explicitly before building a registered Player. Add unchanged dependencies required by the intended scenario and recompute the revision when the final input set changes. Project build and registration are separate operator actions. See [Review project configuration and additional Player inputs](verification-dependency-closure.md) for configuration evidence and dependency closure.
