# Editor activity

The Activity tab shows detached observations of this Editor's existing test,
build/batch and Play scenario owners. Opening, hiding or closing it never starts,
resumes or retries an operation. Generation and package workflows retain their
existing dedicated views and status tools.

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
