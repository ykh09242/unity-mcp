# Directing a work program

A work program turns a human objective into scoped tasks, evidence and reviewable PRs.
The final director is accountable for the result. A local registry preserves decisions
between sessions; it does not grant permissions, authenticate people or run agents by itself.
Use the [tool guide](feature-program-tool.md) for commands and the local dashboard.

## Objective and authority

Record the human's objective, success criteria, constraints and permitted delivery actions
before decomposing the work. The director determines task purpose, implementation approach,
priorities, dependencies and staffing within that boundary. Each task needs a clear expected
result, owned paths, acceptance criteria, required checks and a next action.

Record the session-start language for human communication. Ask questions, offer choices and
explain decisions in that language; carry it through recovery and personnel changes. A later
explicit user preference may change it without rewriting historical questions or answers.
The dashboard's selectable interface language is independent. Author newly supported human JSON
in the recorded session language from the first operation, including Korean summaries, reasons,
questions and decisions when Korean is recorded. Keep Markdown documentation in English under
the repository authoring rule, and leave technical IDs, models and paths unchanged.

For existing human content in another language, use the reviewed, source-bound presentation
catalog rather than changing canonical source or past answers. The dashboard uses only current
entries for the recorded session language. Preserve exact originals and evidence in local
program records; show task content and actionable results in the dashboard without original/source
audit disclosures or duplicate content-language metadata. Inspect both current and historical
coverage through the program tools; a working fallback or a passing
schema check does not establish complete translation or readable rendered output. Keep the
program-specific catalog and language-review evidence under the ignored program directory.
Verify original/displayed search, canonical identity, long prose, keyboard navigation and
draft/caret/retry preservation with both interface languages before acceptance.

Keep the authority record explicit: who directs the program, which branch receives PRs,
whether the user authorized merges, and which actions remain outside scope. A portable
template is not authorization. A new session inherits recorded user decisions after checking
them against newer instructions; it must not request the same permission repeatedly.

Keep one actual canonical program in its designated checkout. Separate implementation/QA/PR
checkouts supply frozen source and original proof without copying the live registry or inventing
production submit/review events. Integrate the coherent changes, obtain actual receiving CI, then
freeze the final scoped bytes and full relevant input equivalence once before preparing canonical
submissions. Earlier code review, native checks and CI remain supporting proof; obtain one actual
independent acceptance review of each newly submitted packet. A commit-label change alone does
not require another task review. A relevant source/input or obligation change requires the affected
comparison, check or review; retain unchanged originals and qualify the actual delta. Ordinary
relocation stays inside the selected repository root; it does not transfer a canonical program
between linked worktree roots.

## Read performance and storage handoff

For a program using prepared dashboard reads, record the canonical program location and
actual producer checkout in the private recovery note. After changing producer code, use the
explicit `storage-enable` rebuild described in the tool guide before measuring the delivered
screen. Preserve the original registry backup and logical history. Relocation requires a new
model for the destination; a copied store does not establish currentness.

Keep write preparation, cold first response, completed refresh and normal new-revision
response timings separate. Include the complete freshness checks in measured reads, retain
slow fallback results, and test representative accumulated history. A fast cached response
alone does not establish correct refresh behavior. During browser verification, preserve the
selected route, filters, scroll, focus and unsaved answers across the actual completed refresh.
Use targeted regression checks for changed dependencies and one frozen integration result;
repeat a check only after a relevant change, failure or unresolved finding.

## Roles

| Role | Responsibility | Limit |
| --- | --- | --- |
| Final director | Objective interpretation, decomposition, priorities, assignments, conflict resolution, acceptance, integration and authorized merge | One current final director; material uncertainty returns to the user |
| Sub-director | A bounded group's dependencies, direction, coverage, reviews and recovery records | May recommend corrections; does not automatically inherit final merge authority |
| Worker | Implement the assigned scope and provide reproducible evidence | Preserve other work; report changed assumptions and unowned dependencies |
| Reviewer | Independently compare changes and evidence with acceptance criteria | Report findings and remaining uncertainty; a status assertion is not proof |
| User | Set the major objective and resolve consequential ambiguity | Receives the next-goal-setting action after completion |

Use as many workers and sub-directors as the work warrants and the runtime can support.
Prefer reuse for related scope and useful context. Create another worker for an independent
slice, a fresh review or a documented context reset. Add sub-directors when dependency,
review or context load becomes difficult to supervise. Record the reason, reporting parent,
scope, acceptance and model settings for each staffing decision. A quiet worker is not
automatically unavailable, and a saved assignment is not proof of a running process.

