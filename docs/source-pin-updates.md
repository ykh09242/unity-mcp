# Reproducible source pin updates

Use the existing version helper to select **reviewed, locally available full commit IDs**. The helper never fetches, commits, pushes, installs dependencies or publishes a release.

```sh
python tools/update_versions.py --server-commit <40-hex-server-commit> --skill-commit <40-hex-skill-commit> --dry-run
python tools/update_versions.py --server-commit <40-hex-server-commit> --skill-commit <40-hex-skill-commit>
```

Review the dry-run file list and before/after SHA-256 values. The transaction updates the Unity package source, manifest launcher, canonical documentation examples, both committed skill mirrors and `release-source-inputs.json`. Unity and Python version numbers remain independent; version-number updates must be a separate command. Repeating the same selection makes no changes.

Before writing, the helper validates the fork remote, package/distribution identity, full commit availability, canonical source URL, manifest agreement, example counts and the selected skill mirrors. It refuses unknown skill files, unreviewed local edits, noncanonical paths and links/reparse points. Keep the checkout exclusive while applying; this is an offline authoring tool, not a concurrent filesystem security boundary.

When the selected server commit contains `Server/src/services/skills_bundle.json`, its bundled revision must equal the selected skill commit. Identical skill mirror bytes at a newer commit do not make the revisions interchangeable: the revision is part of the bundle payload and checksum. Select the skill revision already bundled by that server, or first prepare and validate a server commit with the refreshed bundle.

Both dry-run and apply validate the committed bundle before creating a transaction journal, staging files or changing public files. This standard-library-only check verifies the supported envelope, repository/revision metadata, payload checksum and the complete ordered file set, including exact raw committed bytes, sizes and file digests. Bundle comparisons preserve committed line endings, matching the verified generator; checkout/mirror normalization remains separate. The check validates frontmatter metadata shape; YAML syntax and derivation from `SKILL.md` remain the responsibility of `tools/generate_skill_bundle.py` and its existing contract tests. Older server commits without a bundled skill remain supported.

All replacement bytes and original backups are prepared first under the repository's `.work` directory. Each file replacement is atomic. Multi-file publication is **recoverable, not one operating-system atomic operation**: an interruption before finalization triggers rollback, while forced process termination can leave a partial working-tree update with a durable journal. Do not commit or publish that state. Another pin update refuses until recovery succeeds:

```sh
python tools/update_versions.py --recover-pins
```

Before cleanup, the complete apply or rollback atomically retires the active journal. Interruption during cleanup preserves that settled outcome; recovery only finishes deleting retired transaction files, including when the descriptor has already been removed. It never restores old bytes over a completed apply.

Recovery of an active transaction validates every affected path and backup before restoring original bytes. It refuses to overwrite a subsequent user edit, leaving the journal for inspection. An abrupt stop during preparation can leave an unpublished `.work/.source-pins-*` directory; package files have not changed at that point. Recovery may retain empty directories created for new mirror files. No unrelated directory is recursively removed.

After applying, review and commit the generated files as a work unit. `tools/check_release_evidence.py` validates the committed generated source-input record against package and manifest pins when present, while preserving compatibility with older commits without that record. Choose the actual package commit only after committing; the generated record does not invent that future commit identity or claim tests have passed.

## Detecting launcher drift

The launcher uses an immutable archive, so merging server changes does not automatically update what a newly installed client starts. Check committed server inputs without downloads or working-tree mutation:

```sh
python tools/check_source_pin_drift.py --revision HEAD
python tools/check_source_pin_drift.py --revision HEAD --require-current
python tools/check_source_pin_drift.py --revision HEAD --baseline <reviewed-base-commit>
```

The JSON report distinguishes `current`, `stale` and `unavailable`. It compares the entire committed `Server` tree, including new packaging files, while excluding `Server/tests/`, `Server/README.md`, `Server/DOCKER_OVERVIEW.md` and `Server/pyrightconfig.json`. Runtime resources under `Server/src` remain included even when they contain documentation. Pin-only README example changes are excluded so a refresh can point to a previously validated merged server commit instead of an impossible self-referential future commit. A changed tree that is later exactly restored is current. Uncommitted edits are intentionally outside this check.

Ordinary server changes report stale pins as an advisory and exit zero; they may merge before the final reviewed pin refresh. `--require-current` makes stale inputs fail with exit one. Supplying `--baseline` automatically enforces that same rule when the selected source pin changes, so a refresh cannot silently leave additional server changes behind. Invalid/inconsistent metadata or unavailable local history fails with exit two, including in advisory mode. Provide full history and repair metadata before retrying; this tool never fetches missing objects. Output includes at most 100 relative paths but always compares and counts every changed input.

The `Immutable server source` workflow runs on relevant beta pushes and pull requests with full history, read-only permissions, a JSON artifact and a check summary. It validates package, manifest and generated source-input agreement, then applies the baseline rule above. This reports drift without requiring a new launcher pin for every intermediate server PR. It does not publish packages or claim that the selected server commit passed tests. Before a release, run strict mode and inspect CI for the chosen immutable server commit; before merging a pin refresh, verify its archive startup and applicable PR CI. Existing release evidence checks remain separate.

Full checkout history includes commits reachable from fetched refs. A source pin can
refer to an earlier work-unit commit whose branch was later squash-merged and deleted;
that commit may be absent even with `fetch-depth: 0`. Before the offline comparison,
the workflow explicitly runs a separate network preparation step:

```sh
python tools/prepare_source_pin_history.py --revision <checked-commit>
```

This helper first validates the committed package, manifest and generated source-input
agreement using the same metadata rules as the checker. An available local commit
needs no network. A missing pin is fetched by its validated full SHA only from the fixed
public fork `https://github.com/ykh09242/unity-mcp.git`, with a 60-second timeout and
depth one. The helper does not follow metadata-supplied URLs or configured remotes,
prompt for credentials, fetch tags/submodules, update branch refs or overwrite
`FETCH_HEAD`. It verifies the fetched object is exactly the requested commit.

Invalid metadata, an existing non-commit object, a failed/timed-out fetch or a still
missing object fails preparation with exit two and sanitized diagnostics. The workflow
still runs the offline checker to preserve its `unavailable` report on preparation
failure. Neither helper converts unavailable history into a passing freshness result.
`check_source_pin_drift.py` remains offline and read-only; local authoring and release
checks continue to require their selected commits to be available locally.
