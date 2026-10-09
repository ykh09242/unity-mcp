# Key-store process lifetime regression

Run from the repository root with the existing .NET 9 SDK/reference pack:

```powershell
& tools/tests/fixtures/key_store_process/RunRegression.ps1
```

`-OutputRoot` optionally selects the build/probe directory. The default is
`.tmp/key-store-process-regression`; the runner does not delete output or install
optional dependencies. Both harnesses compile the production sources directly.
No fixture calls an operating-system credential service or uses actual keys.

- `LifecycleHarness` runs the complete Linux/macOS key-store sources against an
  instrumented process boundary. It checks 100 timeout repetitions for each of
  seven invocation paths, exposed-stream disposal, pending IO cleanup, start /
  stream setup / read / write / disposal failures, and existing value, stdin,
  structured-argument and nonzero-exit behavior.
- `ProcessHarness` runs the production helper against real child processes of
  the fixture executable. It checks simultaneous output draining, 256 KiB full
  pipes, a 4 MiB blocked stdin write, timeout termination, failed startup and
  handle growth over 50 completed commands without forcing collection. Hung
  fixtures exit after 20 seconds as a fallback; the parent also cleans a failed
  test's owned child. Synthetic inputs and PID probes are task-local.

These verify managed ownership and real process/pipe behavior on the host OS.
They do not run the Unity Editor, macOS Keychain or Linux libsecret. Termination
can still be denied by the OS; cleanup in that case remains best effort.

For a historical source comparison, `LifecycleHarness.csproj` accepts
`--property:KeyStoreSourceRoot=<directory>` containing the two platform source
files and, when present, `KeyStoreProcess.cs`. Baseline versions without the
helper are supported. Keep such copies in a task-local temporary directory.
