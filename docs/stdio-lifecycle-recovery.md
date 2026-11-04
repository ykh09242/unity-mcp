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

Native regression evidence distinguishes actual domain replacement from invoking
a lifecycle callback inside the same domain. The explicit reload regression runs
only when selected in an isolated batch Editor. Its production scheduling gate is
invoked after actual replacement because automatic network startup is disabled in
the owned test runner.
