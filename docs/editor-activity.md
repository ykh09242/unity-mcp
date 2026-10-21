# Editor activity

The Activity tab shows detached observations of this Editor's existing test,
build/batch, Play scenario, package and asset-generation owners. Opening, hiding or closing it never starts,
resumes or retries an operation. Generation and package workflows also retain their dedicated views and status tools.

Visible rows update when owners change their revision. A one-second visible
scheduler checks that scalar revision and updates elapsed labels; unchanged rows
do not reread artifacts or rebuild controls. Hiding the tab pauses the scheduler.
No scene scan, package refresh, native report or test-result graph is retained in
rows. Evidence/status is loaded only after the user presses its button.

Cancellation actions resolve the original owner and job ID again. The displayed
request is separate from confirmed termination. Cancelling a build batch allows
the active synchronous BuildPlayer call to finish and stops later children.
Single native builds cannot be cancelled here. Client observer timeout is not a
native failure; native test deadline and control observations remain separate.

Each owner keeps its original retention/reload behavior. Build rows are domain
local, while retained reports remain readable through `report_history`. A missing
row is not evidence that work stopped. The view does not keep its own job history.

Test cancellation is enabled only while the job holds a supported native control owner. A refused or expired request is shown as an observation in the evidence window; it is not reported as confirmed cancellation.


Package rows preserve `unknown` completion after lost requests or unobservable
resolve completion. They remain unresolved observations, with no cancellation or
automatic retry action. Opening evidence reads the retained job; it does not issue
a package request or reconcile by installing anything. Source package URLs are
not repeated in row labels or copyable evidence. Evidence preserves job/recovery/resolve
identifiers and reports whether a native error exists, while omitting raw error text
that can contain credentials. A genuinely observed native failure remains failed.

Generation rows include existing provider/recovery and import phases, plus byte
progress when the owner has it. Provider progress is not presented as an overall
completion percentage. Local cancellation never proves remote provider completion;
an active local import remains visible until its owner releases it. A recovery
record with an unknown provider outcome is displayed as unknown. Evidence reads
the existing receipt and result identifiers without starting or resuming work.
Generation jobs do not currently retain a wall-clock start/end pair, so the common
elapsed label remains explicitly unavailable; phase duration is available in the
owner's evidence instead of inventing timestamps.

Owner persistence, lifecycle and changed download observations invalidate the
shared scalar revision. Unchanged progress and elapsed time do not force a new
capture every Editor update. The view adds no owner subscriptions or background
tasks. All active jobs and each owner's existing terminal retention remain in
scope; expired evidence explains that the job is no longer retained.
