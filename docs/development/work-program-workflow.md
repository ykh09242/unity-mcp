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
The dashboard's selectable interface language is independent. Keep repository source and
public documentation in the language required by its authoring rules.

Keep the authority record explicit: who directs the program, which branch receives PRs,
whether the user authorized merges, and which actions remain outside scope. A portable
template is not authorization. A new session inherits recorded user decisions after checking
them against newer instructions; it must not request the same permission repeatedly.

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
public diff for private records before pushing.

In this repository, validate work branches through pull-request CI, including additional
pushes to an open PR. Automatic push checks run after integration into `beta`; Python and
Unity validation retain their existing reusable entry points. A work branch without an
open PR has no automatic push validation. Use a supported manual workflow when an earlier
check is needed. Keep path filters and test requirements intact when avoiding duplicate runs.

Verify applicable GitHub Actions against the current PR head and relevant merge revision.
Inspect actual execution, not only the green summary: missing or unexpectedly skipped
required tests block acceptance. After fixes, re-review and verify the new revision.
An offline CI receipt does not authenticate GitHub; the director must inspect remote facts.

When authorized to merge, recheck the current PR head/base, review and checks immediately
before merging, use the expected-head guard and retain the resulting merge SHA. Never
substitute a direct feature push to the integration branch.

After a confirmed merge, verify the branch has no later unmerged commits, is included in
the target, and is not used by a worktree, active checkout or dependent PR. Remove eligible
remote and local task branches using normal deletion; otherwise record deferred cleanup.
Do not delete the default, integration or protected branches, or automatically discard a
closed but unmerged PR branch. Keep the PR, commits, CI, reviews and handoff evidence.

## Objective completion

The director makes the final verdict against the human success criteria, required tasks,
independent reviews, actual CI and delivery evidence. Record unresolved limitations rather
than treating effort or an empty queue as completion. Category percentages count task leaves;
they do not independently prove objective acceptance.

Final acceptance also requires a fresh independent integrated-review artifact bound to the
current objective, full criteria, aggregate source scope and digest. Its reviewer must be
independent of the director and member-task implementers. Historical task reviews remain
valuable evidence, but they cannot cover later changes to shared code by themselves.

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
