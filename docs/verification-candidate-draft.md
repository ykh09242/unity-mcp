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

Git blob lookup preserves each committed pathname's exact UTF-8 spelling. Working-tree drafts and Player source verification require the same spelling in each physical directory entry. A host filesystem can open a differently cased or Unicode-equivalent name through its own lookup rules. The verifier refuses that alias even when its content digest matches. A working-tree draft reports `candidate_checkout_path_alias`, and Player pair reproduction records `path_alias` under `checkout_diagnostics` with no checkout digest. Use a checkout on a filesystem that preserves the reviewed names. The portable manifest still rejects two inputs with the same case-folded path.

The **Verification checkout paths** workflow runs on hosted Linux, macOS and Windows for relevant `beta` pushes and on demand. Each test constructs a Git tree with two exact names and checks that production Git reads return their committed bytes. Its separate checkout records the Git command's result, the raw Git path bytes, enumerated physical names, and which physical file each host lookup opened. The collision cases use case-only names and the NFC and NFD spellings of the same Hangul name. Each runs with distinct and identical content. The `verification-checkout-paths-<os>-<sha>-<attempt>` artifact retains the exact tested revision and the observations in `pytest-junit.xml`. These results describe the filesystem used by each runner.

## Check the anticipated merge

Pass the current receiving ref and reviewed head to inspect what Git would combine with its built-in merge behavior. Use Git 2.43 or newer and a full local checkout containing both commits. The selected Unity project must have a tracked `Assets` directory and a regular `ProjectSettings/ProjectVersion.txt` in the compared trees.

```sh
unity-mcp --no-project-context verification-candidate anticipate-merge --project . --target origin/main --head review-head --output review/merge.json
```

The operation computes the actual three-way result in a temporary bare Git repository using read-only access to the original objects. It records the base, target and head commit and tree IDs and the clean merged tree ID. The original checkout, index, refs and object database stay intact. The receiving commit supplies versioned attributes. Local configuration, external merge drivers and checkout filters are outside this prediction. If a parent changes a protected path, the operation reports `unavailable` before loading merge blobs.

The report compares static C# `Resources.Load`, `LoadAsync` and `LoadAll` lookups with the resource asset and `.meta` pairs tracked in each tree. When a branch moves a resource and the other introduces a call to its former key or folder, each parent can have no failures while the merged tree reports `key_not_found` at the new caller. The finding names the caller's merged Git blob, line, key and previous providers, including GUIDs and their merged Resources paths when available. Failures already present in either parent appear in the snapshot evidence. The coverage is `partial_static_resources`. Dynamic calls, custom loaders, package caches and Unity import or Player behavior need separate evidence.

The command returns `0` for a clean Git merge with no new covered findings, `1` for a merged-only finding or Git conflict, and `2` when the required Git objects or static inputs are unavailable. A conflict report gives repo-relative paths and Git's conflict types. Source analysis runs only for a clean merge tree. Loader findings use paths relative to the selected Unity project.

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
