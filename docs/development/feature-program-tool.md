# Feature program coordination tool

`tools/feature_program.py` keeps a reviewed feature backlog, ownership, evidence and recovery
checkpoints on disk. It uses the Python standard library and performs no GitHub, agent,
network or Unity operations. Use the existing Python environment described in
[the development formatting guide](../FORMATTING.md).

The [work-program workflow](work-program-workflow.md) defines director/sub-director duties,
staffing, handoffs, ongoing direction review, PR cleanup and the next-goal boundary.

## Private state and public code

The default program directory is `.work/programs/default/`, which is ignored by Git. Keep
actual source reports, plans, machine paths, agent identities, task packets, checkpoints
and execution evidence there. Public commits should contain the reusable tool, synthetic
tests and sanitized documentation. Never force-add the private program directory or paste
its content into a public PR. A code push does not back up local program state.

Use named programs under `.work/programs/` for separate objectives, and `.work/handoffs/`
for session continuity. Select the same explicit program path in the CLI and web service.
Legacy paths remain explicit choices; the default does not search for or initialize an old
program automatically.

The importer accepts a nonempty set of portable task IDs with matching source and planning
records. Supply a local source report, a matching `planning.json` and the full commit SHA
originally assessed by that report. Initialization refuses to overwrite an existing registry.
If a report was moved, `--reference-base` retains its original repository-relative link base.

For a new program, generate starting material in an ignored directory:

```sh
python tools/feature_program.py --program-dir .work/programs/example starter --program-id example-program --objective "Improve the agreed workflow" --criterion "The agreed user flow is verified" --task EXAMPLE-1
```

Replace the example objective and criterion with the human's actual decision. Complete the
generated source, plan and role instructions before initialization; starter files are not
passing evidence. An uninitialized starter has no `backlog.json`; `status`, `check` and
`packet` require explicit initialization. Initialization and rendering preserve an authored
`README.md` byte for byte; the starter refuses to overwrite existing material. Generated
boards, checkpoints and task packets describe initialized work. Use the same `--program-dir`
for every subsequent command. A new major
objective with a different task set uses another directory, preserving the completed program.

```sh
python tools/feature_program.py --program-dir .work/programs/example init --report .work/programs/example/source/feature-opportunities.md --planning .work/programs/example/planning.json --reference-base .work/programs/example/source --review-baseline <reviewed-commit-sha>
python tools/feature_program.py --program-dir .work/programs/example check
python tools/feature_program.py --program-dir .work/programs/example status
python tools/feature_program.py --program-dir .work/programs/example next
python tools/feature_program.py --program-dir .work/programs/example packet EXAMPLE-1
```

Replace the example commit placeholder with a real SHA. `init --help` documents explicit
report/planning paths. Set `--reference-base` to where the report's relative links were authored,
including when replacing or copying the generated example source. Every example below keeps
the same program directory; substitute your own consistently. Use `--repo` for an explicit
repository root. Tests create their own isolated
synthetic reports; they do not depend on a user's private backlog.

## Relocating a private program

Stop services and coordinate with writers before moving an existing program. Choose an
absent destination inside the same repository; linked, overlapping or occupied destinations
and active nested programs are rejected. Use the accepted director ID, or `leader` before
director appointment:

```sh
python tools/feature_program.py --program-dir .omo/example-program relocate --destination .work/programs/example --owner director-example --reason "Use the neutral local workspace"
python tools/feature_program.py --program-dir .work/programs/example check
```

Relocation verifies copied bytes, retains a manifest and original registry backup, and
preserves task history and frozen evidence. It changes source location metadata only where
the source or its reference base is inside the moved subtree. External reference bases stay
unchanged. The old directory is retained with `RELOCATED.json`; normal reads and writes reject
that archive and report the new explicit path. Update launch commands and current handoff
links after verifying the destination. Do not rewrite historical receipt contents.

An interrupted destination remains guarded by `RELOCATION_INCOMPLETE.json`. A verified ready
copy can finish publication through the explicit recovery command, which rechecks the source
and copy before activating it:

```sh
python tools/feature_program.py --program-dir .work/programs/example relocate-recover --owner director-example --reason "Recover the verified interrupted relocation"
```

An incomplete copy cannot be activated. Preserve its diagnostic records, inspect the failure
and retry from the still-authoritative source to another absent destination. Never remove
markers or overwrite a destination to bypass verification. Stop and investigate source drift;
copying an active registry by hand can create divergent histories.

## Optional inventory and execution gates

Existing programs retain their original claim/submit behavior until an appointed current
director explicitly runs `phase-adopt`. Adoption is recorded at the current time. Real earlier
inventory/review/acceptance documents may be referenced as evidence, but adoption does not
invent historical enforcement or claim that implementation was previously gated by this tool.
The gate implementation itself can be built under an already authorized human review.

The sequence is adoption, inventory freeze, independent candidate review, then director
execution acceptance. Obtain current binding values before preparing each mutation:

```sh
python tools/feature_program.py --program-dir .work/programs/example phase-binding
python tools/feature_program.py --program-dir .work/programs/example phase-adopt --input evidence/_phases/attempt-1/adopt.json
python tools/feature_program.py --program-dir .work/programs/example inventory-freeze --input evidence/_phases/attempt-1/freeze.json
python tools/feature_program.py --program-dir .work/programs/example candidate-review --input evidence/_phases/attempt-1/review.json
python tools/feature_program.py --program-dir .work/programs/example execution-accept --input evidence/_phases/attempt-1/accept.json
```

Each JSON receipt copies `program_id`, `objective_id`, `objective_digest`,
`task_definition_digest`, `planning_digest`, `task_ids` and `expected_revision` from the latest
`phase-binding` result and supplies nonblank `actor_id` and `reason`. `expected_revision` is
the SHA256 of canonical registry JSON, checked under the mutation lock; it is a transaction
revision token, not a lasting approval identity. Obtain it again after every registry mutation.
Receipts and optional `evidence` paths must use unique immutable filenames beneath
`evidence/_phases/attempt-1/`. Every successful transition retains the receipt and its hashes.

| Command | Additional receipt fields |
| --- | --- |
| `phase-adopt` | Current director `actor_id`, `acknowledged: true`. For an older registry without `planning_path`, provide the repository-relative path to the original frozen planning input as `planning_path`. |
| `inventory-freeze` | Current director `actor_id`, `candidate_path` beneath the phase evidence directory, its byte SHA256 as `candidate_digest`, explicit unique `inventory_authors`, and exact `units: [{"id": "EXAMPLE-1", "scope": ["path/to/source.py"]}]`. |
| `candidate-review` | Independent reviewer `actor_id`, `reviewer_role: "independent"`, current `inventory_digest`, `candidate_digest` and `units` from `phase-binding`, `verdict: "accepted"` or `"rejected"`, and `unresolved_findings`. Accepted reviews require an empty findings list. |
| `execution-accept` | Current director `actor_id`, current `inventory_digest`, `candidate_digest` and `units` from `phase-binding`, and `acceptance_complete: true`. A current accepted independent candidate review is required. |

The reviewer must differ from the director, freezing actor and every declared inventory
author. These identities are recorded local facts, not authentication or native-agent liveness
proof. Every current declared task must appear exactly once in `units`, with its complete
declared `candidate_paths`. Claims may use a file inside an accepted directory, but cannot
claim an ancestor or another unit's paths. Claim and submit enforce the same acceptance from
their core methods; adopting with existing claims holds their submission until acceptance.

Bindings cover frozen candidate/planning bytes, lossless task definitions, declared paths,
dependencies, checks and the current objective identity, criteria and task membership.
Consequential definition or accepted-scope changes require a new freeze, independent review
and acceptance. A rejected or repeated review clears acceptance. Existing implementation
source revalidation, submission and review freshness checks remain separate. Implementation
edits, lifecycle progress, actors, checkpoints, events, evidence and runtime next actions do
not invalidate a current planning approval. Frozen evidence still must retain its exact bytes.

## Discovery-to-execution succession

Use explicit succession when a discovery program has no current claims and all its tasks
are planned, and a different execution program is already initialized. Released historical
attempts remain intact. This operation identifies a successor; it does not copy/reset work,
complete discovery tasks or grant delivery authority. Obtain each program's canonical revision
token (`phase-binding` where an objective is recorded, or SHA256 of canonical `backlog.json`).

```sh
python tools/feature_program.py --program-dir .work/programs/discovery program-succeed --successor .work/programs/execution --successor-id execution-program --expected-revision <discovery-revision> --successor-revision <execution-revision> --owner director-example --reason "Continue the approved scope in the initialized execution program"
```

Both program locks and frozen provenance are checked before publication. The successor must
have its stated distinct program identity and repository-relative path; self, overlapping,
linked, nested/ambiguous or uninitialized targets and stale revisions are rejected. The
predecessor's original registry, source, attempts and prior event bytes stay unchanged.
`SUCCEEDED.json` and `PREDECESSOR.json` record exact identities, paths, revisions and reason.
Normal predecessor registry reads/writes reject the archive and name the explicit successor; ordinary
successor progress remains allowed after publication.

Publication guards the predecessor first. An interruption after that guard leaves an explicit
`SUCCESSION_INCOMPLETE.json`; the predecessor stays blocked and a guarded successor cannot
execute. Do not remove markers or reactivate discovery. Recover at the predecessor:

```sh
python tools/feature_program.py --program-dir .work/programs/discovery program-succeed-recover --owner director-example --reason "Finish the unchanged interrupted publication"
```

Recovery locks both programs, verifies exact predecessor bytes, identities, frozen successor
source/task/planning provenance and existing markers, then finishes the same publication.
If the successor was still writable before its guard or was already activated when the final
predecessor cleanup failed, verified runtime progress remains intact; recovery does not reset
its registry to the initial revision. `SUCCESSION_RECOVERY.json` records that observed revision
and the recovery reason. Changed frozen provenance or conflicting data stays guarded for
investigation. A failure before central registration leaves the original programs
unchanged and permits a normal retry. A durable registered or publishing entry instead
requires exact recovery or its permitted explicit reconciliation, even when no local guard
exists yet. Registered cancellation is available only before publishing begins and with
unchanged endpoint marker inventory. An interrupted transition never labels discovery done.

## Reviewed task admission and active membership

After explicit phase adoption and current accepted execution scope, the current director
may add reviewed work or amend membership of the same active human objective. Neither
command implicitly adopts a legacy program or changes its criteria. Original report and
planning bytes, task source sections, attempts, events and evidence remain intact. A new
report and plan form an immutable supplemental intake; report/plan IDs must match exactly
and be disjoint from every retained task, including case collisions. New tasks start
`planned`, unowned, with empty attempts and fresh claim revalidation required.

```shell
python tools/feature_program.py --program-dir .work/programs/example admission-binding --input evidence/_planning/attempt-1/proposal.json
python tools/feature_program.py --program-dir .work/programs/example task-admit --input evidence/_planning/attempt-1/intake-1.json
python tools/feature_program.py --program-dir .work/programs/example objective-membership --input evidence/_planning/attempt-1/membership-1.json
```

`admission-binding` is read-only: it creates no lock, files or authorization. Supply the
proposal fields below using the actual current `phase-binding.expected_revision`. The
result supplies `before_binding`, `after_binding`, complete `units`, exact `membership`
and the same real pre-state revision. The stable future binding includes supplemental
source/plan content hashes and current membership revision, while excluding recording
timestamps, history observations and relocation locators. It still includes all retained
task definitions and paths; membership exclusion does not remove provenance units.

| Proposal field | Required meaning |
| --- | --- |
| `schema_version`, `operation`, `id` | Version 1, `task-admit` or `objective-membership`, and a new portable transaction ID. Committed IDs cannot be reused. |
| `program_id`, `actor_id`, `objective_id`, `reason`, `expected_revision` | Exact current program, accepted director, active objective, nonblank rationale, and current canonical registry revision. |
| `source` | For admission: repository-relative `path`, actual byte `sha256`, exact ordered `task_ids`, authored `review_baseline` and safe `reference_base`. Keep complete expected-result and acceptance prose in the existing `### ID. Title` format. |
| `planning` | For admission: repository-relative frozen `path` and actual byte `sha256`. The supplemental JSON contains exactly `schema_version: 1` and `tasks`; it cannot change workstreams or verification policy. |
| `intake_authors` | For admission: explicit unique authors, all included among renewed inventory authors so none can self-review. |
| `membership` | Exact `before` and `after` ID lists plus `added` and `removed` lists matching that delta. Every admitted ID must be newly included. Unknown, empty, duplicate and unchanged membership is rejected. |
| `category_placements` | When categories are authored: one `{"id": "NEW-1", "category_id": "existing-leaf"}` per added task, compatible with its existing workstream. Otherwise retain workstream-root fallback. |
| `inventory` | Optional preview convenience: `candidate_path`, actual `candidate_digest`, and explicit `inventory_authors`. Binding output then includes the deterministic `inventory_digest` for future review receipts. Actual receipt and candidate bytes are independently checked at commit. |

Supplemental task metadata declares portable `id`, positive `phase`, existing `workstream`,
nonempty `candidate_paths`, `depends_on`, `related_to`, `conflict_groups`, valid
`required_checks`, `next_action`, nonblank `expected_result` and nonempty unique
`acceptance_criteria`. `planning_note` is optional. Runtime keys and unsupported metadata
are rejected. Required paths, checks, references and dependency cycles use existing
validators; report prose remains lossless and its completeness is independently reviewed.

Copy `before_binding` and `after_binding` into a new final receipt beneath
`evidence/_planning/attempt-1/`. Add nonempty `evidence` referencing the actual independent
findings and director decision in that directory, and a `renewal` object:

```json
{
  "renewal": {
    "inventory": "evidence/_phases/attempt-1/intake-1-inventory.json",
    "review": "evidence/_phases/attempt-1/intake-1-review.json",
    "acceptance": "evidence/_phases/attempt-1/intake-1-acceptance.json"
  },
  "evidence": ["evidence/_planning/attempt-1/intake-1-independent-review.md"]
}
```

Each new phase receipt uses every field from `after_binding`, complete prospective `units`,
the same real pre-state `expected_revision`, and `actor_id`/`reason`. Inventory adds the
candidate path/hash and all authors. Review adds `reviewer_role: "independent"`,
`verdict: "accepted"`, `unresolved_findings: []`, candidate hash and inventory digest.
Acceptance adds `acceptance_complete: true`, candidate hash and inventory digest. Existing
phase independence and evidence rules apply. Do not run standalone future phase commands
first: the combined operation validates all three against the prospective state in memory,
records current-time renewal observations, and publishes tasks, membership and gates once.
These observations do not fabricate earlier persisted gate transitions. A concurrent
runtime update needs a fresh transaction revision and refreshed final receipt; unchanged
stable future scope can reuse its real independent review.

Membership-only receipts omit source/planning/intake authors and use the same binding and
complete-scope renewal. Changes retain goal ID, title, criteria and past major-objective
history, appending a separate monotonic `membership_revision`/`membership_history` with
before/after, added/removed IDs and rationale. Excluded work remains recorded and is neither
done nor retired. New claim/submit operations reject excluded tasks for that amended goal.
Release owned active/review/blocked work before removal; retained members cannot depend
on an excluded unfinished prerequisite. Completion binds exact current members. After
amendment, both the completion receipt and its independent integrated-review artifact
require the current `membership_revision`, so removal/re-addition cannot revive old
aggregate acceptance even when the final member IDs match an earlier revision. Missing
criterion impact remains unknown rather than inferred.

Admission preflights full validation and document budgets, rehashes frozen inputs and
publishes under the current lock. Failure before the canonical write preserves prior
registry bytes. A derived-render failure after commit identifies the committed revision
and requires `render` recovery; retrying the intake must not duplicate it. Check, packet,
snapshot and relocation resolve each task's actual original or supplemental source. Read
snapshots report missing/drifted documents explicitly; strict checks and mutations reject
invalid frozen bytes. Relocation preserves receipt bytes and changes current locators only.

## Planned metadata and execution priority

The current accepted director can amend one or a bounded batch of planned, unowned,
current-objective tasks. A released task with prior attempts remains eligible; its
attempts and evidence are retained. Active, review, blocked, done, disposed, excluded
and unknown targets are rejected. These commands require prior phase adoption and
current acceptance, the exact active objective/current revision and an explicit reason.
They never change membership, its revision/history, original source/planning files,
task acceptance prose, categories, ownership or lifecycle.

```shell
python tools/feature_program.py --program-dir .work/programs/example admission-binding --input evidence/_planning/attempt-1/plan-proposal.json
python tools/feature_program.py --program-dir .work/programs/example task-plan --input evidence/_planning/attempt-1/plan-1.json
python tools/feature_program.py --program-dir .work/programs/example task-prioritize --input evidence/_planning/attempt-1/priority-1.json
```

Use the same version/program/director/objective/revision/reason/unique-ID fields as
admission. For `task-plan`, supply `changes` with exact matching before/after field
sets, for example:

```json
{
  "changes": [
    {
      "id": "UNIT-1",
      "before": {"depends_on": [], "next_action": "Inspect existing scope"},
      "after": {"depends_on": ["UNIT-0"], "next_action": "Inspect prerequisite integration"}
    }
  ]
}
```

Only `candidate_paths`, `depends_on`, `related_to`, `conflict_groups`, `required_checks`,
`next_action`, `planning_note`, `phase` and existing `workstream` are allowed. Existing
path/reference/cycle/check-policy and category/workstream validators apply. Category
membership is preserved; incompatible placement is rejected. A changed planned scope
cannot overlap currently owned scope or conflict groups. Planning eligibility does not
prove readiness: dependencies, collisions and fresh claim revalidation still apply.

For `task-prioritize`, use the same `changes` array with scalar values:

```json
{"changes": [{"id": "UNIT-1", "before": null, "after": -10}]}
```

Priority accepts signed 32-bit integers only; booleans, floats and overflow are rejected.
Lower sorts first. `null` means unspecified and removes a prior explicit value; explicit
zero remains distinct. Missing values use neutral zero for sorting. The first explicit
priority transaction activates priority, then phase, then portable task-ID ordering;
activation persists even if all values are later removed. Untouched legacy queues keep
their existing phase/array ordering. Priority remains valid after the task is claimed
or completed and never grants claim eligibility.

