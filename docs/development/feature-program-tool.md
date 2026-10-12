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
status and text filters; each task has a separate detail page with source context, linked
dependencies and advisory relations, their recorded owners, attempts,
reviews and delivery. Team shows role responsibilities, recorded model settings, observations
and handoffs. A reviewed task and a recorded PR/CI result are different measurements.

The dashboard polls every five seconds and preserves filters, selection and answer drafts.
It identifies stale or unavailable snapshots rather than inventing progress. Snapshot reads
create no locks or program files and perform no Git or network operations. Bounds include
2,000 tasks, 8 MiB documents and 64 MiB of evidence per snapshot; an exceeded bound produces
an explicit error instead of a truncated successful view.

Korean and English interface text can be selected independently of authored task or question
content. The saved browser preference takes precedence, followed by a supported program
communication language, browser language and English. Interface switching does not translate
or rewrite source text, saved answers or task IDs. Dates and numbers follow the interface locale.

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

## Tool verification

```sh
python -m pytest tools/tests/test_feature_program.py tools/tests/test_feature_program_coordination.py tools/tests/test_feature_program_questions.py tools/tests/test_feature_program_web.py -q
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
