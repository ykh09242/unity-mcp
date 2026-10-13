# Reviewing dashboard interfaces

Use this procedure to turn relevant design guidance into observable behavior and review the
interface people actually use. It complements the [feature program tool guide](feature-program-tool.md)
and [annotated resource catalog](web-ui-resources.md). It does not confer execution authority,
prove accessibility conformance or replace the project's acceptance and delivery gates.

## Start with tasks and surfaces

Identify users, repeated tasks, constraints and information needs before choosing a layout or
component pattern. Select relevant resources from the catalog and distinguish source guidance
from a project adaptation. Record meaningful alternatives and why the chosen approach fits.

Inventory actual routes, shared navigation and header, nested components, dialogs, disclosures
and meaningful states. For this dashboard, include Overview, Work lists and selected details,
Team, questions and answer forms, evidence, and nested attempts, reviews, checks, delivery and
handoffs. Include first-use help and recovery surfaces. Give every entry an applicable principle,
a concrete check, a justified non-applicability decision or an unresolved finding. A component's
presence and a fixed inventory count are not acceptance evidence.

The researcher supplies sources and limits. The implementer defines observable behavior and
produces the intended build. An independent reviewer inspects that build and its actual rendered
states. The director reconciles findings and accepts the verified result. Keep these roles and
their evidence distinct; a successful build or green test count does not establish visual quality.
The director selects staffing and model settings within the existing user authorization. These
roles describe responsibilities, not a mandatory number of agents or a new permission gate.
The local dashboard remains a read-only view except for its explicit answer-saving surface;
it does not execute agents or apply decisions.

## Design the whole reading and interaction flow

Allocate space according to the task. A record-comparison view may need a fluid list and detail
region, while long prose benefits from a restrained reading measure. Evaluate both against the
whole available viewport. Neither a universal maximum width nor a target whitespace percentage
answers whether important content is cramped.

Group related controls, emphasize the primary task and make secondary information discoverable.
Keep frequent actions and material warnings visible. Expanded details must remain readable, and
summaries must preserve access to complete original records. Distinguish absent, unknown,
unavailable, stale and not applicable; omitted rows do not mean no remaining conditions.

Review complete screens for concise, natural copy and consistent terminology. Name actions by
what they do. Closing a detail view does not cancel work. Avoid repeated introductions, guessed
causes, celebration and invented progress. Translate system labels consistently while preserving
authored text, source identifiers and technical precision. The Korean-writing resources inform
product-specific adaptations; English grammar rules are not a mechanical Korean style guide.

Use recognizable icons at a coherent visual scale, with meaningful accessible names and actual
hit regions. Check the target separately from the SVG dimensions. Tooltips provide supplementary
context; they do not replace accessible names or required instructions. Verify pointer, keyboard,
dismissal and clipping behavior where a custom tooltip appears.

Convey status through text or icons as well as color. Inspect contrast, visible focus, selected
and disabled states, headings and semantic controls. Verify local dates and numbers without
rewriting stored values. Announce meaningful waiting, results and errors without stealing focus
or announcing every polling cycle; inspect actual announcement behavior, not attributes alone.

## Bind the build and data before observing

Record the authored source identity, relevant uncommitted changes, generated asset identity and
the bytes or build identity actually served to the browser. A commit does not prove which bundle
a running service serves. Confirm the intended entry document, asset responses and data source;
account for caching where relevant. Record the data revision and relevant semantic content,
since unchanged registry identity can still accompany changed freshness or integrity facts.

Use real records or clearly labeled valid fixtures with appropriate authorization. Do not invent
passing checks, acknowledgements or successful provider results to make a screen look complete.
Preserve private data and detailed execution evidence in the project's designated private area;
public guides should contain only reusable methods and resources.

## Choose a finite, proportionate matrix

Select cases from changed behavior, shared dependencies and unresolved risks. Include narrow and
wide views, affected breakpoint neighbors, both supported UI languages, long content, keyboard,
zoom and reduced motion where relevant. Cover loading, empty, filtered-empty, unknown, error,
stale and recovery states that the surface can actually produce.

Choose widths and heights from supported user contexts and the affected layout, rather than
importing a universal size list or a mandatory Cartesian product. Map shared components to
representative checks and inspect each surface's unique behavior. Include list-only, selected
detail and return paths; one Work screenshot cannot establish full-dashboard coverage.

