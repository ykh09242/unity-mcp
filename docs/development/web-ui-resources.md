# Web interface design and review resources

Use these annotated publisher sources with the [review procedure](web-ui-review.md). Stable IDs
let a design decision or review finding point to its rationale. The checks below are project
applications of source principles, not quotations or universal design mandates.

The catalog was prepared from publisher-page text research. It does not claim rendered-source
inspection, application visual quality or accessibility conformance. W3C Understanding documents
explain success criteria; consulting them is not a conformance audit. Verified publication or
update dates are included where useful, and unknown dates remain unknown.

For each use, record the relevant source/version, review date, evidence type, task, adaptation and
limits in the project evidence. Recheck a source when its link, relevant guidance or task changes;
there is no recurring search quota. Publisher-specific spacing, breakpoints and branding should
not become unexplained product rules.

## Interface references

### UI-R01 — Task-appropriate grid

- Publisher/title: IBM Carbon, **2x Grid overview**.
- Source: [Publisher page](https://www.carbondesignsystem.com/building-blocks/foundations/2x-grid/overview)
- Source metadata: source updated 2026-09-25; official page text.
- When useful: choosing list/detail allocation for reading or comparing records.
- Principle: choose fluid, fixed or hybrid layout according to whether the task needs more items or more content per item.
- Verify: compare full viewport, main content, list and detail bounds at supported widths; demonstrate useful content expansion and aligned edges. Explain any capped width against the actual reading or comparison task.
- Limits: do not import Carbon breakpoints or spacing blindly. Reading prose can justify a line-length cap; a cramped operational detail panel needs its own evidence.

### UI-R02 — Grouping and hierarchy

- Publisher/title: Microsoft Fluent 2, **Layout**.
- Source: [Publisher page](https://fluent2.microsoft.design/layout)
- Source metadata: source update date unknown; official page text.
- When useful: organizing dense controls and related facts into readable regions.
- Principle: proximity, group spacing and region sizes communicate relationships and hierarchy.
- Verify: identify the primary task and related controls in each surface; inspect whether region size and spacing support that task across narrow and wide layouts.
- Limits: whitespace is not a quota. Empty area does not justify compressing important content elsewhere.

### UI-R03 — Dense data and expanded detail

- Publisher/title: IBM Carbon, **Data table guidelines**.
- Source: [Publisher page](https://www.carbondesignsystem.com/building-blocks/core/components/data-table/guidelines)
- Source metadata: source updated 2026-09-30; official page text.
- When useful: designing tables, toolbars, pagination and expanded records.
- Principle: provide adequate data width, coherent toolbars and pagination; move cramped expanded details into a suitable panel or page.
- Verify: inspect column readability, consistent density, visible sort/filter/paging state, expanded detail and toolbar overflow. Evaluate crowded action groups, including those exceeding five actions, in context.
- Limits: a data table is not a spreadsheet substitute. Five actions is contextual guidance, not a global failure threshold.

### UI-R04 — Progressive disclosure

- Publisher/title: Nielsen Norman Group, **Progressive Disclosure**.
- Source: [Publisher page](https://www.nngroup.com/articles/progressive-disclosure/)
- Source metadata: published 2006-12-03; publisher article text.
- When useful: separating frequent actions from supplementary detail.
- Principle: expose frequent choices first and give secondary information an obvious, named entry.
- Verify: complete each primary task without searching hidden help or settings; inspect the discoverability and return path of secondary detail.
- Limits: do not hide frequent controls, essential instructions or warnings. This is older foundational guidance, not a current component specification.

### UI-R05 — Action hierarchy and names

- Publisher/title: GitLab Pajamas, **Button**.
- Source: [Publisher page](https://design.gitlab.com/components/button/)
- Source metadata: source update date unknown; official page text.
- When useful: choosing prominent actions or making icon controls understandable.
- Principle: distinguish action hierarchy and provide action-specific accessible names, especially for ambiguous or changing icons.
- Verify: primary action recognition, keyboard order, disabled states, destructive object names and icon-only names in both UI languages.
- Limits: GitLab's SVG-only convention and preference against icon-plus-text are local choices, not portable requirements.

### UI-R06 — Supplementary tooltips

- Publisher/title: GitLab Pajamas, **Tooltip**.
- Source: [Publisher page](https://design.gitlab.com/components/tooltip/)
- Source metadata: source update date unknown; official page text.
- When useful: explaining a control or revealing a truncated value.
- Principle: use short supplementary context or full truncated values; a tooltip alone is not an accessible name.
- Verify: pointer and keyboard access, name consistency, truncation recovery and clipping at viewport/panel edges.
- Limits: required instructions, rich content and interactive controls must not exist only in a tooltip.

### UI-R07 — Hover and focus content

- Publisher/title: W3C WAI, **Understanding SC 1.4.13: Content on Hover or Focus**.
- Source: [Publisher page](https://www.w3.org/WAI/WCAG22/Understanding/content-on-hover-or-focus.html)
- Source metadata: WCAG 2.2, level AA; source update date unknown; official explanatory text.
- When useful: inspecting custom content that appears on pointer hover or keyboard focus.
- Principle: qualifying custom content is dismissible, hoverable and persistent.
- Verify: reach it with pointer and keyboard, move onto it, and dismiss obscuring content without moving focus where required.
- Limits: apply the criterion's conditions and browser-controlled presentation exception rather than treating every native tooltip identically.

### UI-R08 — Pointer target minimum

- Publisher/title: W3C WAI, **Understanding SC 2.5.8: Target Size (Minimum)**.
- Source: [Publisher page](https://www.w3.org/WAI/WCAG22/Understanding/target-size-minimum.html)
- Source metadata: WCAG 2.2, level AA; source update date unknown; official explanatory text.
- When useful: checking compact controls and adjacent pointer targets.
- Principle: actual pointer targets meet 24 by 24 CSS pixels or an applicable exception.
- Verify: measure hit regions, not only SVG pixels; evaluate the specified spacing-circle exception and adjacent target separation where needed.
- Limits: sub-24 dimensions are not automatically a failure; minimum compliance does not establish visual comfort or readable icon scale.

### UI-R09 — Stable loading geometry

- Publisher/title: Google web.dev, **Optimize Cumulative Layout Shift**.
- Source: [Publisher page](https://web.dev/articles/optimize-cls)
- Source metadata: source updated 2025-02-07; official page text.
- When useful: diagnosing layout movement during loading or refresh.
- Principle: reserve appropriate late-loading geometry and diagnose actual shifts.
- Verify: capture header/control bounds before, during and after refresh, including changed status text and locale labels; inspect frames and loading space alongside any metric.
- Limits: CLS can exclude shifts shortly after input. A passing metric or an excessively large blank reserve is not proof of stable, useful layout.

### UI-R10 — Status feedback without focus theft

- Publisher/title: W3C WAI, **Understanding SC 4.1.3: Status Messages**.
- Source: [Publisher page](https://www.w3.org/WAI/WCAG22/Understanding/status-messages.html)
- Source metadata: WCAG 2.2, level AA; source update date unknown; official explanatory text.
- When useful: reporting asynchronous refresh, save, failure and recovery results.
- Principle: expose waiting, results, progress and errors without requiring focus changes.
- Verify: actual announcement behavior for refresh, save, failure and recovery; preserve reading and focus. Attributes alone do not prove announcements work.
- Limits: do not announce every cell or poll; result content and a result-status message have different roles.

### UI-R11 — Contextual messages

- Publisher/title: Atlassian Design, **Designing messages**.
- Source: [Publisher page](https://atlassian.design/foundations/content/designing-messages)
- Source metadata: source update date unknown; official page text.
- When useful: choosing local feedback for empty, pending or failed tasks.
- Principle: scope messages to the task and give a relevant next action.
- Verify: distinguish first-use empty, filtered no-results, loading, partial failure, fatal error and last-good recovery on actual surfaces.
- Limits: routine local refresh should not automatically become a global banner or modal interruption.

### UI-R12 — Concise and localizable content

- Publisher/title: Atlassian Design, **Style, grammar and punctuation**.
- Source: [Publisher page](https://atlassian.design/foundations/content/language-and-grammar/)
- Source metadata: source update date unknown; official page text.
- When useful: writing system labels, helper text and localized action names.
- Principle: use familiar terms, concise action labels, consistent sentence case and wording that can be localized.
- Verify: contextual strings, repeated introductions, long translations and locale choice during refresh; keep source IDs and authored content intact.
- Limits: English article, case and contraction rules do not mechanically apply to Korean. Locale persistence is our predictable-interaction requirement, not a quoted rule from this source.

### UI-R13 — Reflow

- Publisher/title: W3C WAI, **Understanding SC 1.4.10: Reflow**.
- Source: [Publisher page](https://www.w3.org/WAI/WCAG22/Understanding/reflow.html)
- Source metadata: WCAG 2.2, level AA; source update date unknown; official explanatory text.
- When useful: checking narrow layouts and genuine browser zoom.
- Principle: ordinary content reflows at the equivalent of 320 CSS pixels; genuinely two-dimensional content has a scoped exception.
- Verify: narrow and zoomed search, headings, pagination and actions; contain table scrolling and preserve readable cells.
- Limits: do not flatten necessary two-dimensional data or exempt all surrounding controls because a table exists.

### UI-R14 — Visible focus and interaction motion

- Publisher/title: W3C WAI, **Understanding SC 2.4.7: Focus Visible**, and **SC 2.3.3: Animation from Interactions**.
- Sources: [Focus Visible](https://www.w3.org/WAI/WCAG22/Understanding/focus-visible.html) and [Animation from Interactions](https://www.w3.org/WAI/WCAG22/Understanding/animation-from-interactions.html)
- Source metadata: WCAG 2.2, focus level AA and animation level AAA; source update dates unknown; official explanatory text.
- When useful: checking keyboard tasks and nonessential interaction animations.
- Principle: maintain visible keyboard focus and allow prevention of nonessential interaction motion.
- Verify: complete keyboard tasks and return paths; inspect reduced-motion behavior and focus visibility in selected, disabled and refreshed states.
- Limits: essential motion has an exception. The animation criterion is AAA, not an AA mandate.

### UI-R15 — Whole-task usability review

- Publisher/title: Nielsen Norman Group, **10 Usability Heuristics for User Interface Design**.
- Source: [Publisher page](https://www.nngroup.com/articles/ten-usability-heuristics/)
- Source metadata: source reviewed 2024-01-30; publisher article text.
- When useful: reviewing complete tasks and explaining the consequence of a usability finding.
- Principle: visible status, familiar language, consistency, recognition, essential content and recoverable errors support real tasks.
- Verify: each finding identifies a user task, observed problem, evidence, criterion, severity and remedy; review visual hierarchy as well as functional completion.
- Limits: completing a checklist or passing automated tests does not establish visual quality.

## Korean product-writing references

These separate IDs identify Korean publisher material. Titles here are English descriptive
translations. Their product practices are useful inputs, not universal Korean language standards.
All entries were researched as publisher text; they are not inspected visual examples.

### UI-K01 — Concise action-oriented writing

- Publisher: Toss Tech, Kim Jayu, **Eight writing principles**.
- Source: [Writing principles](https://toss.tech/article/21022).
- Source metadata: published 2022-11-15; publisher article text.
- When useful: simplifying Korean labels and repeated screen explanations.
- Principle: name the next action, remove meaningless repetition and give each short natural sentence one main message.
- Apply: read the complete screen in task order; remove filler and check that buttons predict their actions.
- Limits: brevity must not remove a material consequence, necessary technical term or recovery instruction.

### UI-K02 — Useful error messages

- Publisher: Toss Tech, Kim Jayu, **Six principles for useful error messages**.
- Source: [Error-message principles](https://toss.tech/article/21021).
- Source metadata: published 2022-09-21; publisher article text.
- When useful: explaining failures and providing an achievable next action.
- Principle: explain the actual situation, known cause and reachable recovery action with treatment appropriate to severity.
- Apply: compare local failure, stale data, rejected save and recovery messages with actual behavior.
- Limits: do not guess a cause or promise recovery that the product cannot perform. Retained data and unsaved edits need explicit truthful treatment.

### UI-K03 — Consistent product terminology

- Publisher: Toss Place, **UX writing**.
- Source: [Writing guidance](https://docs.tossplace.com/guide/front-integration/plugin/design/ux-writing.html).
- Source metadata: update date unknown; official page text.
- When useful: aligning terminology across navigation, forms and dialogs.
- Principle: start from the audience and repeated use, simplify noun chains and use predictable action terms.
- Apply: distinguish closing a view from cancelling work; check the same action uses consistent terms throughout navigation, forms and dialogs.
- Limits: point-of-sale conventions require adaptation to a developer dashboard; retain exact technical identities when they matter.

### UI-K04 — Whole-screen reading flow

- Publisher: Toss Tech, Kim Jayu, **A new standard for UX writing**.
- Source: [Whole-screen writing discussion](https://toss.tech/article/toss-people-2).
- Source metadata: published 2024-10-15; publisher article text.
- When useful: checking whether individually concise strings form a coherent screen.
- Principle: good individual sentences can still form a repetitive or disconnected screen.
- Apply: review headings, helper text, badges, notices and actions together in both languages and with long content.
- Limits: sentence-level translation checks do not establish hierarchy, natural reading flow or successful interaction.

### UI-K05 — Rules derived from real product cases

- Publisher: Toss Tech, Kim Jayu, **What the first UX writer should do**.
- Source: [Product-specific guideline development](https://toss.tech/article/1st_uxwriter).
- Source metadata: published 2023-04-14; publisher article text.
- When useful: maintaining product writing examples and justified exceptions.
- Principle: derive usable writing rules from actual product cases instead of importing an abstract voice guide unchanged.
- Apply: keep a small set of reviewed examples for empty, filtered-empty, pending, failed, unknown and successful states; record deliberate exceptions.
- Limits: examples must preserve the product's actual behavior and should evolve when that behavior changes.

## Developer-dashboard adaptation and maintenance

Use calm, concise explanations and predictable headings, badges and actions. Keep necessary
identifiers and diagnostic detail available. Explain recorded-versus-live boundaries in relevant
help without repeating the same disclaimer everywhere; retain material stale/error feedback next
to the affected fact. Preserve authored task, finding and answer text rather than translating it
as system UI. These are dashboard adaptations, not direct source requirements.

Maintain stable IDs when an annotation changes. Record changed guidance and its impact privately;
a new source check does not retroactively validate a previous build. Prefer direct publisher
links and avoid superseded editions, unsupported redirects and third-party reproductions when
an inspected primary source is available. Add resources for a concrete decision, not to enlarge
the catalog. Verify application behavior using the review procedure after applying the guidance.
