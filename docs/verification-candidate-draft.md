# Draft verification inputs from Git

From a trusted Git checkout, select the Unity project directory, a baseline commit and the side of the change to review.

```sh
unity-mcp --no-project-context verification-candidate draft --project . --baseline HEAD --selection working-tree --output working.json
unity-mcp --no-project-context verification-candidate draft --project . --baseline HEAD~1 --selection staged --output staged.json
```

The command writes a new review file or prints JSON to stdout when `--output` is omitted. The baseline resolves once to the commit recorded in the draft. A project can occupy a subdirectory of a larger Git checkout.

* `staged` compares Git blobs from the baseline and the index. Partial staging is preserved.
* `working-tree` compares baseline blobs with exact on-disk bytes for staged paths, Git-reported modifications and nonignored untracked paths. A working edit that undoes a staged change disappears from this comparison. Git ignore rules, sparse checkouts and index flags affect which paths Git reports. Line-ending conversions can produce distinct staged and working byte identities.

The command selects supported sources in `Assets` and `Packages` and includes each changed asset's source and metadata when available. An unsupported changed path contributes to the skip count. Unsafe metadata, links, unresolved index conflicts, path collisions and resource-limit failures stop the draft when encountered on a selected input. Supported inputs use the same file types as [Player verification registrations](verification-profiles.md#verify-a-registered-standalone-player).

`changed_paths` lists added, modified and deleted files. A GUID found under a different asset path appears in `asset_moves`. A metadata GUID change at the same path appears in `asset_replacements`. An asset removed with no surviving GUID match in the selection appears in `asset_deletions`. Missing GUID evidence leaves the file-level change visible. A draft captures up to 128 inputs per side, 4 MiB per file and 32 MiB per side. Capture while the index and working files are stable. Git and filesystem reads sample the checkout across several operations.

Review the `baseline` and `candidate` sections before using their `inputs`, `assets` and `source_revision` for a project-owned build. An empty candidate input map can represent deletion of the entire selected set. Select remaining project inputs explicitly before building a registered Player. The draft covers the observed changes. Add any unchanged dependencies the intended scenario requires and recompute the revision when the final input set changes. Project build and registration are separate operator actions.