The read-only binding response adds computed `renewal_required`, exact before/after
bindings and full units, omitting membership for these operations. Copy those bindings
to the final receipt and provide nonempty frozen rationale `evidence`. If the actual
stable bindings differ, supply the complete prospective inventory/review/acceptance
`renewal` described above. Pure priority and next-action changes preserve existing phase
acceptance only when actual bindings are equal; omit `renewal` in that case. A client
flag cannot waive renewal. Effective scope changes and their acceptance publish together.

Each top-level `amendments` record retains the receipt, immutable field patches, reason,
director/time, before/after bindings, prior/effective priority maps, full retained ordering
and distinct `ready_before`/`ready_after` queue snapshots observed at the mutation.
`execution_priorities` is separate from task definitions. Strict validation checks patch
chains and effective metadata/priority against history. Runtime next-action updates may
advance independently. Packets expose current metadata, priority and relevant amendments;
snapshots expose the priority map/history, and status/next report null for unspecified
priority. Precommit drift and incomplete publication reject before the canonical write.
Postcommit render failure reports the committed revision; run `render`, and inspect the
recorded amendment instead of resubmitting its ID.

## Reviewed splitting and retirement

`task-split` and `task-retire` use the same current director, active objective,
revision, immutable transaction ID, reason/evidence and before/after binding envelope.
Both require prior phase adoption/current acceptance and complete prospective
inventory, independent candidate review and director acceptance renewal.

```sh
python tools/feature_program.py --program-dir .work/programs/example admission-binding --input evidence/_planning/attempt-1/split-proposal.json
python tools/feature_program.py --program-dir .work/programs/example task-split --input evidence/_planning/attempt-1/split-1.json
python tools/feature_program.py --program-dir .work/programs/example task-retire --input evidence/_planning/attempt-1/retire-1.json
```

Supply `predecessor` and `lineage` with that exact predecessor ID and an ordered
`successors` array. Split requires at least two genuinely new children, with the
existing admission `source`, `planning`, `intake_authors` and authored-category
placements. Source and plan must identify exactly those children in the same order.
Retirement requires empty successors and rejects source/planning/intake-author/category
fields. A single replacement is outside these commands.

The read-only binding response includes `predecessor_before`: the stable selected
`definition_digest`, its actual resolved frozen `source` descriptor and lossless
`section` (including original text, line and digest). Copy this object, both scope
bindings and the complete units into the reviewed final receipt/renewal. Execution
rejects a missing or stale predecessor binding. Runtime source revalidation for a
later child claim remains an independent requirement.

Declare every dependent rewrite explicitly through `dependency_changes`, including
an empty array when none are needed:

```json
[{"id": "D", "before": ["P"], "after": ["C1"], "reason": "D requires C1 output"}]
```

The predecessor and rewrite targets must be eligible planned, unowned current members.
Release owned work first; these commands cannot rewrite active/review/blocked/done or
excluded tasks. No edge is inferred from lineage. Rewrites apply before the exact one
membership delta: remove only the predecessor and add all split children, or remove
only the retired task. Unknown/duplicate IDs, missing replacements, executable edges
to disposed prerequisites, cycles, stale authority/revision and empty membership reject
without a canonical write. Completed historical references and retained disposed
records do not turn retirement into dependency completion.

The predecessor retains planned status, its source, category, attempts, evidence and
historical priority. Its separate `disposition` is `split` or `retired`; reciprocal
task `lineage` keeps earlier parent relationships when a child is subsequently split.
Children start planned/unowned with empty attempts and no inherited priority or passing
checks/review/delivery evidence. Disposed work is absent from ready candidates and
rejects claim/submission, planned edits, priority changes and membership restoration.

One amendment publishes actual split source intake, explicit dependency patches,
disposition/lineage, membership revision and renewed phases. Retirement creates no
intake. Immutable history records the predecessor binding, before/after disposition
and lineage, patches and rationale. Canonical JSON comparison preserves exact types
through PLAN/split replay and effective-state validation. All retained provenance units
remain reviewed, while aggregate completion uses only the current membership revision.
Existing atomic publication/precommit checks and committed-render recovery apply.

## Publication tracking for definition changes

Definition changes require completed relocation/succession publication at both endpoints,
even when ordinary successor progress is already enabled. The controlled repository store
`.work/programs/.publications` contains one atomic `ledger.json`, its immutable identity,
and immutable operation receipts. Every new succession and relocation registers both
endpoint paths and program identities under both endpoint locks before publishing any
endpoint marker. It durably enters `publishing` before the first marker. A publishing
succession guards its predecessor even if the first local marker write fails. Registered
and publishing operations block related definition changes; the successor's ordinary
progress still follows its existing local guards. Registration also reserves an absent
destination against initialization. Program endpoints cannot overlap the reserved store.

The guard reads the bounded ledger and only positively related operation receipts and
local/backlink/relocation-alias/explicit legacy inventory endpoints. It never traverses
the repository or opens unrelated operation payloads. Unreadable historical caches
therefore need no filesystem or ACL changes. Related unreadable, missing, linked,
conflicting or malformed material fails closed. The ledger has at most 4096 total active
and historical summaries; each ledger/document is limited to 8 MiB, and related reads
share a 64 MiB budget. Capacity errors occur before endpoint publication and require
inspection; there is no rollover or automatic deletion. A busy nonblocking ledger lock
requires retry; never steal it. Missing or changed adopted store identity requires
restoring the original storage, never silently creating another ledger.

### Fresh tracking and reviewed legacy adoption

Explicit `init` automatically tracks a genuinely fresh target. The permitted layout is
the supplied report and planning file plus root starter Markdown files: `README.md`,
`DIRECTOR.md`, `SUBDIRECTOR.md`, `WORKER.md`, `REVIEW.md`, `EVIDENCE.md`, `DECISIONS.md`,
`DESIGN.md`, `PRIORITY.md`, `ACTORS.md`, `RESPONSIBILITIES.md`, `MODEL-POLICY.md`,
`STAFFING.md`, `DELIVERY.md` and `QUESTIONS.md`. Their bytes are preserved and frozen in
`FRESH_INIT.json`; unrelated directories, runtime files, prior publication markers or
an existing backlog prevent fresh initialization. A custom README is preserved.
Tracking is written before the backlog. If interrupted, repeat the exact explicit init
only while no backlog exists and every report, planning, baseline, reference, program,
definition and starter-file binding is unchanged. The original receipt remains immutable;
the successful registry records its actual initialization observation time. Once the
backlog exists, inspect/check/render it rather than repeating init.

Existing untracked registries retain ordinary runtime behavior but need explicit reviewed
`publication-adopt` before admission or membership changes. The director supplies a new
receipt under `evidence/_publications/attempt-1/`:

```json
{
  "schema_version": 1,
  "program_id": "example",
  "actor_id": "director",
  "expected_revision": "CURRENT_REGISTRY_DIGEST",
  "ledger_revision": "CURRENT_LEDGER_DIGEST_OR_NULL_IF_ABSENT",
  "binding": {"program_id": "example", "objective_id": "goal", "objective_digest": "DIGEST", "task_definition_digest": "DIGEST", "planning_digest": "DIGEST", "task_ids": ["TASK-1"]},
  "reason": "Actual endpoint inspection and obsolete writer reconciliation",
  "inventory": {".work/programs/example": {}},
  "uninspected_artifacts": [{"path": ".omo/historical-cache", "reason": "Not inspected; unrelated historical artifact"}],
  "obsolete_writers_quiesced": true,
  "inventory_authors": ["director", "inventory-author"],
  "review": "evidence/_publications/attempt-1/inventory-review.json",
  "evidence": ["evidence/_publications/attempt-1/actual-inspection.md"]
}
```

`inventory` maps each finite inspected repository-relative endpoint to actual byte hashes
of existing `SUCCESSION_INCOMPLETE.json`, `RELOCATION_INCOMPLETE.json`, `SUCCEEDED.json`,
`PREDECESSOR.json` and `RELOCATED.json`. Empty objects mean those named files were absent
at that inspected endpoint. Include the current program, all recorded relocation aliases,
and transitive endpoints named by those markers. `inspect_publication_inventory` is the
read-only Python helper for that finite closure. It does not certify filesystem-wide
absence. Mandatory endpoints cannot appear among uninspected exclusions. The director
must actually reconcile obsolete concurrent writers; their old code cannot participate
in central registration.

The independent review contains exact `program_id`, `binding`, `inventory_digest`,
`reviewer`, `reviewer_role: "independent"`, `verdict: "accepted"` and empty
`unresolved_findings`. Its reviewer is neither the director nor any inventory author.
`inventory_digest` is the canonical digest of `{"inventory": ..., "uninspected_artifacts": ...}`.
An existing scope review beneath `evidence/_phases/attempt-1/` can be reused if it explicitly
covers that exact inventory; a separate review is not required merely for its filename.
Adoption requires already adopted/current accepted phases and real evidence. It freezes
the decision, inspected/excluded observations and references in `adopted.json`, then writes
`PUBLICATION_TRACKING.json`, without changing backlog bytes. Old pending markers become
`legacy-observed` records at the current observation time; their original bytes remain
unchanged. A failed final tracking write can resume only the exact frozen decision against
its recorded published ledger revision. Earlier failures require inspecting the retained
observations and refreshing stale explicit review/revision inputs.

```sh
python tools/feature_program.py --program-dir .work/programs/example publication-adopt --input evidence/_publications/attempt-1/adoption.json
```

### Publication recovery and failed-copy retry

