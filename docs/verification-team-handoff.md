# Recompute a shared verification result offline

Use the `team` commands in the installed `unity-mcp-evidence` reader or under `unity-mcp verification-evidence`. A sender exports the observations of a specific comparison. A teammate needs the resulting JSON file, its separately communicated SHA-256 and the reader. The receiving computer can use a different account and filesystem layout.

## Export a Player change

Supply the original verification and settled Player file for each side. The exporter validates them with the same ownership, source, finalization and outcome rules as `verification-evidence compare`. Output must be a new file.

```sh
unity-mcp-evidence --format json team pack-change \
  --baseline before/verification.json before/run.json \
  --candidate after/verification.json after/run.json \
  --bundle handoff.json
```

Send the file and provide the returned `bundle_sha256` through an independently reviewed record. The receipt includes SHA-256 values of the original files and a separate digest of each exported projection. The whole-file digest detects a changed handoff. A teammate with the authorized originals can compare their hashes against the receipt. Without the originals the recipient relies on the sender's validation of them.

## Export a measured test selection

```sh
unity-mcp-evidence --format json team pack-selection \
  --selected selected.xml --full full.xml --affected-test Project.Navigation.RestoresRoute \
  --bundle selection-handoff.json
```

Use the instrumented NUnit cohorts supported by `verification-evidence selection`. Supply `--fallback fallback.xml` for a broader run. Execution identity, comparable fixture inputs, per-case terminal outcomes and run times are retained so the recipient can recompute coverage, missed failures and measured time savings. Optional source roles that were never collected appear as `absent`. To record an intentionally excluded source without sharing its observations, pass its input and `--withhold ROLE`. A required missing or withheld input, or an explicitly withheld fallback, gives an incomplete overall verdict.

## Read the handoff

```sh
unity-mcp-evidence --format json team replay handoff.json --sha256 REVIEWED_BUNDLE_SHA256
```

Replay checks the whole-file pin and each included projection, then runs the normal verdict algorithm. Exit 0 indicates a complete result with no new Player regression or a measured NUnit comparison. Exit 1 indicates a new or changed Player failure. Exit 2 indicates incomplete or incomparable inputs or failed admission. A measured NUnit result can contain missed failures; read the `assessment.selection.missed_failures` field.

The exporter allowlists comparison fields and assigns opaque identifiers with a fresh, private HMAC key for every export. Absolute paths, account names, game state values, logs and failure text stay with the originals. Equality of observed values within a handoff is preserved, including the distinction between an absent failure signature and an empty one. Separately exported handoffs have independent identifiers. Keys and original files are omitted from the handoff. Counts, outcomes, timing measurements, source digests and equality patterns remain visible to the recipient.

The hosted offline workflow applies both installed distributions to the same synthetic controls. A fresh second runner receives only the shareable files and the installed reader. It replays in a network namespace with only loopback, compares verdicts with the original computation and checks withheld and modified inputs. Native Player execution for a project is established by the corresponding source run.
