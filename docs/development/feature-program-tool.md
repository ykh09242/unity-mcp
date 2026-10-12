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
passing evidence. Use the same `--program-dir` for every subsequent command. A new major
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
