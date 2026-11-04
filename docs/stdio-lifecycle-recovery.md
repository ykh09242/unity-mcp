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

After preparing an isolated project with `tools/unity_editor_transport_qa.py`, run
`--group stdio-lifecycle --group stdio-order-wakeup` for ordinary lifecycle, FIFO
and wakeup coverage. Run `--group stdio-explicit-stop-reload` separately because it
replaces the domain. The runner's required-method gate refuses skipped native-tick
coverage when the Editor does not expose the native wakeup facility; ordinary
fallback tests remain separately available on those Editors. See
[native QA groups](native-qa-groups.md) for preparation, ownership and evidence rules.
