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

## Export a changed asset and its selected scene tests

Export two complete `unity-mcp verification-candidate dependency-scan --output`
inventories using the same scene root and graph scope at distinct commits. Capture
commit-bound Editor graph pages for both scans. Use the GUID of an asset present
in both scene inventories. Supply the Player candidate manifest with the scene
and asset GUIDs and explicit scene `mappings[].tests` entries. Include each
version-two manifest input chunk using a separate `--mapping-chunk PATH`.
Version-one manifests carry inline input hashes. Supply a selected instrumented
NUnit result recording the candidate scene and asset GUIDs and source digests.
[Dependency scan guide](verification-dependency-scans.md)

```sh
unity-mcp-evidence --format json team pack-impact \
  --asset-guid REVIEWED_ASSET_GUID \
  --baseline before.jsonl --candidate after.jsonl \
  --mappings candidate.json --mapping-chunk input-chunks/REVIEWED_CHUNK_SHA.json \
  --selected selected.xml --bundle impact-handoff.json
```

Replay compares the asset path, source digest and metadata digest for the same
GUID. It follows the included native dependency edges from scene to asset,
including through prefabs. The result lists selected passing or failing case
observations that match the scene's declared mapping and the fixture's recorded
asset inputs. Replay checks the manifest's source and metadata digests against
the scene and changed asset in the candidate inventory. One witnessed route is
sufficient. An unobserved route leaves
consumption unknown. Coverage is limited to the selected scene, asset and test
capture. Runtime behavior and project-wide test coverage need their own evidence.

`assessment.unavailable_conclusions` identifies what the teammate could not
reconstruct. For example, without the baseline inventory, the candidate route
and its selected tests can remain visible while `asset_changed` is `null`.
Without the candidate inventory, that route and the test attribution are also
unknown. A missing mapping or mismatched NUnit input affects the attribution of
selected tests.

## Export an interrupted prefab write and its continuation

Save the receipt-owned JSON responses from `reconcile_prefab`, the approved
`replan_prefab` on a fresh operation ID, and the successor `create_prefab` or
`register_prefab`. Save the responses from `unity-mcp --format json receipt
execute`, or use the original receipt IDs with `receipt get --include-result`
when those responses were retained. Use the same authenticated receipt owner,
project, and store and capture the reconciliation before claiming the
continuation. [Prefab copy and registration recovery](addressable-prefab-recovery.md)

```sh
unity-mcp-evidence --format json team pack-recovery \
  --reconcile reconciled.json --replan approved.json --resumed registered.json \
  --bundle recovery-handoff.json
```

Replay checks the checkpoint phase, observed prefab identity, receipt scope,
original and replacement Editor sessions, and the approved predecessor link.
It recovers the last confirmed write, the approved next step, and evidence of
the resumed invocation and its settlement. If the successor's result was
withheld, the last confirmed write and an independently observed approval can
still be shown. Older responses without workflow provenance remain explicitly
unavailable for attribution. A native invocation whose settlement is unknown
leaves `resumed_write_confirmed` unknown even when its start was observed.

## Read the handoff

```sh
unity-mcp-evidence --format json team replay handoff.json --sha256 REVIEWED_BUNDLE_SHA256
```

Replay checks the whole-file pin and each included projection, then recomputes
the assessment from the retained observations. Exit 0 covers a reconstructed
impact or recovery, a complete Player comparison with no new regression, or a
measured NUnit comparison. Exit 1 indicates a new or changed Player failure.
Exit 2 indicates incomplete or incomparable inputs or failed admission. A
measured NUnit result can contain missed failures. Inspect
`assessment.selection.missed_failures` for the observed misses. For impact and
recovery, omit an unavailable source option or pass the source with `--withhold
ROLE` to retain its digest without including its observations.

The exporter allowlists comparison fields and assigns opaque identifiers with a fresh, private HMAC key for every export. Absolute paths, account names, game state values, logs and failure text stay with the originals. Equality of observed values within a handoff is preserved, including the distinction between an absent failure signature and an empty one. Separately exported handoffs have independent identifiers. Keys and original files are omitted from the handoff. Counts, outcomes, timing measurements, source digests and equality patterns remain visible to the recipient.

The verified pin checks the exact file provided to the teammate. The opaque
identifiers preserve equality between observations, including the service's
receipt-owned predecessor link. The teammate relies on the exporter for the
origin and validation of the underlying graph and receipt records. Authorized
reviewers can compare original artifact digests with the receipt.

The hosted offline workflow exercises both installed distributions with synthetic
controls. A fresh second runner receives only the shareable files and the
installed reader. It replays in a network namespace with only loopback and
checks the reconstructed paths, test results, continuation steps, and partial
or modified handoffs. The native Addressables recovery profile exercises the
underlying checkpoint identities across an Editor restart.