Use genuine browser zoom for zoom cases, including an applicable 200-percent case and ordinary
content at a 320-CSS-pixel-equivalent reflow condition. Record the actual zoom setting, CSS viewport,
device scale and screenshot dimensions. CSS transforms, a resized image and a filename containing
a width or zoom value do not establish those conditions. Apply any two-dimensional table exception
to the table, not the surrounding search, pagination or controls.

Scale follow-up checks to the change. Documentation-only edits need content and link checks.
A local control change needs its interactions and affected keyboard/responsive contexts. Shared
layout, localization or refresh changes need the affected surfaces and transitions. Reuse unchanged
evidence only with an explicit source/dependency justification. Expand a passing set only for a
new change, failure or unresolved concern; retain the separately agreed integrated acceptance set.

## Separate initialization from continuity

Define two different expected-result checks:

| Context | Setup and observation |
| --- | --- |
| Fresh document | Open a new document at the intended entry URL. Declare storage and locale setup. Observe initial loading, route initialization, selected content and focus. Test direct detail links and default entry independently where applicable. |
| Mounted document | Establish the current route, locale, filters, selection, page, scroll anchor, focus, disclosures and draft/version. Navigate or refresh without replacing the document, then compare the declared invariants. |

Hash navigation is not a fresh-document start. A newly opened page cannot prove that an in-place
refresh preserved a draft or anchor. Record the actual setup and verify action dispatch; a timed-out
click with no dispatch proof is not executed coverage or a demonstrated product failure.

## Observe transitions, not only settled screens

Exercise relevant manual and background refresh paths: initial load, pending, unchanged response,
equal-content success, semantic change at the same registry revision, changed registry data,
failure with last-good data, and recovery. Record the request outcome separately from visible
state. Capture before, pending and settled frames or a recording; a final screenshot can miss
transient shifts, stale flashes and focus changes.
Where conditional requests apply, distinguish an unchanged 304 from an equal-content 200.
Verify that initialization without usable data shows loading or failure rather than fabricated
empty counts, and that mounted refresh retains truthful last-good context until recovery.

Compare URL and view, independent UI language, search/filter/sort, selected record, valid page,
scroll anchor, keyboard focus, disclosure state and unsaved draft/version where applicable.
Declare legitimate changed-data behavior in advance: removed records, shortened pages and changed
question versions need truthful context and recovery. Do not silently choose another record,
claim a draft was saved, or discard edits to conceal a conflict. Preservation expectations are
project interaction requirements, not blanket promises about every lifecycle change.

Measure header and control bounds as pending and result labels change, including longer localized
labels. Inspect overlap, clipping and wrapping. A Cumulative Layout Shift metric can exclude
input-adjacent movement, so combine metrics with actual frames and geometry. Reserved loading
space must remain proportionate and useful. Avoid competing timing or browser-control work on
the same measurement surface.

## Inspect raw evidence and correct findings

Capture the full viewport before cropping a detail. Measure shell, navigation, header, main content,
list, detail, gutters and outer margins when layout is affected. Open the raw captures and confirm
they show the intended build, dimensions, data and state; reject stale, blank or misleading crops.
Record browser console and runtime errors and distinguish application defects from harness failures.

For each scenario retain setup, expected result, dispatched action, observed result, raw capture,
relevant geometry, console output and a truthful classification: passed, demonstrated product
failure, blocked/unknown or not executed. Similarity scores, HTTP status and code assertions can
support this record but cannot replace inspection of the rendered behavior.

An actionable finding names the user task, surface, build/state, applicable criterion, consequence,
severity, evidence and responsible owner. After correction, preserve the original failure, rebuild,
rebind served assets and data, and recheck the affected cases and dependencies. Obtain independent
review of the correction. Do not transfer a verdict from a rejected build or hide an unresolved
criterion behind an overall visual pass.

## Handoff and stop condition

Keep a durable, private handoff with:

- Scope, user tasks, surface inventory, selected resources, adaptations and rejected alternatives.
- Source, generated and served asset identities, data identity and actual browser setup.
- Scenario coverage, raw evidence, geometry, console findings and execution limits.
- Findings, dispositions, owners and correction rechecks, including preserved failed evidence.
- Independently reviewed build, director decision, unresolved limits and the next concrete action.

Close the review when the declared finite set has been completed and material failures or unknowns
have explicit dispositions consistent with the acceptance criteria. Keep implementation checks,
visual review, authorized acceptance, provider CI, integration and cleanup as separate facts.