Use `program-succeed-recover` at the predecessor or `relocate-recover` at the destination;
both accept `--publication-id ID` when the active relation is ambiguous. Recovery can
finalize central history after all local markers were cleaned up, without demanding the
successor's historical registry hash after permitted runtime progress. Matching canonical
completion receipts and absent incomplete guards must be proved before an active entry
becomes historical. Immutable observations precede the final ledger write, so interrupted
finalization can reuse that evidence. Unreferenced pre-registration receipts are retained;
they do not assert publication or completion.

An inspected copying-stage failure cannot be promoted by relocation recovery. Retry to a
distinct absent destination with `relocate --supersedes FAILED_OPERATION_ID`. The exact
old copying marker remains preserved and its destination remains guarded. A positively
verified replacement and old supersession become historical in one atomic ledger update.
Only that replacement/current alias lineage can proceed with definition changes; missing,
changed or unreadable old copying bytes invalidate the exemption.

`publication-reconcile --input RECEIPT` has only two reviewed actions: `cancel-registration`
for a durable registered operation with unchanged pre-publication marker inventory at both
endpoints, or `supersede-failed-copy` for an exact copying-stage marker and a distinct already
completed replacement. The receipt uses the adoption authority/binding/revision/reason,
authors/review/evidence fields plus `action`, `operation_id` and, for supersession,
`replacement_id`. Its review digest covers all receipt fields except `review` and `evidence`.
Cancellation observes current runtime registry progress honestly and never cancels a
publishing operation. No reconciliation deletes markers, overwrites destinations, silently
reopens a predecessor or discards an unverified active publication. Publication is reread
under the selected-program lock before a definition transaction commits.

## State and review

The canonical `backlog.json` stores complete imported task prose, hashes, planning metadata,
attempts and events. `BOARD.md`, `tasks/<ID>.md` and `CHECKPOINT.md` are generated views;
regenerate them with `render`. The director changes task and governance state through the CLI;
the web can only save bounded answers to issued questions. Workers edit claimed code and
their own evidence. Atomic file replacement and a local lock
prevent concurrent writes; the tool does not automatically steal an existing lock.

