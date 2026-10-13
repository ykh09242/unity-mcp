# Work Program dashboard design contract

## 0. Design references

- Use a compact operational ledger with aligned status and ownership columns, a separate
  personnel context and progressive disclosure for original records. These patterns support
  repeated inspection without a decorative dashboard-card hierarchy.
- The [list-detail pattern](https://github.com/changeroa/StyleGallery/blob/main/patterns/split-sidebar/list-detail.md)
  places the category rail outside the list/detail composition. Source order follows reading
  order; narrow screens use a single readable detail view with a contextual return path.
- [Carbon data-table guidance](https://www.carbondesignsystem.com/building-blocks/core/components/data-table/guidelines)
  informs the fluid working area, scan-friendly rows and grouped search/filter/paging controls.
  Readable prose keeps its own line-length limit inside the fluid workspace.
- [Fluent layout guidance](https://fluent2.microsoft.design/layout) informs consistent spacing
  and grouping. Neutral selected washes distinguish persistent selection; visible keyboard
  focus retains a separate outline.
- [WCAG reflow guidance](https://www.w3.org/WAI/WCAG22/Understanding/reflow.html) applies to
  navigation, controls and prose. Dense historical metadata stays secondary and must remain
  keyboard accessible at narrow widths and browser zoom.
- No remote assets or research integrations execute in the shipped dashboard. SVG sources
  are locally vendored with their license and exact upstream revision.

## 1. Atmosphere & Identity

A quiet workspace for directors and contributors. Its signature is a nested category rail
beside an aligned task ledger, with a separate page for task context. Visible task provenance,
review acceptance, and recorded delivery make the meaning of progress easy to inspect.
Content jobs: rail navigates; progress explains; filters locate; task rows compare; detail
proves; checkpoint and personnel retain context. Overview is a concise progress and action
view, Work is a searchable ledger, Team explains responsibility, and director questions have
focused response pages. The user supplies major goals; completion awaits their next goal.

## 2. Color

| Token    | Value        | Job                                              |
| -------- | ------------ | ------------------------------------------------ |
| canvas   | #08090a      | Deep workspace background                        |
| rail     | #0f1011      | Navigation and detail background                 |
| raised   | #191a1b      | Inputs and history records                       |
| hover    | #28282c      | Interactive hover surface                        |
| ink      | #f7f8f8      | Headings and task titles                         |
| body     | #d0d6e0      | Reading text                                     |
| muted    | #9da3ad      | Metadata; raised from the reference for contrast |
| accent   | #a5adff      | Links and keyboard focus                         |
| success  | #82d8b1      | Reviewed completion                              |
| warning  | #f0c786      | Stale/blocker information                        |
| error    | #f5a7ab      | Reserved error text                              |
| line     | white at 12% | Neutral separators                               |
| selected | white at 10% | Selected row wash                                |

Status meaning always has a text label. No decorative chromatic edges or glowing surfaces.

## 3. Typography

Local system sans-serif; task IDs and source labels use local system monospace.
No font downloads. Body 14px/1.5, metadata 12px, section labels 11px, task details 24px,
workspace title 28px. Weights 400, 510 and 590. Headings track at -0.025em. Full source
context preserves line breaks and wraps unbroken strings.

## 4. Spacing & Layout

Four-pixel base with 4, 8, 12, 16, 24 and 32px steps. Desktop rail 224px, main fluid with
a readable maximum for prose-oriented pages. Work uses the full available main pane,
with a flexible ledger and contextual detail; prose inside detail retains a readable measure.
Task pages have source and assignment columns; below 1100px those stack. Below 700px navigation and content also stack. Above 1100px the rail and workspace
each own vertical scrolling inside a bounded viewport shell, with `min-block-size: 0`.
Below that width the document owns scrolling. Reading and keyboard order follow the DOM.

## 5. Components

- Category button: nested navigation with unique leaf-task rollup, reviewed/total count;
  selected neutral wash, text-only label, native keyboard activation.
- Task row: a link to its own detail page, with title/context, status and owner. Selection
  has a neutral wash. Back navigation restores filters and focuses the selected row.
  At narrow widths titles use two readable lines, with compact status and ownership metadata.
  The exact one-line next-action glimpse and clipped title have a keyboard/touch path to full detail.
- Filters: visibly labeled native search/select controls. Empty results explain recovery.
  Snapshot polling preserves search values, task selection, expanded details and row focus.
- Detail: source prose, requirements, dependencies, checks, recorded ownership/settings,
  blocker/next action, delivery, handoffs and historical attempts. No editable controls.
  Dependencies and advisory relations link to referenced tasks and name their current
  recorded owner/contact. Missing tasks or contacts stay explicit; a contact label does not
  establish an active agent or provide a message transport.
- Personnel: management nesting, recorded assignment, settings provenance, last observed
  status and staffing history. Saved fields never imply authenticated runtime settings.
- Familiar utility and paging actions use pinned local Lucide SVGs, 18px glyphs and 40px
  square targets, localized accessible names and focus/hover tooltips. Domain actions retain text.
- Refresh status uses reserved language/status/action slots: initial loading, observed timestamp, stale integrity and unavailable
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
  Dates, numbers, guidance and known status/role values follow the interface choice. Reviewed
  human content follows the program's recorded session language through a separate source-bound
  presentation view. A current entry must match its typed identity, origin and original value;
  stale, withdrawn, unavailable or missing entries retain the original. Actor aliases never
  replace IDs used by routes, filters, list keys or assignments. Original text and exact locators
  are disclosed beside the relevant entity, not in a global prose list in the navigation rail.
  Saved answers, drafts, technical evidence, IDs and model settings remain original. Clicking
  a translated option explicitly copies its visible wording into the editable answer. Locale
  and presentation updates preserve existing drafts, caret, navigation and frozen retry payloads.

Primitives share native hover/active/focus treatment. Controls have 40px minimum height;
keyboard focus uses a 2px ring and 3px offset. Loading disables manual refresh only.

## 6. Motion & Interaction

No decorative animation. Hash navigation opens Overview, Work, Team, task or question pages.
Explicit navigation focuses the content landmark; returning to the ledger focuses its
selected task. Automatic polling preserves connected native focus and disclosure state. If a refreshed question removes the focused editor, focus continues at that question's current state heading; deliberately moved focus is preserved. React owns
the DOM; stable task, actor, question and history keys prevent replacement during refresh.
Polling skips hidden pages, prevents overlapping reads and times out after 12 seconds.
Reduced-motion users receive the same static UI. Server restarts refresh the session nonce.

## 7. Depth & Surface

Mixed tonal layers and neutral hairline separators; no shadows. Controls use 6px radii.
Ledger rows are square and separated by single lines. History records use raised surfaces.

## 8. Accessibility Constraints & Accepted Debt

Target WCAG 2.2 AA contrast and keyboard access. Native labels, landmarks, skip link,
visible focus, text status labels, non-color progress explanation and polite explicit refresh status. Routine background polls update the visible freshness text without live announcements.
Primary content targets reflow at 320 CSS pixels and must survive long/unbroken prose. Task
pages are not virtualized: browser find and assistive reading can reach each rendered page.
Filtering and sorting apply to the full list before explicit pagination.

Automated checks do not establish screen-reader conformance. Fresh rendered verification
and independent review are required before reporting completion. No accepted visual or
interaction defects are recorded at design time; any found defects must be fixed or listed
with the actual affected surface and follow-up owner.

## Recorded history

Current task and review facts precede optional history. Attempts and review rounds retain
recorded checks, findings and exact submitted identities; comparison labels describe only
same, different or unknown supplied values. Explicit coverage links connect criteria to
task paths and frozen evidence without computing acceptance or review readiness.

Delivery observations remain in canonical append order, independently of current delivery.
PR, CI conclusions, revision mismatches, merge and cleanup are separate facts. Missing
projection differs from an explicitly empty history. Local event times never imply provider
observation times; absent or ambiguous times stay unknown. Original records and exact
references remain available in secondary disclosures, with longer reference lists expanded
on demand. No source-file endpoint or provider query is introduced.

Team rows show compact saved role/model/effort/provenance for current assignees. Staffing
history compares frozen claim settings with current saved metadata; it does not infer a
running agent changed settings. Transfer stages retain every supplied acknowledgement and
compare only supplied context. Objective membership and source amendments expose explicit
added/removed task links, binding changes and receipts. Authored prose stays unchanged;
system copy is concise and localized. Polling keeps disclosure and navigation context.

## Program summary and focus continuity

Overview leads with its page heading, current objective and scoped progress. Related decision
narratives remain in a secondary collapsed section; the attention summary and question routes
retain their actionable links without an expanded duplicate list before the objective.
Overview consumes only the supplied, revision-bound program brief and coverage records.
Attention includes every supplied kind, uses a short initial list with an explicit expansion,
and distinguishes visible, supplied and omitted counts. Missing or mismatched projections
remain unavailable. Exact source references and complete supplied originals are secondary.
Criterion contributors, uncovered reasons, frozen checks, annotation freshness, recorded
objective acceptance and integrated review remain separate observations. None establishes
claim authority or criterion completion by inference.

Human-authored copied fields retain their source-bound session-language presentation;
interface language changes only system labels. Recovery artifact references are observed
local paths, not invented backlog identities or file-fetch capabilities.

Mounted Refresh remains keyboard reachable while requests are pending; the guarded action
prevents duplicate reads. Initial loading retains a disabled control. Explicit filter removal
continues at its stable search field. Question filters share a wide row and stack at narrow
widths; narrow navigation preserves complete English words. Korean prose retains ordinary
word boundaries with emergency wrapping for long technical identifiers.
