# Work Program dashboard design contract

## 0. Research Log

- Embedded references: shortlisted Linear, GitHub and Notion. Selected the operational
  taste reference plus Linear for compact rows, tonal depth and consistent task anatomy.
  These are inspiration, not an implementation of an official component library.
- Lazyweb: two public queries, `project management list detail dashboard` and
  `project management hierarchy people assignments`; two screens actually viewed
  (Rocketlane and Toggl Track). Adopted aligned status/ownership columns and a separate
  personnel context. No screenshot, brand asset or proprietary copy is shipped.
- StyleGallery: adopted its [list-detail pattern](https://github.com/changeroa/StyleGallery/blob/main/patterns/split-sidebar/list-detail.md).
  The category rail is outside the list/detail composition. Source order follows reading order.
- Image concepts: two synthetic concepts, a three-column list/detail view and a detail-below
  view. Selected the first for desktop; the second informs the narrow-screen reflow.
  Research images remain private and ignored. The generated selection outlines are replaced
  by neutral selected washes; only keyboard focus uses an accent outline.
- No remote assets or research integrations execute in the shipped dashboard.
- Framework documentation: Context7 CLI was unavailable because of its monthly quota.
  Used official [React effects](https://react.dev/learn/synchronizing-with-effects),
  [Vite production builds](https://vite.dev/guide/build.html) and
  [Vitest](https://vitest.dev/guide/) documentation. Research was reused when the user
  explicitly requested React, separate pages and the question-response flow.

## 1. Atmosphere & Identity

A quiet workspace for directors and contributors. Its signature is a nested category rail
beside an aligned task ledger, with a separate page for task context. Visible task provenance,
review acceptance, and recorded delivery make the meaning of progress easy to inspect.
Content jobs: rail navigates; progress explains; filters locate; task rows compare; detail
proves; checkpoint and personnel retain context. Overview is a concise progress and action
view, Work is a searchable ledger, Team explains responsibility, and director questions have
focused response pages. The user supplies major goals; completion awaits their next goal.

## 2. Color

| Token | Value | Job |
| --- | --- | --- |
| canvas | #08090a | Deep workspace background |
| rail | #0f1011 | Navigation and detail background |
| raised | #191a1b | Inputs and history records |
| hover | #28282c | Interactive hover surface |
| ink | #f7f8f8 | Headings and task titles |
| body | #d0d6e0 | Reading text |
| muted | #9da3ad | Metadata; raised from the reference for contrast |
| accent | #a5adff | Links and keyboard focus |
| success | #82d8b1 | Reviewed completion |
| warning | #f0c786 | Stale/blocker information |
| error | #f5a7ab | Reserved error text |
| line | white at 12% | Neutral separators |
| selected | white at 10% | Selected row wash |

Status meaning always has a text label. No decorative chromatic edges or glowing surfaces.

## 3. Typography

Local system sans-serif; task IDs and source labels use local system monospace.
No font downloads. Body 14px/1.5, metadata 12px, section labels 11px, task details 24px,
workspace title 28px. Weights 400, 510 and 590. Headings track at -0.025em. Full source
context preserves line breaks and wraps unbroken strings.

## 4. Spacing & Layout

Four-pixel base with 4, 8, 12, 16, 24 and 32px steps. Desktop rail 224px, main fluid with
1180px readable maximum. Task pages have source and assignment columns; below 1100px those
stack. Below 700px navigation and content also stack. Above 1100px the rail and workspace
each own vertical scrolling inside a bounded viewport shell, with `min-block-size: 0`.
Below that width the document owns scrolling. Reading and keyboard order follow the DOM.

## 5. Components

- Category button: nested navigation with unique leaf-task rollup, reviewed/total count;
  selected neutral wash, text-only label, native keyboard activation.
- Task row: a link to its own detail page, with title/context, status and owner. Selection
  has a neutral wash. Back navigation restores filters and focuses the selected row.
  At narrow widths the owner occupies a full row. Titles wrap instead of clipping.
- Filters: visibly labeled native search/select controls. Empty results explain recovery.
  Snapshot polling preserves search values, task selection, expanded details and row focus.
- Detail: source prose, requirements, dependencies, checks, recorded ownership/settings,
  blocker/next action, delivery, handoffs and historical attempts. No editable controls.
  Dependencies and advisory relations link to referenced tasks and name their current
  recorded owner/contact. Missing tasks or contacts stay explicit; a contact label does not
  establish an active agent or provide a message transport.
- Personnel: management nesting, recorded assignment, settings provenance, last observed
  status and staffing history. Saved fields never imply authenticated runtime settings.
- Refresh status: initial loading, observed timestamp, stale integrity and unavailable
  snapshots. Failed refresh retains previous records with explicit observation time.
- Progress: native labeled progress element counts reviewed leaf tasks. Recorded PR/CI
  occupies a separate block and explicitly describes its unauthenticated provenance.
- Question inbox: current questions have concise links; more than three fold into a
  disclosure. Applied and withdrawn history stays collapsed. The form preserves typed text
  across polling, offers recorded choices, prevents duplicate submit and retries with the
  same idempotency identity after an uncertain save. Saved, acknowledged, applied and
  withdrawn are separate text states. Only answering an existing question is writable.
- Language: English and Korean interface choices persist locally. The initial choice follows
  a saved interface preference, then the program communication language, then browser locale.
  Dates, numbers, guidance and known status/role values follow the interface choice. Recorded
  task titles, question prompts, options, answers and acknowledgement prose stay unchanged.
  Switching language and polling preserve the current answer draft.

Primitives share native hover/active/focus treatment. Controls have 40px minimum height;
keyboard focus uses a 2px ring and 3px offset. Loading disables manual refresh only.

## 6. Motion & Interaction

No decorative animation. Hash navigation opens Overview, Work, Team, task or question pages.
Explicit navigation focuses the content landmark; returning to the ledger focuses its
selected task. Automatic polling preserves native focus and disclosure state. React owns
the DOM; stable task, actor, question and history keys prevent replacement during refresh.
Polling skips hidden pages, prevents overlapping reads and times out after 12 seconds.
Reduced-motion users receive the same static UI. Server restarts refresh the session nonce.

## 7. Depth & Surface

Mixed tonal layers and neutral hairline separators; no shadows. Controls use 6px radii.
Ledger rows are square and separated by single lines. History records use raised surfaces.

## 8. Accessibility Constraints & Accepted Debt

Target WCAG 2.2 AA contrast and keyboard access. Native labels, landmarks, skip link,
visible focus, text status labels, non-color progress explanation and polite refresh status.
Primary content must reflow at 375px and survive long/unbroken prose. No virtualized task
list, so browser find and assistive reading can reach every filtered task.

Automated checks do not establish screen-reader conformance. Fresh rendered verification
and independent review are required before reporting completion. No accepted visual or
interaction defects are recorded at design time; any found defects must be fixed or listed
with the actual affected surface and follow-up owner.