Inspect `depends_on` and advisory `related_to` links before coordinating with other owners.
Discuss bounded contracts through actual native-agent contacts, then record consequential
agreements and counterpart acknowledgement in task notes or linked evidence. A proposal
alone does not establish agreement or change ownership. Escalate scope and acceptance
conflicts through the [directing workflow](work-program-workflow.md#related-work-and-peer-communication).

Use actual `--help` and generated JSON templates to obtain the current schema:

```sh
python tools/feature_program.py --program-dir .work/programs/example template EXAMPLE-1 --kind revalidation --scope path/to/source.py
python tools/feature_program.py --program-dir .work/programs/example template EXAMPLE-1 --kind execution
python tools/feature_program.py --program-dir .work/programs/example template EXAMPLE-1 --kind review
python tools/feature_program.py --program-dir .work/programs/example template EXAMPLE-1 --kind delivery
```

Templates are incomplete inputs, not evidence of successful work. Revalidation must identify
the current checkout, exact scope and acceptance assessment. Claims reject unmet dependencies
and conflicting ownership. Scope arguments are repository-relative; evidence arguments are
program-relative. Use one `--scope` per explicit file or bounded directory. Avoid broad root
claims, sensitive files and evidence paths outside the program.

The normal flow is revalidate, `claim`, implement and test, `submit`, review and `complete`.
Submission enters review rather than completing the task. Required checks and acceptance
evidence must pass and remain bound to the submitted source. Rejected reviews use `rework`;
fix the findings, rerun affected checks and submit a fresh immutable round. Use `note` for
progress and evidence, `block` for an unresolved obstacle, and `release` before replacing an
unavailable worker. These commands preserve history rather than deleting earlier attempts.

Completion records what was verified at that time. It does not assert that future changes
cannot introduce regressions. Later authorized changes do not rewrite historical receipts.
Use a reasoned `reopen` for follow-up work after completion; a new claim needs revalidation.
An independent reviewer should challenge acceptance and execution evidence. If a director
performs self-review, identify that honestly and record the reason.

### Resolving a blocked attempt

Use `resume` after recording an actual blocker resolution. It keeps the current owner,
attempt, claim baseline and settings; it does not transfer ownership or record approval.
Inspect the bound template and write an immutable attempt-specific resolution receipt:

```sh
python tools/feature_program.py --program-dir .work/programs/example template TASK-1 --kind resume
python tools/feature_program.py --program-dir .work/programs/example resume TASK-1 --owner worker-example --receipt evidence/TASK-1/attempt-1/resolution-1.json
```

The template is read-only and its empty evidence is not a passing resolution. Keep its
`program_id`, `task_id`, saved `owner`, `attempt`, `expected_revision`, `definition_digest`,
`block`, `origin`, exact `scope`, current `head` and `source_digest`. Supply `schema_version: 1`,
`operation: "resume"`, a unique portable `id`, actual acting `actor_id`, nonblank resolution
`reason` and `next_action`, and nonempty attempt-relative `evidence`. `block` binds the latest
block through its zero-based `event_index` and canonical `event_digest`; a timestamp alone
does not identify it. Refresh the receipt after any registry, definition, HEAD or source change.

The tool derives the destination. An active-origin block resumes active even when implementation
bytes changed. A review-origin block restores review only for the same exact submitted scoped
bytes and valid frozen evidence. Changed submitted bytes return to active and clear the current
submission pointer; the old immutable round remains and fresh submission/review is required.
Resume grants neither approval nor completion. Repeated blocks retain their first episode origin.
For older data the tool proves origin from the exact current-attempt event chain and proves scope
from the original frozen claim/revalidation; ambiguous history requires release and a fresh claim.

Current accepted definitions and readiness control resume. Unmet dependencies, scope/conflict
collisions, disposition/exclusion, incomplete applicable succession, stale bindings, retired actors
or relevant pending transfers reject. Accepted metadata evolution alone is not rejection. Claimed
scope must remain unchanged; release and claim again for a different scope or owner. Fresh guards,
source, evidence, revision and authority are rechecked before atomic publication. Precommit failure
does not record a resolution. A rendering error after commit reports the committed revision: run
`render` and inspect the resolution instead of retrying its ID.

Owned notes, blocks, releases and resume permit the nonretired current owner or the currently
accepted director. Reopen requires that director. With no director appointment, the supported
legacy override is the literal `leader`; an invalid or retired appointment never falls back to it.
New `reviewer_role: "leader"` reviews also require this current director/legacy authority and a
reason. A self-review must use that honest role; the label alone grants no authority. Historical
review receipts and staffing snapshots are preserved without replaying today's authority over them.

## Verification requests and observations

Programs may record optional verification facts in the existing canonical registry without
adding task states or another status store. Use `verification-record --input` for a request,
started, blocked, finished, canceled, finding or resolution event. It freezes the input and
referenced artifacts, resolves current task/attempt/submission/source context, and publishes
through the normal authority, revision, evidence and location guards. The command does not
launch a check, acknowledge a peer, clear a task blocker or accept work.

```sh
python tools/feature_program.py --program-dir .work/programs/example verification-packet EXAMPLE-1
python tools/feature_program.py --program-dir .work/programs/example verification-record --input evidence/EXAMPLE-1/attempt-1/request-1.json
python tools/feature_program.py --program-dir .work/programs/example submission-inputs --input evidence/EXAMPLE-1/attempt-1/submit-request.json
```

The packet is a bounded JSON view of the current task definition, original acceptance,
owned paths, verification input identity, exact current submission when present, required
checks and recorded verification facts. It leaves a pre-submission context's submission
null; it never invents a future attempt/round or criterion extracted from ambiguous prose.
Existing Markdown `packet` and recovery `brief` remain available. Reads do not claim work,
execute checks, acquire mutation locks or publish results.

A record input uses these common fields:

| Field | Meaning |
| --- | --- |
| `schema_version`, `operation` | Version 1 and `verification-record` |
| `id`, `kind`, `task_id` | Unique portable event ID, one supported event kind and exact task |
| `actor_id`, `reason` | Actual acting participant and nonempty reason under current authority |
| `expected_revision` | Current registry revision from brief/snapshot; refresh stale inputs before mutation |

A request additionally names `lane`, `verifier_id`, `check_ids`, optional exact `criteria`,
`input_scope`, `input_complete`, `evidence_destination` and `next_trigger`. Optional fields
include environment identity, a resource name/boundary, a packet `context_path` and an explicit
`supersedes` request. Keep verification read inputs separate from writable ownership. Include
shared tests, configuration and runtime inputs that affect the check; an asserted complete
scope is not automatic proof that every ambient dependency was captured. Do not recopy
task/submission/context hashes or future submission numbers: the tool observes current
context and freezes references.

Subsequent events reference exact `request_id` and `request_version`, with the relevant actual
method, timestamps, result, artifact, blocker or finding facts. A finished event records
`method` with `execution_kind` and a command, structured argv/cwd or manual procedure;
`result`; task-attempt `artifacts`; and actual observed `input_before`/`input_after` digests.
Both input digests must match the compatible checked inputs to prepare a passing new check;
missing digests remain nonpassing. These are execution observations, not copied record
context: a fingerprint taken only when recording cannot certify what the check executed.
Nonzero `exit_code` cannot be passed. `not_applicable` needs explicit `policy_evidence`.
Timing uses actual `observed_at` with `clock_id`; missing endpoints remain unknown.
Record actual reporter attribution when a director records an observation. Requests and overrides require current
valid ownership/director authority; the named nonretired verifier records its own observations,
or the current director records an explicit reasoned observation/override. Recorded actor IDs
are not identity authentication or live contact endpoints. Retain original events and findings;
a recorded resolution does not replace the normal independent review decision.

Request/progress phase, observation outcome and current applicability are separate. A later
failed, canceled, stale or unavailable event never revives an earlier matching success.
Source, obligation, attempt or request changes retain history while invalidating applicable
reliance. Missing legacy metadata stays optional/unknown; explicitly malformed new data fails
rather than falling back to legacy. Snapshot/brief projection shares bounded request-local
history validation and indexes; shortened display does not omit integrity checks.

`submission-inputs` takes `task_id`, `owner`, selected finished `observation_ids`, `summary`,
`next_action`, actual ordinary `acceptance_evidence`, `acceptance_complete` and `limitations`.
It returns `status: not_assessed`, an ordinary execution `receipt`, `missing_fields` and
`verification_refs`. Current changed paths, check facts and frozen references are resolved
from matching observations instead of manually duplicating them. Missing or incompatible
required observations and acceptance remain nonpassing. Save the reviewed ordinary receipt
under the current task/attempt, supply actual criterion acceptance, and use the existing
preview/submit/review flow. Preparation neither executes recorded command text nor converts
process exit zero into behavioral acceptance.

Reuse assessment compares complete declared production/test/configuration inputs, method,
environment, check/criterion obligations and immutable result references. Equal compatible
facts permit an advisory reuse candidate with a reason; changed or unknown inputs require
recheck or remain not assessed. Fresh review for changed submission bytes, actual required
PR/beta CI and independent integrated review are still required. A partial or stale finding
bundle cannot supply missing acceptance. Waits use explicit compatible observed endpoints,
clock and as-of context; task state age or a publication timestamp is not a wait duration.

See the [parallel verification workflow](work-program-workflow.md#parallel-verification)
for lane ownership, resource reservations, finding bundles and the final director's decisions.

## Explicit criterion coverage and acceptance diagnostics

Coverage annotations are optional, append-only records outside objective and task definitions.
They map original objective criteria to explicitly authored task/path/evidence contributors.
Recording a link changes no criterion, membership, candidate approval, check result or review.
Legacy absence and omitted criteria remain unknown. Only a current explicit zero-contributor
declaration with a nonblank reason means uncovered; task completion and prose never imply coverage.

```sh
python tools/feature_program.py --program-dir .work/programs/example coverage-binding --input evidence/_coverage/attempt-1/draft.json
python tools/feature_program.py --program-dir .work/programs/example coverage-record --input evidence/_coverage/attempt-1/coverage-1.json
python tools/feature_program.py --program-dir .work/programs/example coverage
```

Author a draft with `schema_version: 1`, `operation: "coverage-record"`, a unique portable `id`,
current accepted director `actor_id`, actual `reason`, `links` and optional `uncovered` rows.
Each link has `criterion: {ordinal, text_sha256}`, `task_id`, `scope_kind`, nonempty unique `paths`
and nonempty `evidence: [{path, sha256}]`. Ordinals are the original zero-based objective array
indices; text hashes bind exact UTF-8 criterion bytes, with no normalization or new criterion IDs.
The current task must be an objective member. Multiple tasks may contribute to one criterion;
one task may contribute to several criteria. Duplicate contributor pairs and conflicting
contributor/uncovered declarations reject.

`scope_kind: "candidate"` refers to declared candidate scope, with no attempt or round assertion.
It is planning intent even if the task later becomes owned. `scope_kind: "attempt"` additionally
requires the exact current integer `attempt`; optional integer `round` must name the latest actual
submission round. Paths must be safely contained in the applicable candidate or claimed scope.
Attempt evidence must remain in that task/attempt directory. Candidate evidence must be inside
the selected program's evidence directory. No artifact is created by binding or projection reads.

Optional `records` on a link contain `{kind, receipt: {path, sha256}}`, where kind is execution,
review, finding or delivery. These name actual JSON records of the same task/applicable attempt.
Captured check/findings/verdict facts are displayed as recorded observations, not new execution
or criterion acceptance. Missing facts stay unknown; a linked failed check differs from an absent
check. Recorded task acceptance is verified through the existing terminal validator and explicitly
identified as historical. Recorded objective criterion results retain their immutable completion
origin; current source freshness and unassessed HEAD remain separate.

`coverage-binding` prints the actual `expected_revision`, `objective_binding`, bound `links` and
bound `uncovered` rows for the supplied draft. Copy these exact fields into a new immutable input
under `evidence/_coverage/attempt-1/`. Binding requires real evidence; it does not fill passing
placeholders. Objective binding preserves exact criteria and current membership revision (or
legacy member IDs). Links bind effective task definitions, frozen report/section provenance,
applicable scope bytes and attempt/round identity. Uncovered declarations also bind the current
definition/source inventory. The revision token protects recording; subsequent unrelated notes,
actor observations and runtime actions do not invalidate unchanged stable bindings.

Current source/definition/member/attempt/round/evidence drift remains explicit. Old annotations
are retained and cannot silently attach to another attempt or to an exclude/re-add membership
revision. Completed relocation keeps content identity only through exact archived source lineage;
current locators are still safely read and hashed. Missing, changed or ambiguous provenance is
stale/unavailable, never a basename or generic alias fallback. Current independent review and
director acceptance remain necessary for consequential planning changes; annotation progress
alone does not renew phase gates.

New execution, review, structured finding, objective-completion and integrated-review inputs
may declare reliance with the same optional envelope:

```json
{
  "coverage_refs": [
    {
      "receipt": {
        "path": "evidence/_coverage/attempt-1/coverage-1.json",
        "sha256": "EXACT_RECORDED_RECEIPT_HASH"
      },
      "criteria": [{"ordinal": 0, "text_sha256": "EXACT_ORIGINAL_CRITERION_TEXT_HASH"}]
    }
  ]
}
```

Each row needs nonempty known criteria, exact current annotation/hash and no duplicate/conflicting
references. Task inputs must link that applicable task. Absence or an empty array declares no
reliance and preserves legacy behavior; prose is never interpreted as a reference. The shared
authoritative validator rejects explicit invalid/stale reliance. An execution/review input freezes
its exact envelope; annotation artifacts stay globally frozen without weakening task/attempt
evidence-prefix rules. Previously recorded inputs and reviews remain unchanged. Newly declared
reliance is checked against the actual pre-state and rechecked after derived preparation, directly
before publication; a submission's new round must not retroactively change that pre-state.

```sh
python tools/feature_program.py --program-dir .work/programs/example coverage --operation submit --task TASK-1 --owner worker-example --input evidence/TASK-1/attempt-1/execution-1.json
python tools/feature_program.py --program-dir .work/programs/example coverage --operation complete --task TASK-1 --input evidence/TASK-1/attempt-1/review-1.json
python tools/feature_program.py --program-dir .work/programs/example coverage --operation objective-complete --input evidence/_coordination/attempt-1/completion-1.json
```

Diagnostics support submit, complete, rework and objective-complete. They reuse the real detached
semantic preparation, authoritative validation and publication preflight; they never call save,
lock, render, mkdir, ledger publication or provider operations. Explicit CLI operation diagnostics
may acquire actual read-only Git HEAD outside those pure validators. Missing/unavailable metadata
is `not_assessed`, with no baseline substitution. Structured results identify the actual first
failure reason/type, what was checked and limitations. Missing required next actions come from
the existing validator; intentionally unassigned planned tasks do not receive speculative owner
errors. Without an actual operation/input, execution is not_assessed and structural validation
is reported separately. CLI read success means an observation was printed, not that its supplied
operation passed or became authorized. `structural_validation` reports the original authoritative
validation result for supplied operations too. If that original state fails validation, CLI
coverage returns `projection.status="unavailable"` with the exact reason/type and diagnostics,
without synthetic criteria, task or history facts. A valid original state still receives normal
coverage output when transition preparation fails; healthy binding drift remains explicit.

Integrated acceptance still requires exact criterion evidence, current membership, terminal member
reviews, separate delivery/CI, no unresolved relevant questions/transfers, and an integrated reviewer
independent of the director and every member implementer. Coverage links supply none of those facts.
The shared snapshot adds detached `coverage` metadata with linkage, knowledge, freshness, captured
results, historical acceptance origins and explicit integrated-review prerequisites. Snapshot and
projection remain Git/lock/write-free; unavailable facts are not passing facts. Existing 8 MiB
registry/output and per-document limits, 2000 snapshot evidence references, 64 MiB artifact reads
and 128 MiB scoped-source reads remain enforced. Observation caches are request-local, never a
registry-revision-only freshness cache. Precommit failures publish no annotation; committed render
failures report recovery through render and inspection rather than reusing a recorded ID.

## People, categories and objectives

Governance is optional for an existing registry. Each command below reads one UTF-8 JSON
receipt from inside the program directory using `--input receipt.json`. Use new filenames
for new decisions; receipts frozen by a transfer or acceptance must not be overwritten.

| Command | Receipt content |
| --- | --- |
| `actor-record` | `id`, `role`, `parent_id`, `model_id`, `reasoning_effort`, `provenance`, `observed_status`, `observed_at`, `decision`, `reason` |
| `category-set` | `categories` containing `id`, `title`, `parent_id`, `task_ids`, plus a `reason` |
| `director-appoint` | `actor_id`, `acknowledged: true`, source binding, evidence and user-authorized `authority` |
| `objective-record` | `id`, `title`, nonempty `criteria` and `task_ids`, `supplied_by: "user"`, `human_authorization` |
| `transfer` | Role, predecessor/successor, owned task when applicable, source binding, changed paths, decisions, unfinished work, evidence and next action |
| `transfer-ack` | Transfer identity, successor acknowledgement and fresh validation of the recorded work |
| `transfer-accept` | Transfer identity and director acceptance after the successor acknowledgement |
| `objective-complete` | Objective identity, current source binding, exact task acceptance/delivery references and a fresh independent integrated review |

For example, this receipt records a worker whose runtime model has not been observed:

```json
{
  "id": "worker-example",
  "role": "worker",
  "parent_id": null,
  "model_id": null,
  "reasoning_effort": null,
  "provenance": "unknown",
  "observed_status": "unknown",
  "observed_at": null,
  "decision": "new",
  "reason": "Assigned to the agreed example task"
}
```

```sh
python tools/feature_program.py --program-dir .work/programs/example actor-record --input worker-example.json
python tools/feature_program.py --program-dir .work/programs/example snapshot
```

Use `verified_launch` only when the exact model and reasoning effort are confirmed. Inherited
or unknown settings remain null. Reuse, replacement and retirement append staffing history;
claim-time assignment metadata is preserved separately. A recorded observation needs its
timestamp and does not prove the agent is still running. The tool never launches an agent.

Workstreams remain category roots. Optional child categories must form an acyclic hierarchy;
each task has one leaf membership and rollups count each task once. One accepted final director
holds the recorded authority. A pending transfer does not change ownership or merge authority.
Worker succession still requires release and a freshly validated claim.

Source-bound receipts use `source_head`, `source_digest` and `scope`. Obtain real current
values with `fingerprint --scope path/to/source.py`; its `head` output becomes `source_head`
in the receipt. Paths in `evidence` are program-relative. These are factual records, not a
way to grant permissions that the user did not give.

Final objective acceptance requires all declared tasks, their current reviews and delivery
evidence, no relevant pending questions or handoffs, and an independent integrated review
of the current aggregate source. Acceptance sets `awaiting_next_goal` with the user owning
the next action. A different task set belongs in a new local program; preserve the prior one.

## Local web dashboard

The Python service serves the committed React bundle and the selected local program. It
requires no npm install, CDN, account or remote backend at runtime:

```sh
python tools/feature_program_web.py --program-dir .work/programs/example --port 8765
```

Open `http://127.0.0.1:8765/`. The service binds to loopback and exposes no arbitrary file,
agent, shell or Git controls. Task views are read only. Responses to existing director
questions are enabled by default; add `--read-only` to disable that one write surface.
Use another port and explicit program directory to inspect another program.

Overview shows the objective, progress, blockers and next action. Work provides category,
status and text filters; each task has a separate detail page with readable descriptions,
acceptance criteria, linked dependencies and advisory relations, their recorded owners, attempts,
reviews and delivery. Team shows role responsibilities, recorded model settings, observations
and handoffs. A reviewed task and a recorded PR/CI result are different measurements.

The dashboard polls every five seconds while visible; hidden polling is suppressed and returning
to the foreground triggers refresh. Initial loading or failure is distinct from an empty
program. In-app refresh retains the last-good snapshot through pending and failed reads; a
successful read updates changed facts without resetting the current view. Unchanged semantic
data retains the current model. Filters and selection remain subject to valid current data.
Answer drafts and their version/retry state survive in-app navigation, polls and outages within
the current document, keyed by program and question. Reloading or closing the document clears
unsaved drafts; answer text is not stored in URLs or browser local storage.
The dashboard identifies stale or unavailable snapshots rather than inventing progress. Snapshot
reads create no locks or program files and perform no Git or network operations. Bounds include
2,000 tasks, 8 MiB documents and 64 MiB of evidence per snapshot; an exceeded bound produces
an explicit error instead of a truncated successful view.

Korean and English interface text can be selected independently of authored task or question
content. The saved browser preference takes precedence, followed by a supported program
communication language, browser language and English. Interface switching does not translate
or rewrite source text, saved answers or task IDs. Dates and numbers follow the interface locale.

For dashboard changes, use the [web interface review procedure](web-ui-review.md) to choose
proportionate interaction and visual checks. The [annotated design resources](web-ui-resources.md)
explain the relevant principles, application checks and limits.

## Director questions and answers

Issue questions only from the accepted director, with a current source binding and an explicit
objective or affected task scope. The receipt for `question-issue` contains `id`, `version: 1`,
`actor_id`, `prompt`, `why`, `requested_decision`, optional `options`, `affected_task_ids`,
`objective_id`, `source_head`, `source_digest` and `scope`. Use the recorded communication
language for the prompt, options and explanation. Private question text may use that language;
public source and documentation retain the repository's authoring convention.

```sh
python tools/feature_program.py --program-dir .work/programs/example question-issue --input question.json
python tools/feature_program.py --program-dir .work/programs/example question-acknowledge --input question-acknowledgement.json
python tools/feature_program.py --program-dir .work/programs/example question-resolve --input question-decision.json
```

The browser saves one bounded answer against the question ID and version. Repeated delivery
of the same submission is idempotent. The acknowledgement and resolution receipts use the
actual `question_id`, `version`, saved `submission_id`, current director `actor_id`, source
binding and a meaningful `summary`. The lifecycle is:

1. `open`: a director-issued question needs an answer.
2. `answered`: the response is stored, awaiting director inspection.
3. `acknowledged`: the director explicitly recorded reading it.
4. `resolved`: the director recorded the resulting decision.

An abandoned question uses `question-withdraw` with a reason-bearing summary; it is preserved
as withdrawn without inventing a response. Changed wording needs a new question identity.
Unresolved relevant questions block objective acceptance. Historical questions bound to an
older explicit objective do not block a new goal merely because task IDs repeat.

Saving an answer does not wake a stopped agent session or apply a decision. The active
director checks responses at work checkpoints and on recovery, acknowledges them and records
the resulting action. Resume a stopped session to continue. Local responses are not proof of
an authenticated person's identity; receipt content cannot expand the user's authority.

## Git and CI delivery

Implementation review and GitHub delivery are separate. Follow the repository's existing
commit convention and use independently reviewable PRs. Keep closely coupled changes together
and stage only the intended work unit. Recheck the staged content for personal or local data.

After a push, inspect GitHub Actions for the latest PR head and, where applicable, its test
merge revision. Record actual run/job URLs and conclusions in local delivery evidence.
Fix relevant failures, repeat local review and verify the new revision. A push, old green
run, missing workflow or unexpectedly skipped required execution is not sufficient. Native
Editor/Player execution remains distinct from compilation and policy-gated skips.

The offline `delivery` command records supplied metadata; it cannot authenticate a GitHub
result. The director must independently inspect the remote state before recording it. Keep
PR creation, CI verification and merge status distinct, and follow the user's merge authority.
See GitHub's [required-check guidance](https://docs.github.com/en/pull-requests/how-tos/merge-and-close-pull-requests/troubleshooting-required-status-checks)
and [status-check behavior](https://docs.github.com/en/pull-requests/reference/status-checks).

## Recovery after interruption

Read the local entrypoint, decisions, latest checkpoint and affected task packets. Run
`check` and `status`, inspect the real Git checkout and verify any saved agent's liveness.
Consult a surviving sub-director for omissions when the program uses one, then compare that
report with saved facts. If unavailable, recreate the role from its local contract.

Record the language used to begin the human session as `communication_language`. New starter
material accepts `--language ko` (or another portable language tag). An existing program can
record a later explicit preference without rewriting its source or historical receipts:

```sh
python tools/feature_program.py --program-dir .work/programs/example language --value ko --owner director-example --reason "Recorded the user's session language"
```

Use the program's actual accepted director ID (or its leader before appointing a director).
Questions, choices and explanations use that language across recovery and succession. This
preference does not change the dashboard's separately saved interface locale. Missing legacy
language metadata remains unspecified until a director records the actual session context.

Do not duplicate an unfinished claim or discard another worker's changes. Preserve receipts
and record an explicit release/reassignment when replacing an agent. Save a new checkpoint
after a meaningful handoff or before anticipated context compaction:

```sh
python tools/feature_program.py --program-dir .work/programs/example checkpoint --note "Reviewed current work" --next-action "Inspect the saved active attempt"
```

Files preserve recorded facts; they do not preserve unsaved thoughts or guarantee running
processes survive. A fresh clone also needs an independently authorized copy of private
program state before it can resume that program.

## Read-only authoring

`authoring-support` prints the exact supported command names, CLI arguments, receipt
fields and assessment limits. `scaffold` prints editable JSON to stdout. It does not create
evidence files or passing receipts. Missing facts remain null, pending, not_run or false;
the output lists fields and command arguments that still need input. Actor observations
do not establish liveness, independent review or authorization.

```sh
python tools/feature_program.py --program-dir .work/programs/example authoring-support
python tools/feature_program.py --program-dir .work/programs/example scaffold --operation claim --task TASK-1 --scope tools/example.py
python tools/feature_program.py --program-dir .work/programs/example scaffold --operation question-issue --scope tools/example.py
python tools/feature_program.py --program-dir .work/programs/example scaffold --operation template --kind execution --task TASK-1
python tools/feature_program.py --program-dir .work/programs/example preview --request preview-request.json
python tools/feature_program.py --program-dir .work/programs/example recovery-readiness
```

The preview request is bounded program-relative JSON containing exactly `argv`, with the
same arguments used by the real command. Global repository/program options, recursive
preview commands and help requests are not transition inputs. For example:

```json
{"argv": ["submit", "TASK-1", "--owner", "worker", "--receipt", "evidence/TASK-1/attempt-1/execution.json"]}
```

The mutation families are explicit:

| Family | Commands | Input |
| --- | --- | --- |
| Execution | claim, submit, complete, rework, resume, delivery | Exact task/owner and revalidation, execution, review, resolution or delivery receipt as required by the real command |
| Progress | note, block, release, reopen, language, checkpoint | Existing CLI arguments; no invented receipt wrapper |
| Verification | verification-record | `--input` optional exact request/progress/observation/finding facts; no task lifecycle transition |
| Governance | category-set, actor-record, director-appoint, transfer, transfer-ack, transfer-accept, objective-record, objective-complete | `--input` governance receipt; applicable source, authority and independent review bindings |
| Questions | question-issue, question-acknowledge, question-resolve, question-withdraw | `--input` bounded question receipt; exact question/answer version and current source |
| Phases | phase-adopt, inventory-freeze, candidate-review, execution-accept | `--input` phase receipt with actual revision and planning binding |
| Planning | task-admit, objective-membership, task-plan, task-prioritize, task-split, task-retire | `--input` complete amendment, frozen evidence and any required prospective phase renewal |
| Coverage | coverage-record | `--input` exact criterion/task/source/evidence annotation |
| Publication | publication-adopt, publication-reconcile, program-succeed, program-succeed-recover, relocate, relocate-recover | Existing receipt or explicit endpoint/revision/owner arguments; assessment remains partial |

The five existing template kinds are revalidation, execution, review, delivery and resume.
`scaffold --operation template --kind ...` uses their shared preparation. The delivery
scaffold leaves the tested commit unknown; current HEAD is not proof of a provider run.
Question answers continue through the existing answer surface, not a new mutation command.

Ordinary previews validate the original state before projecting facts, run the same
detached transition preparation used by execution, and perform the real final save
preflight and read-only freshness guards. They include the observed registry revision,
predicted state, changed fields and the first canonical prerequisite failure. Planning
previews include the full staged amendment and renewal, not just a prospective binding.
Category previews retain full-replacement semantics and show parent/task placement,
moved tasks, workstream-root fallback, and direct/descendant counts.

All six publication previews return `status: not_assessed` and
`assessment: partial_publication` after successful read-only preparation. Their output
identifies inspected inputs and the remaining locked publication steps. Relocation does
not copy into a virtual destination or claim copied artifacts are verified. Adoption does
not bootstrap a ledger. Recovery checks named records without deleting or repairing them.
An inspected preflight is never successful publication.

Preview and scaffolding never acquire a program/store lock, create directories, freeze
receipts or publish a registry, history, ledger or view. Existing read-only Git metadata
may be observed for exact source binding; no Git, provider or agent mutation is performed.
Input/reference and registry changes detected during observation require refresh.
Predicted timestamps describe the observation and are not promised execution timestamps.
Both preparations use that same context-local observation clock, then compare the complete
canonical proposed state, including derived hashes. Changed source or semantic facts reject
the preview; normal mutation clocks remain unchanged. Checkpoint previews observe the same
NUL-separated Git status format as actual checkpoints.
Every result is advisory: actual execution rechecks under its normal locks.

`recovery-readiness` reports the first authoritative failure, stale review submissions, pending questions/transfers,
observed ownership/collisions, marker contents and saved lock PID/time. Malformed original
state is not used to invent derived facts. A PID, timestamp or contact cannot establish
that a worker is alive, a lock is abandoned, or takeover is authorized.

### Frozen provider delivery inputs

`delivery-inputs --input REQUEST.json` accepts an explicitly supplied local provider
observation reference (`provider: {path, sha256}`) and a nonempty `tasks` list. Each task
entry supplies `task_id`, `attempt`, `submission_sha256`, `reviewed_source_digest`,
`required_ci` and attempt-relative `evidence`. These bindings remain individual when
provider observations are shared by multiple tasks. The provider JSON supplies branch,
base, commits, PR URL, tested_commit, CI job records, merge_status and any merge_commit.
No current-HEAD substitution or authentication inference occurs.

The output contains separate proposed receipts. Each receipt references the exact frozen
provider file; actual delivery retains that reference in immutable evidence. Missing or
inconsistent task/source/check facts fail shared metadata validation. Recording failed or
pending CI can be a valid transition, while its CI status remains `ci_failed` or
`ci_pending`. The authoring result never claims remote verification or delivery acceptance.
It publishes no receipt; save the reviewed inputs explicitly before running delivery.

Optional `cleanup` observations contain `status` (`cleaned`, `deferred` or `unknown`),
reason, exact `merge_identity` matching the receipt's `merge_commit`, actor_id,
recorded_at and attempt-relative evidence. Legacy absence means unknown. Cleanup facts
do not affect CI status, confer cleanup eligibility or perform any cleanup operation.

### Recorded provider execution and advisory preparation

A frozen provider file may supply a versioned `execution` envelope alongside the legacy
provider fields. A receipt adopting that envelope also supplies exact `provider_context`;
explicit adoption requires both. Legacy absence remains unassessed for the new execution
facts and does not rewrite historical delivery. The external context states the repository,
PR head/base/tested revision, expected receiving beta revision when applicable, current
accepting director and each required stage/check. Captured requirements cannot be inferred
from missing jobs.

The envelope preserves run/attempt/job/selected-step identities, actual checkout and explicit
test-merge relationship, capture completeness, output presence, policy applicability, retries
and observed times. Required actual execution, authorized policy skips, explicit
nonapplicability and advisory results remain distinct. A native-required criterion cannot
use a policy skip. Later failed/canceled/pending/mismatched captures stay visible; old green
records do not certify current source. PR checks, root premerge decision, exact squash mapping,
beta execution and cleanup are separate stages. Task review still precedes canonical delivery.

These read-only inputs use a frozen `provider: {path, sha256}` reference and exact
`provider_context`:

```sh
python tools/feature_program.py --program-dir .work/programs/example premerge-inputs --input premerge-request.json
python tools/feature_program.py --program-dir .work/programs/example cleanup-inputs --input cleanup-request.json
```

Premerge assessment reports advisory ready/deferred/unknown using current recorded head/base,
review/check context, squash method, expected-head guard and remote-inspection observation.
Cleanup assessment additionally needs exact reviewed-head/squash/beta/content mapping, no
later undelivered branch work, protection/dependent-PR and actual usage facts, plus verified
preservation of required ignored artifacts/recovery. Unknown or in-use/dirty/protected facts
defer cleanup. Neither command contacts a provider, merges, deletes or archives anything;
`remote_verified_by_tool` remains false. The final director refreshes actual remote and usage
facts and performs only already-authorized supported actions.

One frozen capture can prepare individual task-bound delivery inputs without copying another
attempt's evidence path. Actual required PR and receiving-beta execution remain necessary;
parsing local JSON, a successful preflight or a recorded cleanup claim cannot replace them.
See [CI validation](ci-validation.md#assessing-recorded-ci-for-delivery) and the
[delivery workflow](work-program-workflow.md#pr-delivery-and-branch-cleanup).

## Tool verification

```sh
python -m pytest tools/tests/test_feature_program.py tools/tests/test_feature_program_coordination.py tools/tests/test_feature_program_questions.py tools/tests/test_feature_program_authoring.py tools/tests/test_feature_program_web.py -q
python -m pytest tools/tests/test_feature_program_verification.py tools/tests/test_feature_program_verification_integration.py tools/tests/test_feature_program_delivery.py -q
python tools/format_scripts.py --check
python tools/lint_python.py
npm run format:check --prefix tools/feature_program_web
npm run typecheck --prefix tools/feature_program_web
npm test --prefix tools/feature_program_web
npm run build --prefix tools/feature_program_web
```

The existing Python Tests workflow discovers the tool's tests through `tools/tests/` on
matching pushes and pull requests. Verify the actual workflow result; local success alone
does not establish CI success. The Work Program Web workflow checks frontend formatting,
types, behavior and regenerated assets. See [formatting setup](../FORMATTING.md) for the
supported Node environment and a clean `npm ci` install. Generated bundles are committed with
their authored source; CI rejects changed or newly untracked generated output after building.
## Bounded recovery briefs

Use `brief` for current program context or `brief --task TASK-ID` for one task. These
read commands run before the mutation lock and use the same captured observation as
`snapshot`. Existing `status` and `packet` output remains available in full. Briefs do
not claim work, acknowledge answers, render views, inspect Git or contact a provider.

The program brief contains the recorded objective and exact criterion identities,
director authority, communication language, phase and publication observations,
membership, integrity, attention, questions and typed succession records. A selected
task brief adds its current attempt, original expected result and source reference,
scope, required checks, observed submission freshness, current recorded findings,
coordination notes, acceptance and separate delivery status. Earlier attempts are
linked with totals. Checkpoints remain historical context.

`snapshot.brief` contains the program projection. Each original `snapshot.tasks[]`
record additionally contains a compact `readiness` projection; full task briefs are
not duplicated for every task. Readiness includes canonical candidate applicability,
unmet dependencies and collisions from the existing status/collision implementation.
Claim assessment remains `not_assessed`: recorded factors do not supply an acting
identity, fresh receipt, current Git metadata or execution permission. Excluded planned
tasks receive `not_applicable` revalidation rather than a proposed fresh-claim step.

Attention priority is integrity/recovery, current human/director decisions, actual
blockers/conflicts/failed acceptance, pending review/transfer/delivery, then dependency
and revalidation context. Ties use stable kind/entity identity. Current-goal question
attention reuses the canonical relevance filter; historical question records stay
accessible. Criterion impact comes only from explicit current coverage links. A
coverage link, accepted historical review or recorded CI result is not new execution
or current integrated acceptance. Failed/pending CI facts remain distinct from success;
local provider records never imply remote authentication.

Authored notes retain actor, reason, next action, timestamp and their own frozen task
evidence references. Words such as "acknowledged" or "accepted" remain authored text;
they are never parsed into a generic peer-approval status. `peer_agreement.status` is
`unknown`. Typed succession proposal, acknowledgement and acceptance are a separate
existing workflow. Shared contract references do not waive task/attempt evidence path
guards. An actor ID is neither a contact endpoint nor proof of liveness.

Bounds are 32,768 emitted bytes for a program brief, 16,384 for a task brief, 50
attention rows and 512 Unicode code points per displayed prose field. Combining marks
count separately. `truncated_fields` identifies shortened prose; original text and
identity remain in the linked full record. IDs, hashes and link targets are never
shortened. Collections expose `total`, `shown`, `omitted`, `items` and `reference`;
attention also has per-kind counts. Zero shown does not mean zero conditions. Required
integrity inspection happens before display reduction, and integrity totals and
unknown/not-assessed summaries survive omitted rows. If the essential envelope cannot
fit, the command fails explicitly.

The bound uses the actual canonical JSON bytes, including escaped Unicode, indentation
and the final LF. The brief CLI writes these bytes directly so Windows newline
translation cannot exceed the limit. Snapshot embedding retains its existing 8 MiB
output limit. Snapshot reads still enforce 2,000 tasks, 2,000 evidence files and the
64 MiB evidence budget. Source/planning and publication identity captures each have a
64 MiB aggregate budget. Submission and coverage source readers retain their existing
128 MiB budgets; one additional 128 MiB budget checks all observed scopes before return.
Complete source maps, including directory membership and missing paths, must remain
consistent across repeated observations and the final check. Every artifact retains its existing per-file bound. Presentation
savings do not remove these full integrity reads.

Original-record references use the selected program's `backlog.json`, a JSON pointer
and the exact registry revision, with a task/question identity when applicable.
Source references retain repository-relative path, original hash, reference base and
section location. Artifact references retain their program-relative path and hash.
Use the full snapshot, `packet TASK-ID`, or the original local record to follow them;
this does not introduce arbitrary file serving. A changed registry, captured source,
artifact, publication marker or observed scoped source during preparation requires refresh.
Structural and location-guard failures remain errors; ordinary source/evidence drift
remains visible as recorded context with warnings. Lock observations never authorize
takeover or repair. Recovery mutations remain explicit director work.

A blocked current attempt exposes its canonical `block_binding` and exact event
reference. When that origin is review, its retained submission receives the same
source observation as a current review. Changed source calls for resolving the block
through the guarded resume operation, rework and a new submission; the brief does not
execute those steps. Ambiguous legacy origins stay explicitly unassessed. Historical
completed, released or rejected submissions do not gain a new freshness prerequisite.

## Reviewed session-language presentation

The dashboard applies verified current presentation independently of the interface selector.
Goals, criteria, tasks, questions and recorded people use consistent displayed labels in lists,
details, search and accessible names; routes and filter values retain canonical IDs. Search
accepts original and displayed task labels, but never indexes answers, drafts or technical
evidence references. Dedicated original/source audit panels and redundant content-language
metadata are omitted from the dashboard. Exact originals, source locators and catalog coverage
remain available in local program records and through the CLI. Unavailable or non-current
presentation falls back to the original content.

For a translated question option, an explicit click copies the exact visible label into a new
editable freeform answer. The answer endpoint still receives only `question_id`, `version`,
`answer` and `submission_id`. Presentation refresh cannot rewrite an existing draft, saved answer
or frozen retry tuple. Drafts remain in the current document across in-app navigation and data
refresh; reloading or closing the document clears unsaved drafts.

Catalog coverage includes eligible historical sources that the snapshot may not expose as a
rendered record. The browser does not fetch arbitrary files or reconstruct missing event, phase
or publication narratives. Exact supplied origins determine repeated-copy resolution; matching
prose alone is insufficient. Raw source, JSON and technical evidence remain original-only.

Presentation text is optional, reviewed display data. It does not change source sections,
objectives, criteria, actors, issued questions, saved answers, task acceptance or delivery facts.
The optional top-level `presentation` ledger keeps immutable input and independent-review
references. Content uses the exact recorded `communication_language`; a dashboard UI-language
preference does not select a different content catalog. Missing language stays unknown.

Use `presentation-inventory` to enumerate all supported typed targets and their exact original
values, source bindings and locators. `--locale TAG` inspects another exact tag without changing
the program. Targets include goals and criteria, workstreams and categories, task titles and
human narrative, current and historical reasons, question wording and options, actor aliases,
and already referenced receipt summaries. The inventory identifies all 22 field families,
including static UI copy and protected original-only data. It does not read arbitrary target
paths or hydrate unrelated evidence files. Technical evidence, commands, IDs, paths, model
values and saved answers remain original. Unsegmentable mixed markup stays original-only.

Start with `scaffold --operation presentation-record`. Fill its input with a unique portable
`id`, current director `actor_id`, exact `program_id` and `locale`, nonempty `authors` and
`reason`, and between 1 and 256 operations. Copy each operation's `target` from the inventory;
do not invent JSON paths. Each operation uses `action: set`, a mode and expected `supersedes`:

- `translated` supplies reviewed `text` for a human-content target.
- `original` confirms the original already uses the recorded language and stores no text copy.
- `alias` supplies an actor display label while preserving the original actor ID and role.
- `action: withdraw` names the superseded entry and supplies neither mode nor replacement text.

`presentation-binding --input DRAFT` returns resolved `operations`, `expected_revision`,
`observed_registry_sha256` and `review_payload_sha256`. Copy only the first two into the input.
The observed hash identifies the exact read; it is not an input property. The review digest
binds all authored content and source fingerprints, excluding the review reference itself and
the transaction revision. A later unrelated revision requires rebinding; an unchanged payload
can retain its actual independent review. This command performs no review or publication.

The independent review JSON has exactly `schema_version: 1`,
`operation: presentation-review`, `program_id`, `locale`, `payload_sha256`, `reviewer_id`,
`reviewer_role: independent`, `verdict: accepted`, `unresolved_findings: []` and a nonempty
`summary`. The reviewer must differ from the director and all authors. These are recorded
review claims, not identity authentication or automated language-quality assessment. Never
write an accepted review for work that has not actually been reviewed.

Freeze the input and review under `evidence/_presentation/attempt-1/`. The input's `review`
is the exact `{path, sha256}` of that review. Run `preview --request REQUEST` with the argv
`["presentation-record", "--input", "INPUT"]` before recording when useful. Preview calls
the same detached preparation and final output preflight as the mutation, without locks,
Git, writes or external operations. Only the current acknowledged director may execute
`presentation-record --input INPUT`. Publication rechecks raw registry identity, current
authority and language, original bindings, supersession, and frozen input/review bytes.

`snapshot.presentation` supplies the selected locale, revision, coverage counts by kind and
current/historical period, and latest entries keyed by the canonical typed-target hash. An
entry is `current`, `stale`, `unavailable` or `withdrawn`; only a current translated/alias entry
contains usable text. Original mode points back to the original. Missing targets are counted
without adding a warning object for every row. Unavailable original receipts and orphaned
entries are explicit. A newer stale or withdrawn entry never revives an older translation.
The full inventory provides missing-target detail without a sampling cap. Partial coverage
is not complete translation, and successful recording is not UI or human-language acceptance.
Presentation transaction inputs, reviews and their own publication audit events are technical
original-only records, counted separately by `technical_records`. Recording a catalog does not
create a fresh human-content target for its own audit reason. Other authored history remains
eligible, so a fully reviewed catalog can reach complete coverage without recursive translation.

Each input and review is limited to 1 MiB of encoded JSON. Limits are 65,536 Unicode code
points for narrative, 512 for short titles, 500 for options and 128 for actor aliases. Code
spans, fenced code and Markdown link destinations inside eligible prose must remain exact.
Existing 8 MiB registry/output and aggregate snapshot budgets are unchanged; essential
overflow fails explicitly. Large catalogs can use separately reviewed batches. Reads retain
the ordinary source/evidence/registry consistency guards and never acquire a program lock.