Record exact model IDs and reasoning settings from actual launch information when available.
When the user delegates selection, the director chooses a currently supported combination
based on complexity, consequence, independence, speed and cost and records its rationale.
Announce actual settings before launch. A running agent is not reconfigured merely because
its saved profile changes; use a recorded handoff if another model requires a new agent.
Mark inherited or unknown settings honestly. Keep staffing status separate from timestamped
runtime observations. The director manages real agents through the available native tools;
the registry and read-only web view only display recorded facts.

## Allocating ready work

Inspect the complete remaining task set against the objective, validated outputs, current
ownership and actual runtime capacity. Numeric phases and display order do not establish
implementation readiness. Classify each reason for waiting before allocating the ready set:

| Constraint | Allocation decision |
| --- | --- |
| Hard prerequisite | Name the required validated output that is unavailable. Hold implementation that needs it; assign useful preparation separately with its own result and evidence. |
| Exact write conflict | Identify overlapping source, tests, contracts or generated paths. Give each shared path one writer and an explicit handoff boundary. |
| Integration-only condition | Independent implementations may proceed under an acknowledged interface; require their combined actual behavior to pass before acceptance. |

A task is ready only when its recorded prerequisites and claim checks are satisfied and its
owned scope is available. Correct an overconservative dependency through the supported
[reviewed planning amendment](feature-program-tool.md#planned-metadata-and-execution-priority)
before claiming it; retain the required integration and review gates. Record the specific
blocker and next start trigger for work that cannot proceed.

Allocate all useful independent slices using the [roles](#roles) and staffing rules above.
Justify reuse, new workers, sub-directors and model/effort choices against current capacity,
context and supervision load, without a fixed worker count. Verify native contacts and
runtime observations; retain inherited or unknown settings honestly. Distinguish actual
implementation from preparation, research and review in progress reports.

Use one review-obligation queue for recorded verification lanes, submitted task reviews and
required-check gaps. Count and order the complete assessed facts before reducing the display;
a short visible lane list cannot establish that no obligation remains. Distinguish the next
verifier, implementation owner and blocker resolver. Explicitly choose the verifier when
preparing an assignment; a saved actor status is not current availability or capacity.
A superseded request recorded as running remains resource-use uncertainty until an actual
terminal observation. Count every occupied or ambiguous dispatch before bounding visible rows;
a terminal report for another dispatch cannot free that work. A review WIP limit is allocation
policy, and its headroom is not observed runtime capacity. Without a complete compatible native
runtime observation, available slots remain unknown. Use `review-queue` and `review-assignment`
for this read/preparation step.
A queue row neither starts a worker nor adds a mandatory lane to a
legacy task. Resolve routine contracts directly with the responsible peer and escalate only
missing authority, ownership overlap or consequential unresolved decisions.

Use a compact allocation record for each assignment in task notes or linked private evidence:

| Field | Required record |
| --- | --- |
| Result and purpose | Expected output and the objective criterion it serves |
| Ownership | Responsible director, worker, exact writable paths, checkout and branch |
| Contacts and capacity | Verified native contacts, current availability, model/effort provenance and staffing rationale |
| Interfaces and gates | Prerequisites, peer contract/acknowledgement, shared writer and integration obligations |
| Acceptance and evidence | Acceptance criteria, required checks, review and reproducible evidence locations |
| Verification responsibility | Named verifier or resolver for each required lane, input scope, resource boundary and evidence destination |
| Next trigger | Action possible now, awaited condition and its resolver, next start trigger, and handoff recipient/boundary |

Isolate checkouts when concurrent source snapshots, build outputs or branches interfere.
Isolation does not grant duplicate ownership: shared contracts and generated paths still
have one writer. Follow [peer communication](#related-work-and-peer-communication) before
code depends on a consequential interface agreement. Obtain counterpart acknowledgement
before that dependent code starts, and record combined actual verification before accepting
the affected work. If a peer is unreachable, use the responsible director's relay and hold
only the work needing the pending agreement; silence is not acknowledgement.

At assignment, interface change, blocking, freeze, review, handoff and recovery, audit the
complete ready set for ready-but-idle work, needless waits, overloaded owners and objective
drift. Record the correction, responsible actor and next trigger using the existing
[direction and review procedure](#direction-and-repeated-review). Material goal uncertainty
returns to the human through the final director; independent authorized work continues.

Reallocate through supported `release`, fresh `claim` and an acknowledged
[handoff](#handoffs-and-recovery), preserving attempts, failures and reviews. A proposed
replacement does not own the predecessor's paths. Carry allocation decisions, accepted
interfaces, unresolved proposals and next triggers through checkpoints and compaction;
on recovery reconcile them with actual source, ownership and runtime state before resuming.
The director starts and coordinates real agents through native tools; registry records do
not launch or schedule them.

## Related work and peer communication

Before implementation and when an assumption changes, inspect task dependencies, advisory
relations and current owners. Use the actual native-agent address book to contact related
workers directly. A recorded actor ID is not automatically a valid runtime contact. If the
runtime cannot address a peer, ask the responsible director to relay the message and preserve
that limitation; do not claim delivery or acknowledgement without evidence.

Workers may agree on interfaces, data formats, dependency readiness and compatible sequencing
within their assigned scope. Keep ownership boundaries intact: agreement does not grant
permission to edit another worker's files or expand the objective. Escalate conflicting
requirements, ownership overlap, changed acceptance criteria and unresolved cross-area
decisions to the responsible sub-director or final director. The final director returns
material goal ambiguity to the user.

For a consequential agreement, record the related task IDs, participants, proposed contract,
affected files, counterpart acknowledgement, required verification and next action in task
notes or linked evidence. A sent proposal remains pending until the counterpart confirms it.
Link the same agreement from affected tasks instead of maintaining conflicting copies.
Notify the supervising director when the agreement changes sequencing, risk or a blocker.
Include unresolved proposals and accepted contracts in handoffs and recovery checks.

The dashboard links related work and its recorded owners; it is not a chat transport and
does not launch or wake agents. Use native messaging for discussion and the durable registry
for decisions that another worker or future session must rely on.

## Parallel verification

Assign an owner and criteria, implement the bounded unit, run independent code review, QA
and PR CI in parallel, fix findings and recheck affected inputs, then let the final director
accept the work, perform the authorized merge and beta validation, and clean up unused
branches/checkouts safely.

Name verification responsibilities before implementation freezes. Keep code ownership,
verification responsibility and final acceptance distinct. Use the tool's optional
[verification records](feature-program-tool.md#verification-requests-and-observations)
for exact requests, progress, findings and actual observations; the existing task lifecycle
remains authoritative. A request names the lane, verifier, check or criterion, verification
input scope, evidence destination, resource boundary and next trigger. Input read scope may
include shared tests or configuration beyond the worker's writable paths. Missing assignment
or observation remains unrecorded, not a successful check or proof of an idle worker.

Freeze one source-bound candidate and dispatch independent code review, QA and applicable PR
CI as soon as each lane's own inputs are available. Pre-submission review records the actual
candidate with no invented submission round. Native or browser QA also binds the built and
served assets, fixture and environment. PR checks bind the reviewed head, current base and
actual tested checkout or test-merge revision. A stable local digest alone does not establish
provider execution. Collecting a lane early does not complete the task or permit merge;
required current observations and the normal review, delivery and integrated-acceptance
gates still apply.

Reserve only resources that actually interfere: a shared browser session, generated build
outputs, server port, checkout or timing environment. Record the owner, affected operations,
reason and release trigger. An exclusive measurement interval may prevent competing tests
or polling while unrelated review, preparation and continuity work continues. Isolation does
not duplicate source ownership, authorize a new checkout or prove an unused resource is safe
to remove. Recheck actual use before releasing or cleaning it up.

Capture evidence while checks run: exact inputs, command and working directory or manual
procedure, environment, output, actual outcome and observed times when available. Preserve
failed, interrupted and incomplete observations. The tool resolves current context and
references; record/input preparation does not execute a command or authenticate a claimed
result. The explicit `verification-run` invocation executes live argv and captures its
actual result; it does not itself publish a finished record or accept the task.
Use observation-driven submission preparation to avoid recopying known hashes and check
facts, then provide actual criterion acceptance evidence through the ordinary submit flow.
Recover transcript-only evidence honestly when needed; do not repeat an unchanged successful
check solely to obtain a differently formatted receipt.

### Lean execution and record-only recovery

Keep these identities separate: writable ownership, a check's executed inputs, manual
criterion acceptance inputs, the actual provider-tested tree and aggregate/browser inputs.
Declare the source, tests, configuration, immutable fixture/build assets and environment
that affect each check. Include imported test helpers and command-loaded plugins; do not
use a whole-program union as every check's profile. A broad suite still needs its actual
full input closure. Missing or unknown dependencies prevent a passing reuse assessment.
Keep directory membership, missing paths and raw bytes exact. A lock file does not certify
the installed environment, and a matching owned scope does not prove the whole checked input.

Use the explicit `verification-run` invocation for one actual command, then prepare records
from its immutable result. Supply live argv, working directory, meaningful runtime/environment and
limits. Stored method, plan and canonical command text remain inert. Capture before/after
inputs, both streams, selected artifacts, actual exit, interruption and source drift.
Timeout, unsupported supervision, unresolved descendant cleanup, stream overflow, missing
artifacts and incomplete finalization remain visible; exit zero alone cannot establish a
passing behavioral check. Do not hold a canonical mutation lock while the child executes.
Manual acceptance and actual native/provider observations remain distinct evidence.
To inspect captured execution before publication, explicitly select its sealed capture JSON
with snapshot or server `--execution-capture`. This read changes no canonical phase/result;
record actual evidence separately through the ordinary guarded flow.

Use [original-proof preparation](feature-program-tool.md#original-proof-receipt-preparation)
to retain exact original references, task-local attachments and their provenance. Supply actual
criterion decisions, responsible actors and independent verdicts explicitly. Preparation can
fill mechanical current bindings; it cannot infer a PASS from log text or turn one executor into
another verifier. Ordinary JSON attachments remain opaque bytes; only actual typed control
receipts are parsed as control documents. Preserve the original run's time and outcome when
packaging a later submission or review.

For explicitly compatible targets, attach the same original capture byte-for-byte inside
each current task/attempt evidence directory. Retain its original executor, invocation,
times and origin references. Assess each target's authority, obligations, attempt/submission,
method/environment, input completeness and latest adverse facts separately. Attachment
neither runs the command again nor creates passing results for another task by itself.
A different task's evidence prefix or review cannot be borrowed as acceptance.

Preflight an intended record sequence against each projected predecessor, including later
artifact paths, schemas, authority, source and acceptance prerequisites, before publishing
its first stage. Closure preparation may materialize inert task-local evidence/documents;
those files are not canonical committed stages. Every real stage still rechecks current
guards. After interruption, inspect
the exact saved canonical record and immutable receipt before deciding what remains. Retain
already committed failed/nonpassing stages; publish only uncommitted stages. Recover a
postcommit view failure with the normal render recovery rather than replaying its ID.
Historical backlog hashes describe historical observations; they are not the next mutation's
revision. Record-only retry must not rerun an unchanged successful check or perform remote
merge/cleanup. Keep human criterion decisions and independent verdicts explicitly supplied.

When all required facts are already observed, provide them once to the finite
[record preparation and batch flow](feature-program-tool.md#one-guarded-record-batch), then use
its ordinary plan for one local canonical publication. Keep original proof and explicit human
actor/result/timing facts intact; prepare the normal controls against each projected predecessor.
An owner decides the exact submission after its verification is adopted; the independent reviewer
decides after the actual submitted packet exists. Those decision boundaries may require separate
publications and must not be filled by generated future acceptance. Each ordered operation still
passes its ordinary projected-predecessor and fresh publication guards. A late invalid operation publishes none of the batch. Do not place native
messages, tests, provider queries, merge or cleanup inside it. Inspect the exact batch result after
interruption: an already committed batch needs no repeat publication; committed view failure needs
`render`. Refresh an uncommitted stale plan without rerunning its unchanged original check. The
existing sequential closure flow remains available for operations outside the batch allowlist.
For ordinary mutations, retain the supported
[actual publication outcome](feature-program-tool.md#observe-an-ordinary-publication-outcome)
when needed. Inspect its exact publication identity after interruption or wrapper/retention failure;
a later error cannot turn a known durable commit into an uncommitted command. Recover views or
publish only the actually missing records, keeping acceptance and external execution separate.

A reviewer returns all actionable findings discovered in the assigned bounded pass, with
source identity, criterion or check, evidence, correction owner and affected verification.
Separate required corrections from optional suggestions. Notify an urgent blocker promptly;
completion of the bundle is not a reason to delay it. Uncovered criteria remain incomplete,
and a later new finding is retained rather than suppressed by a fixed review-round limit.
Recorded resolution does not clear a canonical rejection or accept a task by itself.

For a correction, compare the prior and current source, tests, configuration, environment,
method and obligation inputs. Rerun affected checks and obtain fresh review for changed
submitted bytes. Reuse unchanged observations only with complete compatible input bindings,
immutable evidence and an explicit applicability reason. Unknown or ambient input freshness
requires verification. Equal owned paths or an unchanged registry revision is insufficient;
unrelated notes alone need not invalidate equal stable inputs. Actual current PR and beta
checks and fresh independent aggregate review remain separate required obligations.

Track submission-to-start wait and start-to-terminal review spans from exact attributable
observations with compatible declared clocks. Keep intent, send, acknowledgement, start and
terminal boundaries separate. A post-save observation is the observed publication boundary,
not an invented atomic-write time. Name each wait cause, resolver and next trigger. Missing,
mixed-clock, reversed or otherwise unavailable endpoints mean unknown, including when canonical
review has completed without an observed terminal endpoint. Gross review spans include blocked
time and do not measure hands-on effort. Retain repeated waits, retries and historical rounds.

Read [observed timing](feature-program-tool.md#observed-timing) with its cohort and valid/eligible
coverage. A completed-sample median excludes unknown durations without hiding their denominator.
No completed samples means unavailable, not zero. Open age is null unless a retained compatible
as-of observation supports a fixed last-observed age. Refresh time, browser clocks, saved actor
status and canonical event preparation time do not measure worker activity. Do not sum overlapping
task or provider spans into lead time, critical path, utilization, ETA or time saved.
Measure ordinary write latency through the completed user response, including preparation,
validation, final source/evidence checks, durable publication and derived views. Retain the same
representative input and environment; report sample count, median and maximum for small samples.
Fixture setup and standalone model rebuild timings and read timing are separate observations, not ordinary write savings.

Monitor outstanding lanes with a bounded current snapshot and the native tools' supported
batch waits or provider reads. Wake for a completion, failure, actionable finding or required
decision, and back off unchanged observations. At the existing allocation and review
boundaries, a sub-director reports a concrete unstaffed lane, stale contract, source drift,
needless wait or recovery omission with evidence, proposed correction, decision owner and
next trigger. It does not take over authority after silence or add recurring status ceremony.

## Direction and repeated review

For programs that explicitly adopt planning gates, freeze the complete candidate inventory
and exact declared task/path contract, obtain an independent candidate review, then record
the current final director's execution acceptance before claims or submissions. See the
[gate receipt contract](feature-program-tool.md#optional-inventory-and-execution-gates).
Keep the planning approval bound to candidate/task-definition/planning digests and the
current objective; use the registry revision token only to protect each mutation transaction.
Implementation progress, staffing, checkpoints and evidence records do not invalidate that
approval. Consequential scope or definition changes require renewed applicable independent
review. Current implementation source checks remain a separate obligation.

Legacy programs keep their behavior until explicit adoption. Record real existing review
evidence honestly at adoption time without backdating enforcement. A synthetic starter is
uninitialized material; inspect the actual registry and location guards after initialization.
Keep authored recovery instructions intact. When a discovery plan is superseded by a distinct
initialized execution program, use the supported
[succession operation](feature-program-tool.md#discovery-to-execution-succession) once discovery
has no current claims. Preserve historical bytes and exact identities; a guarded incomplete
publication must be recovered explicitly and does not mean discovery completed.

After adopting and accepting the phase contract, use
[reviewed admission or membership amendment](feature-program-tool.md#reviewed-task-admission-and-active-membership)
for authorized new work. Freeze an additive report/plan with exact disjoint IDs and
complete owned paths, results and checks. Request the read-only prospective binding,
independently review its complete future inventory, and accept that exact scope before
the one locked transaction. Do not add unreviewed tasks to make future review possible,
silently adopt a legacy program, or rewrite the original source and past attempts.

Keep active membership revisions separate from past major objectives. Excluding unowned
eligible work retains its source and lineage while preventing new execution for that
amended goal; it does not mark the work done or retired. Preserve human criteria and
record exact added/removed IDs and rationale. Required unfinished prerequisites cannot
be excluded from retained work. Aggregate completion needs exact current membership and
a fresh integrated review. After amendment, both artifacts must identify the current
membership revision; matching older member IDs does not restore an old review. Unknown
criterion impact remains unknown.

Before definition changes, finish any related relocation or succession publication at
both endpoints. An enabled successor may still have predecessor cleanup pending; ordinary
progress does not authorize changing its frozen recovery contract. Admission reports
related operations through the bounded central publication ledger rather than scanning
historical caches. Fresh explicit initialization automatically records verified input
tracking. Existing untracked programs need a current director's finite endpoint inventory,
honest uninspected exclusions, obsolete-writer reconciliation and independent review through
`publication-adopt` before definition amendments; ordinary legacy runtime is preserved.
An interrupted central operation remains active until explicit verified recovery or the
two closed reviewed reconciliation actions. A copying-stage failure may be retried to a
distinct absent destination with its explicit superseded operation ID; the old marker stays
preserved and guarded, and only proven replacement lineage can proceed.

Admission reports precommit failures without changing the registry. If its canonical transaction committed
but view rendering failed, recover views with `render` and inspect the recorded transaction
instead of repeating admission. Supplemental provenance follows tasks through packets,
snapshots and relocation without changing immutable source or review evidence.

At task admission, meaningful implementation milestones, assumption or scope changes,
recovery, review and merge, compare the actual source and evidence with the objective.
Check that the task still serves a success criterion, stays within ownership and has not
silently changed its required verification. Sub-directors review their areas; the final
director resolves conflicts across them.

When direction drifts, record the finding, supporting evidence, correction, responsible
actor and next validation. Use task notes, blocks, rework and reassignment without erasing
previous attempts. Correct ordinary implementation choices within the authorized goal.
If the objective, acceptance boundary or a consequential scope tradeoff is unclear, ask
the user a focused question with concrete options and implications. Hold only dependent
work, continue independent authorized work, and record the answer before resuming. Elapsed
time is never approval.

Director-issued questions can be answered through the local dashboard. Track answer saved,
director acknowledged and decision recorded as separate states. The director checks pending
answers during active work and before resuming after interruption. Reading an answer does
not acknowledge it, and submitting one does not itself change task or merge authority.
A stopped agent session is not awakened by the local response store; resume the session to
continue its work. Resolve relevant unanswered or unapplied decisions before final acceptance.

Workers submit evidence bound to the source revision and scope. An independent reviewer
checks behavior, requirements and regressions. Fix findings, rerun affected checks and
obtain a fresh review when the reviewed bytes change. Keep unsuccessful rounds in history.

Resolve a task blocker with the guarded CLI `resume` when the current owner and claimed
scope remain valid. Record the actual resolution, current accepted task definition/revision,
fresh source and evidence, and exact block identity. The tool rechecks current prerequisites,
collisions, phase/location and authority before restoring the same attempt. Accepted metadata
evolution is assessed against current readiness rather than invented legacy definitions.

Preserve whether blocking began during implementation or submitted review. Unchanged submitted
bytes may return to review; changed bytes return to implementation with the old round retained
and fresh submission/review required. Ambiguous legacy origin, changed scope or personnel transfer
requires the supported release/fresh-claim path. A saved owner is not native-agent liveness proof.
Owner actions and director overrides use current nonretired authority; a historical director or
a role label cannot authorize a new override. Reopen remains a director action. Preserve old
actors, settings, blockers, resolutions and evidence, and recover committed view errors with
`render` instead of repeating a resolution.

## Start review at submission

Adopt the [review handoff](feature-program-tool.md#review-handoff-at-submission) when a task is
ready. Select an eligible independent reviewer and current director explicitly; recorded idle
status does not establish live availability. The immutable packet binds the exact submitted
round, source, criteria and proof. Successful canonical submission can publish the packet-ready
intent in the same transaction. A prepared packet or saved intent has not started a reviewer.

The active director consumes that ready result immediately, without waiting for unrelated units:

1. Recheck current packet/round/source, reviewer independence, actual native contact and any shared
   resource boundary. Use the produced packet directly instead of rebuilding its proof in a prompt.
2. In the supported dispatch-claim flow, publish the exact claim before the external native call.
   Only the successful claimant sends; a losing, missing or ambiguous claim sends nothing. A claim
   is not delivery, and
   expiry calls for reconciliation rather than authorizing an automatic resend.
3. For an actually idle/completed native reviewer, use `followup_task` to trigger a new turn. For an
   already running reviewer, use `send_message`. A saved idle profile is insufficient, and sending a
   message alone does not wake a completed reviewer. Keep this external action outside local writes.
4. Retain the actual dispatch attempt, contact/tool result and attributable reviewer reports.
   Sent, acknowledged and started are separate facts. An explicit combined report may retain both
   actual endpoints; never synthesize start from ACK, recorded status or a successful send.
5. Let independent work continue. The reviewer checks that exact packet and returns the complete
   bounded finding bundle and actual independent verdict through the ordinary review flow.
6. If delivery failed or its outcome is ambiguous, inspect the real native runtime and reconcile
   it before a deliberate retry. Unknown delivery means no automatic resend. Retrying a local
   record or recovering a committed view failure must never repeat the external native action.

Consume current ready work again at actual worker completion, source freeze and recovery boundaries.
No CLI read, import, dashboard refresh, batch or stored prompt launches an agent. A changed source,
round, definition or reviewer assignment invalidates incompatible current reliance while retaining
history; it does not prove an old reviewer stopped. For an existing submitted legacy task, explicit
adoption attaches a current packet without backdating dispatch or creating a second submit round.
Ordinary task acceptance and current required verification/provider gates remain unchanged.
For worker assignments, use the ordinary task claim and exact current task packet before the
actual native call. Retain attributable worker ACK/start in ordinary note/outcome evidence;
review dispatch claims and the review-report converter apply only to independent-review intents.
Do not invent a submitted review packet or another ledger for a worker assignment.

## Handoffs and recovery

A replacement begins with a durable outgoing record, or an explicitly marked reconstruction
when the predecessor is unavailable. Include:

- Objective, success criteria, communication language, constraints and confirmed authority.
- Current branch, source revision, owned paths and uncommitted changes.
- Decisions, dependencies, completed checks and immutable evidence references.
- Open findings, blocked decisions and the next concrete action.
- Predecessor, successor, reporting parent, role and known model settings.

The successor reads the record, inspects actual source and Git state, verifies evidence and
acknowledges receipt with fresh validation. Releasing a claim and reclaiming it creates a
new attempt; changing a display name does not transfer ownership. Preserve every prior
attempt and review. A director transfer also records the authority boundary and the one
current final director. A timeout cannot promote a sub-director or create a second director.

Before context compaction, save the checkpoint and ask a sub-director to reconcile omitted
decisions. After compaction, read the entrypoint, decisions, checkpoint and task packets;
consult a surviving sub-director, then compare its memory with files and current Git/CI.
Recreate an unavailable role from its saved contract. Files preserve recorded facts, not
unsaved thoughts, and local state is not backed up by a public code push.

## PR delivery and branch cleanup

Use a PR for each independently verifiable work unit; combine only closely coupled work.
Keep existing commit conventions and stage explicit public paths. Review the complete
public diff for private records before pushing. When an authorized unit has a stable,
reviewable public candidate, its PR checks may run alongside unfinished independent
review or QA. Keep those pending obligations visible; an open PR or green CI result
does not replace acceptance.

In this repository, validate work branches through pull-request CI, including additional
pushes to an open PR. Automatic push checks run after integration into `beta`; Python and
Unity validation retain their existing reusable entry points. A work branch without an
open PR has no automatic push validation. Use a supported manual workflow when an earlier
check is needed. Keep path filters and test requirements intact when avoiding duplicate runs.

Verify applicable GitHub Actions against the current PR head and relevant merge revision.
Inspect actual execution, not only the green summary: missing or unexpectedly skipped
required tests block acceptance. After fixes, re-review and verify the new revision.
An offline CI receipt does not authenticate GitHub; the director must inspect remote facts.

Use squash for every future authorized PR merge. Recheck the current PR head/base, review
and checks immediately before merging, apply the expected-head guard against the reviewed
head, and retain both that head and the resulting squash SHA. Preserve historical merge
records. Never substitute a direct feature push to the integration branch.

After confirmed delivery, verify that the branch has no later undelivered commits and its
reviewed content is included in the target. For a squash merge, retain the exact reviewed
head, squash result, target revision and required delivery checks as proof of that mapping.
Check that no worktree, active checkout or dependent PR uses the branch. Remove eligible
remote and local task branches using normal deletion; otherwise record deferred cleanup.
Do not delete the default, integration or protected branches, or automatically discard a
closed but unmerged PR branch. Keep the PR, commits, CI, reviews and handoff evidence.

### Unused worktrees

Retire local worktrees once their work and retained context no longer need that checkout.
Check actual agent/session use, servers and commands, review and dependent work, open PRs
and the recorded lock purpose before cleanup. A completed task or merged PR alone does not
prove disuse. Keep still-used worker or stable CLI checkouts and record the next cleanup trigger.

Bind delivered work to the reviewed head, merge or squash result, receiving revision and
successful required delivery checks. For temporary review or preparation copies, record why
they are unused and which source or recovery facts must survive. Recheck staged, unstaged
and untracked changes, unpushed or later commits, and needed ignored artifacts. Preserve
unexpected work and verify the retained copy or recoverable reference before removal.

Follow the [handoff and recovery procedure](#handoffs-and-recovery) for needed program state,
evidence and continuity. Keep these outside the checkout being removed, using supported
program relocation when required; a public push does not preserve private recovery records.
Move the active session and command working directory to a verified surviving checkout first.
If that move or an ongoing use cannot be resolved safely, defer removal with the reason.

For an app-managed worktree, use its supported recoverable archive and retain the returned
recovery reference. Preserve needed ignored files separately because its saved snapshot does
not include them. For an ordinary linked Git worktree, use normal `git worktree remove` from
a surviving checkout after the clean-worktree and usage checks pass. Resolve a lock only when
its purpose has ended. Preserve an unclean or unsupported checkout and record the blocker;
do not force removal, run blanket cleanup or substitute recursive filesystem deletion.

Verify physical absence and the expected Git registration or app archive state, with retained
artifacts and recovery references still usable. If an exact active checkout registration remains
after verified removal or archive, separately prove that the entry is stale and unused, then
reconcile only that entry through supported controls and verify the result. Defer unresolved
entries; do not run blanket pruning or removal. Record actual success, failure or deferred
cleanup through the supported program records before applying the branch checks above.
Archive or removal does not by itself delete a branch, authenticate delivery or grant authority.

## Objective completion

The director makes the final verdict against the human success criteria, required tasks,
independent reviews, actual CI and delivery evidence. Record unresolved limitations rather
than treating effort or an empty queue as completion. Category percentages count task leaves;
they do not independently prove objective acceptance.

Final acceptance also requires a fresh independent integrated-review artifact bound to the
current objective, full criteria, aggregate source scope and digest. Its reviewer must be
independent of the director and member-task implementers. Historical task reviews remain
valuable evidence, but they cannot cover later changes to shared code by themselves. Keep one bounded
index of original immutable proof refs and current source/input comparisons, adding only actual
changed-delta evidence. The index neither creates a verdict nor certifies inputs the reviewer did
not inspect. Do not rebuild a large proof graph or reopen a completed task solely to repackage it.

Keep historical execution and its applicability, accepted integrated member completion, and current
named-obligation/input coverage distinct. An accepted integrated review does not make old stale
lanes newly executed or fill an unmapped obligation. Relevant new failed, blocked or pending work,
missing evidence and changed definitions remain actionable even for done members. Use the shared
assessed integrated-review basis and its currentness/reasons rather than inferring completion from
a short queue, task counts or ready prose. The final fresh aggregate still requires actual current
source, full criteria/membership, independent review, delivery/CI and cleanup.

After acceptance, move to `awaiting_next_goal` and assign the next-goal-setting action to the
user. The director may propose options but starts no new major objective without the user's
input. Preserve the completed cycle when another begins. The dashboard should make the
current objective, decision owner and pending user action visible.

## Private records

Keep real plans, actor identities, source reports, receipts, checkpoints and screenshots under
the ignored `.work/programs/` directory, and session handoffs under `.work/handoffs/`. The
generic program default is `.work/programs/default`; name separate programs explicitly.
Relocate older programs through the tool's verified relocation flow and retain its recovery
archive instead of silently copying an active registry. Commit only portable code, sanitized guides and synthetic
tests. Bind the web service to loopback. Its only write surface accepts bounded responses to
existing director questions; task, agent and Git controls remain outside the web. Use exact
origin and CSRF checks, idempotent submission and stale-question rejection. Do not upload
state or add external assets or analytics to display progress.
## Recover with bounded current context

After selecting the canonical program and checking its location, use `brief` or
`brief --task TASK-ID` for bounded current context. Follow its original-record links
to full `status`, `packet`, snapshot or evidence when a collection is omitted or prose
is shortened. The brief is advisory: it does not replace fresh revalidation, authorize
a claim, acknowledge a question, accept a succession, perform a check or establish
delivery. Its checkpoint link is historical; current registry records take precedence.

Read integrity/recovery attention before acting. Then inspect current-goal decisions,
blockers and actual ownership collisions, review/transfer/delivery, and dependency or
revalidation context. A planned overlap with another planned task is not an owned-scope
collision. Excluded membership or disposed work must follow the recorded planning
workflow; a fresh receipt alone does not restore eligibility. Missing actor, receipt or
current execution metadata remains `not_assessed`.

Use current-attempt source, scope, required checks, findings and next action before
historical attempts. Keep task review separate from provider delivery and CI, and keep
both separate from independent integrated review of the exact current objective.
For a blocked attempt retained from review, inspect its canonical block event and
submission freshness. Resolve the block through the normal resume guards; changed
source requires rework and a new submission. Unknown legacy origins remain unassessed,
and historical terminal receipts remain historical.
Recorded failed/pending CI is a valid observation, not successful delivery. Frozen
provider facts never authenticate a remote service or imply cleanup.

Persist consequential peer coordination in each task's own notes and immutable
task/attempt evidence. Include the original shared contract locator/hash and the
counterpart's actual authored acknowledgement where available. Do not share one
attempt-scoped receipt path across tasks, infer acknowledgement from sending a message,
or reinterpret succession acceptance as generic interface agreement. The brief retains
authored wording while machine-assessed peer agreement remains unknown. Recorded actor
identity, contact and runtime liveness are distinct facts.

Each bounded collection reports total/shown/omitted; omitted severe or unknown rows
still count. The program/task byte limits cover actual escaped JSON output, not a
character estimate. Full integrity reads still occur before presentation is shortened.
If refresh is required or an essential envelope exceeds its limit, inspect the explicit
error and full original records. Do not repair locks, rerender views or change canonical
state merely because a read reports drift.
