# Feature program coordination tool

`tools/feature_program.py` keeps a reviewed feature backlog, ownership, evidence and recovery
checkpoints on disk. It uses the Python standard library and performs no GitHub, agent,
network or Unity operations. Use the existing Python environment described in
[the development formatting guide](../FORMATTING.md).

## Private state and public code

The default program directory is `.omo/feature-program/`, which is ignored by Git. Keep
actual source reports, plans, machine paths, agent identities, task packets, checkpoints
and execution evidence there. Public commits should contain the reusable tool, synthetic
tests and sanitized documentation. Never force-add the private program directory or paste
its content into a public PR. A code push does not back up local program state.

The current importer expects the repository's 110-item feature ID set. Supply a local source
report, a matching `planning.json` and the full commit SHA originally assessed by that
report. Initialization refuses to overwrite an existing registry. If a report was moved,
`--reference-base` retains its original repository-relative link base.

```sh
python tools/feature_program.py init --review-baseline <reviewed-commit-sha>
python tools/feature_program.py check
python tools/feature_program.py status
python tools/feature_program.py next
python tools/feature_program.py packet S01
```

Replace the example commit placeholder with a real SHA. `init --help` documents explicit
report/planning paths. Use the global `--program-dir` option for a separate local program
directory, and `--repo` for an explicit repository root. Tests create their own isolated
synthetic reports; they do not depend on a user's private backlog.

## State and review

The canonical `backlog.json` stores complete imported task prose, hashes, planning metadata,
attempts and events. `BOARD.md`, `tasks/<ID>.md` and `CHECKPOINT.md` are generated views;
regenerate them with `render`. Only the director changes canonical state through the CLI.
Workers edit claimed code and their own evidence. Atomic file replacement and a local lock
prevent concurrent writes; the tool does not automatically steal an existing lock.

Use actual `--help` and generated JSON templates to obtain the current schema:

```sh
python tools/feature_program.py template S01 --kind revalidation --scope path/to/source.py
python tools/feature_program.py template S01 --kind execution
python tools/feature_program.py template S01 --kind review
python tools/feature_program.py template S01 --kind delivery
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

Do not duplicate an unfinished claim or discard another worker's changes. Preserve receipts
and record an explicit release/reassignment when replacing an agent. Save a new checkpoint
after a meaningful handoff or before anticipated context compaction:

```sh
python tools/feature_program.py checkpoint --note "Reviewed current work" --next-action "Inspect the saved active attempt"
```

Files preserve recorded facts; they do not preserve unsaved thoughts or guarantee running
processes survive. A fresh clone also needs an independently authorized copy of private
program state before it can resume that program.

## Tool verification

```sh
python -m pytest tools/tests/test_feature_program.py -q
python tools/format_scripts.py --check tools/feature_program.py tools/tests/test_feature_program.py
python tools/lint_python.py
```

The existing Python Tests workflow discovers the tool's tests through `tools/tests/` on
matching pushes and pull requests. Verify the actual workflow result; local success alone
does not establish CI success.
