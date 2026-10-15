# Local work-program dashboard

The React and TypeScript interface is served by the standard-library Python server.
Committed generated assets let the dashboard run from a clean checkout without Node.js.
Task views read the configured program only. The sole write endpoint saves an answer to an
existing director question; it cannot claim tasks, change task status or operate agents.

From the repository root, using the existing Python environment:

```powershell
& .\Server\.venv\Scripts\python.exe tools/feature_program_web.py --repo . --program-dir .work/programs/default --port 8765
```

Open the URL printed by the server. Bindings are limited to `127.0.0.1`, `localhost` and
`::1`. Use `--read-only` to disable question responses. Stop the service with Ctrl+C.
Program creation and director decisions belong to the program CLI, not the dashboard.

Start with **Overview** for the objective, reviewed progress, blockers and next action.
**Work** provides hierarchical categories and task filters. Opening a task shows source
context, assignment, dependencies, review history and recorded delivery on a separate page.
Dependency and advisory task links name the current recorded owner/contact, when available,
so contributors can identify whom to coordinate with. They do not send messages or prove
that an agent is currently running.
**Team** shows management relationships, role responsibilities, settings provenance and
handoffs. Returning from a task preserves the ledger filters and selected task.

Work also offers a read-only Review queue with explicit readiness and next responsibility.
It shares current producer facts with Team, retains submitted reviews and check coverage gaps,
and qualifies omitted or unavailable work. Task Information keeps check inputs, actual captured
execution, evidence completeness and retry history separate from acceptance and delivery.
An explicit server `--closure-plan` selects one immutable local plan or captured preparation
failure for current preparation/preflight/publication inspection. It adds no browser execution
or publication controls.

Director questions appear on Overview. Their pages support suggested choices and text
answers. Saving an answer means it is stored locally; director acknowledgement and applied
outcome are later, separate recorded steps. A withdrawn question is shown as withdrawn.
A stopped director is not automatically woken by the server. Response identity is recorded
as an unverified local human, not an authenticated account.

Refresh reads saved records every five seconds while the page is visible. The UI identifies
fetch time, source-integrity warnings and unavailable snapshots independently of personnel
observation time. It preserves the previous snapshot and answer drafts after failed reads.
Model metadata and saved owner strings do not prove current agent settings or liveness.
PR/CI receipts are locally recorded; open the provider links to inspect actual remote state.

The server exposes only its packaged assets, snapshot/session reads and the exact answer
route. Responses require the current same-origin session nonce and an Origin matching the
server authority. JSON size, exact fields, question version and idempotency identity are
bounded and validated. No evidence-download or arbitrary file-browsing endpoint exists.
The dashboard makes no CDN, font, analytics or third-party requests.

The header language selector supports English and Korean and remembers the interface choice
in local browser storage. Without a saved choice, the program communication language takes
precedence over the browser locale. Switching the interface does not translate recorded task
or question content and preserves an answer draft. The program language controls how the
director authors new questions and persists through personnel handoffs.

## Frontend development

Use Node.js 24.15 or later in the 24 series, or Node.js 26 or later. Direct dependencies are exact stable versions in `package.json`,
with transitive resolution in `package-lock.json`.

```sh
cd tools/feature_program_web
npm ci --ignore-scripts --no-audit --no-fund
npm run format:check
npm run typecheck
npm test
npm run build
```

`npm run format` formats authored sources using the package-local Prettier configuration.
The repository formatter can check explicit files in this package as well. Generated assets,
dependencies and the lockfile are excluded from source formatting.

Vite writes `generated/index.html`, `generated/app.js` and `generated/style.css`. Regenerate
these from source; never hand-edit them. Keep the Python server's static allowlist aligned
with any intentional new asset. The web workflow checks formatting, types, behavior tests
and a reproducible build, including unexpected new generated files.
Work branches run this validation when a pull request is opened or updated. Direct pushes
run it only on beta; manual workflow execution remains available.

Run server checks from the repository root:

```powershell
& .\Server\.venv\Scripts\python.exe -m pytest tools/tests/test_feature_program_web.py -q
```

These tests use isolated synthetic programs and HTTP processes. Real browser verification
must also cover the affected routes, keyboard flow, narrow reflow and question lifecycle.
Keep actual program records, screenshots and execution evidence in ignored local storage.

## Recorded review flow

The existing Review queue and task Review section distinguish a prepared handoff, an
actual request sent, reviewer acknowledgement, review start and terminal observations.
The next responsible actor and trigger remain visible. Team keeps submitted/requested
reviews primary and groups check-only gaps by task in one optional section.

Overview qualifies observed review waiting by its full producer cohort and measurement
coverage. Submission-observed-to-review-start is separate from observed review span,
verification waiting, sealed process duration and recorded history. Missing endpoints
remain unavailable. An explicitly retained age is fixed at its last observation; the
browser does not tick, sort by age or substitute fetch time for a missing event.

Retained evidence and selected evidence preparation preserve original outcomes and
explicit missing decisions. A record-batch summary separates preparation, actual
publication, historical applicability and view recovery. These read-only summaries do
not execute commands, dispatch reviewers, publish state or imply acceptance. Legacy
absence stays quiet; explicit malformed or stale facts remain inspectable. Interface
copy follows the chosen locale while reviewed human content retains its separate
source-bound session-language presentation.
