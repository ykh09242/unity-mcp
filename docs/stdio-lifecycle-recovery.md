# Stdio lifecycle and recovery

End Session retires the current listener and deferred startup. Its explicit stop
intent belongs to the current Editor process: it survives assembly reload and Play
Mode domain replacement through SessionState, and ends when that Editor exits.
Explicit Start clears the stop intent. Reload teardown uses a separate internal
stop path, so a running session remains eligible for the existing reload recovery.
Neither path changes connection settings or transfers intent to another Editor.

Stop closes admission immediately. An already executing command retains its input
reservation and execution ownership until the handler and its cancellation callbacks
actually settle. A replacement listener may start during that drain. A canceled
response is therefore not proof that execution ownership has already ended.

If a stopped session unexpectedly appears unavailable after reload, use Start to
resume it. Repeated automatic startup attempts are intentionally suppressed after
End Session; changing transport settings is unnecessary.

Legacy requests retain admission order until the running owner actually settles.
The queue keeps explicit FIFO order separately from request lookup, so a newly
arriving command cannot take the position of a completed or canceled request.
Negotiated native arbitration still permits its existing conflict/control behavior.

Stdio ingress and settlement signal the shared Editor main-thread wakeup. The native
tick can admit queued work even when the bridge's regular update callback does not
run. Update remains the fallback when that native facility is unavailable. Stop
detaches both bridge pump callbacks; handler and cancellation settlement continue
to own their existing leases independently of those callbacks.

Native regression evidence distinguishes actual domain replacement from invoking
a lifecycle callback inside the same domain. The explicit reload regression runs
only when selected in an isolated batch Editor. Its production scheduling gate is
invoked after actual replacement because automatic network startup is disabled in
the owned test runner.

Server connection selection shares one physical owner for each selector after
trimming surrounding whitespace, matching authoritative resolution. The physical
getter keeps the first caller's original argument. Different names, hashes and ID
spellings retain their own authoritative resolution and ambiguity checks. Each waiter keeps its
original command deadline and can cancel independently;
the physical owner is abandoned only after its last waiter leaves. At most four
selection workers are submitted per pool and loop, and at most 64 distinct selection
owners are retained. Queued admission is canceled before executor submission. Running
or already-submitted work remains owned until actual completion, including pool
retirement. Alias resolution and discovery remain in the bounded worker path.
When a pool is shared by live event loops, retirement requests cancellation on
each owner's loop and drains a thread-safe completion proxy before disposal.
Concurrent close callers share the same cleanup owner across those loops.

An ordinary command response retains its socket timeout policy after reconnect,
capped by the original command's remaining deadline. Explicit request timeouts and
Blender response budgets retain their existing handling. Connection retries honor
`config.max_retries`, including zero; `send_command(max_attempts=...)` overrides that
retry count. Backoff uses `config.retry_delay * 2**attempt` with a random multiplier
from 0.1 to 0.3, capped at 0.25 seconds for transient socket failures, 5 seconds while
reloading, or 3 seconds otherwise, and by the remaining command budget. A lost reply
after dispatch retains the existing continuation or `outcome_unknown` behavior.

After preparing an isolated project with `tools/unity_editor_transport_qa.py`, run
`--group stdio-lifecycle --group stdio-order-wakeup` for ordinary lifecycle, FIFO
and wakeup coverage. Run `--group stdio-explicit-stop-reload` separately because it
replaces the domain. The runner's required-method gate refuses skipped native-tick
coverage when the Editor does not expose the native wakeup facility; ordinary
fallback tests remain separately available on those Editors. See
[native QA groups](native-qa-groups.md) for preparation, ownership and evidence rules.
