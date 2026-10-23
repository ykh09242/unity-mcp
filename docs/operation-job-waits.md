# Observing long-running operation jobs

`wait_for_operation(kind, job_id, wait_timeout=30)` observes an existing build,
package mutation/resolve, or single scenario job. Start the job using its domain
tool, retain its ID, and then request an observation. `wait_timeout=0` makes one
status query; positive values wait up to 60 seconds. This bounds the observation,
not the native operation. Large builds, downloads and scenarios retain their
original budgets and can outlive any number of explicitly requested waits.

The result preserves the domain's job evidence under `data.job` and adds a shared
`phase`: `waiting`, `running`, `finalizing`, `terminal`, or `unknown`. A failed or
cancelled native job is still a successful status observation. `terminal` only
means the owner has published its final outcome. Scenario evidence finalization
remains nonterminal. Package resolve completion may be unobservable; `unknown`
returns promptly and never becomes guessed success. Query-only package list/search
jobs and scenario suites are outside this initial adapter.

If a domain reload loses an add, embed, or remove request, its status becomes
`unknown` until registered package metadata establishes the requested state.
Elapsed time does not turn missing evidence into a native failure. Later status
queries can reconcile this state without starting another package operation.
The optional `recovery` evidence distinguishes `request_lost_after_reload`,
`registration_unavailable`, and `desired_state_observed`; native completion is
still unobserved and `retry_safe` remains false. A failure reported by the original
native request is retained. Recovery metadata uses the same bounded session
history as other package jobs.

`wait_status=expired` retains the last observed job state. Expiry, cancellation,
client disconnection and a lost status reply never start, retry, resume or cancel
native work. Polling sends only the owner's `status` action and performs no
refresh or compile. Each wait captures one selected project, authenticated owner
and connection generation. Reload or replacement ends that observation; explicitly
query the original owner again after reconnecting. For stdio, the original status
tool can establish the authenticated connection before a fresh wait. Retained
build observations after reload continue to report the owner's interrupted state.
Unity's synchronous build phase can block main-thread status responses; this
adapter does not invent progress while Unity cannot answer.

When the existing bounded-wait Tasks feature is enabled, authenticated modern HTTP
clients advertising `io.modelcontextprotocol/tasks` can receive a durable handle
for this same bounded wait. The existing journal/admission limits and SDK gate are
unchanged. `tasks/get` returns its ordinary tool result; Task completion does not
mean the native job completed. `tasks/cancel` cancels only the observer. Legacy or
non-Tasks clients use the same ordinary tool. A full journal falls back before
creating a handle; an unsupported store/SDK leaves Tasks unadvertised.

A persisted handle captures its kind, job, project and generation. Reopening the
server preserves completed results until expiry and marks interrupted observers
failed; it never resumes or redispatches them. Foreign-owner and expired handles
remain unavailable. Native operation permission, tool enablement and lifecycle
checks remain with their existing owners.

See [private store initialization and recovery](private-store-recovery.md) for
interrupted first startup, stable installation identities and refusal of ambiguous state.
