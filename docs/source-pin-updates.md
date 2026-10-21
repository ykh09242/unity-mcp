# Reproducible source pin updates

Use the existing version helper to select **reviewed, locally available full commit IDs**. The helper never fetches, commits, pushes, installs dependencies or publishes a release.

```sh
python tools/update_versions.py --server-commit <40-hex-server-commit> --skill-commit <40-hex-skill-commit> --dry-run
python tools/update_versions.py --server-commit <40-hex-server-commit> --skill-commit <40-hex-skill-commit>
```

Review the dry-run file list and before/after SHA-256 values. The transaction updates the Unity package source, manifest launcher, canonical documentation examples, both committed skill mirrors and `release-source-inputs.json`. Unity and Python version numbers remain independent; version-number updates must be a separate command. Repeating the same selection makes no changes.

Before writing, the helper validates the fork remote, package/distribution identity, full commit availability, canonical source URL, manifest agreement, example counts and the selected skill mirrors. It refuses unknown skill files, unreviewed local edits, noncanonical paths and links/reparse points. Keep the checkout exclusive while applying; this is an offline authoring tool, not a concurrent filesystem security boundary.

All replacement bytes and original backups are prepared first under the repository's `.work` directory. Each file replacement is atomic. Multi-file publication is **recoverable, not one operating-system atomic operation**: an interruption before finalization triggers rollback, while forced process termination can leave a partial working-tree update with a durable journal. Do not commit or publish that state. Another pin update refuses until recovery succeeds:

```sh
python tools/update_versions.py --recover-pins
```

Before cleanup, the complete apply or rollback atomically retires the active journal. Interruption during cleanup preserves that settled outcome; recovery only finishes deleting retired transaction files, including when the descriptor has already been removed. It never restores old bytes over a completed apply.

Recovery of an active transaction validates every affected path and backup before restoring original bytes. It refuses to overwrite a subsequent user edit, leaving the journal for inspection. An abrupt stop during preparation can leave an unpublished `.work/.source-pins-*` directory; package files have not changed at that point. Recovery may retain empty directories created for new mirror files. No unrelated directory is recursively removed.

After applying, review and commit the generated files as a work unit. `tools/check_release_evidence.py` validates the committed generated source-input record against package and manifest pins when present, while preserving compatibility with older commits without that record. Choose the actual package commit only after committing; the generated record does not invent that future commit identity or claim tests have passed.
