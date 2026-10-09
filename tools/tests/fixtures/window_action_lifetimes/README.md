# Window action lifetime regressions

This dependency-free .NET 9 host compiles exact repository bodies for `OnOpenFileClicked`, `OnBrowseGitUrlClicked`, `ResolveServerPath` and the Git URL value-change/Clear callback statements. It does not maintain copied algorithms. `--baseline` extracts those bodies from a read-only Git revision; LF-normalized source SHA-256 values are printed for provenance. Extraction scaffolding exposes these actions without constructing the rest of either window.

The Process/File/EditorPrefs/UI boundaries in `Boundary.cs` are explicit test facades. They never read client configuration or real preferences. The fake Process retains wrappers so ownership assertions measure prompt disposal rather than finalizer timing. TextField models changed-value notification for an attached panel and suppresses notification for detached fields or `SetValueWithoutNotify`. Null is a synthetic boundary case; native Unity TextField null normalization is outside this test. The actual `ResolveServerPath` body runs against synthetic `File.Exists` answers: tests cover a valid selected folder and a checkout whose `Server` subfolder contains pyproject.toml. Recorded queries and ordered traces demonstrate duplicate work directly. Failures injected into file query/persistence/subscribers check propagation and mutation ordering; they do not claim these exact exceptions occur in Unity.

A separate real `System.Diagnostics.Process` case starts this host as a hidden child, closes the returned wrapper, verifies its SafeHandle is closed while an independent observer sees a live child, then asks it to exit naturally via a task-owned signal file. It never calls Kill/CloseMainWindow. The child has a ten-second self-exit deadline. This proves actual .NET 9 wrapper ownership, not Unity Mono behavior or OS shell file associations.

From the repository root in PowerShell:

```powershell
& 'Server/.venv/Scripts/python.exe' tools/tests/fixtures/window_action_lifetimes/run.py --group process --output .tmp/CS-20261009-memory-query-remaining/services-window-actions/process
& 'Server/.venv/Scripts/python.exe' tools/tests/fixtures/window_action_lifetimes/run.py --group clear --output .tmp/CS-20261009-memory-query-remaining/services-window-actions/clear
& 'Server/.venv/Scripts/python.exe' tools/tests/fixtures/window_action_lifetimes/run.py --group browse --output .tmp/CS-20261009-memory-query-remaining/services-browse/green
& 'Server/.venv/Scripts/python.exe' tools/tests/fixtures/window_action_lifetimes/run.py --output .tmp/CS-20261009-memory-query-remaining/services-browse/all
& 'Server/.venv/Scripts/python.exe' tools/tests/fixtures/window_action_lifetimes/run.py --group browse --baseline c2cdd2b29f6c068170167f591e6db407b1a08d23 --output .tmp/CS-20261009-memory-query-remaining/services-browse/red
```

`process` checks five cases, `clear` eight, `browse` eight. Default `all` runs twenty-one and asserts the executed case count. Each group can pass independently of unfixed actions in other groups. Browse baseline above expects four passing / four failing cases; after the fix it expects eight passing / zero failing. Earlier Process/Clear red evidence remains in reports/CS-20261009-memory-query-resources. Generated source/project/binaries/obj stay under validated task .tmp directories (remaining and earlier resources task); no packages or copied assemblies are used. Installed .NET SDK 10.0.401 ran the net9.0 host.

Browse evidence: changed valid selection had File.Exists/prefs writes/Git notifications/HTTP notifications = 2/2/2/2 before, 1/1/1/1 after. Auto-correcting the parent had 3/2/2/2 before, 2/1/1/1 after. Selecting the same value still writes and notifies once; empty/null picker cancellation does no work. Ordered traces check query -> persistence -> Git -> HTTP, with automatic correction logging before persistence and final action logging afterward. Both callbacks observe the final corrected field and preference. Resolver failure precedes mutation; persistence failure happens after field update and before notifications; subscriber failure stops later notification. Fresh controls are created per scenario, preventing an earlier registration from masking duplicate work.

Earlier evidence remains covered: 1,000 returned wrappers had 1,000 undisposed before / zero after; 1,000 Clear clicks emitted each downstream event 1,001 times before / 1,000 after. Empty/repeated-empty and detached Clear, ordinary typed changes, null shell return, missing config and launch failure still have focused cases.

Native Unity panel execution, editor window close/reopen and actual shell file opening were not run. Parent audit separately owns Unity-reference compilation. API references use the authorized official-source fallback after the previously disclosed Context7 quota limit:

- [Microsoft Process.Close/Dispose ownership](https://learn.microsoft.com/en-us/dotnet/api/system.diagnostics.process.close?view=net-9.0)
- [Unity 6 BaseField.SetValueWithoutNotify](https://docs.unity3d.com/6000.0/Documentation/ScriptReference/UIElements.BaseField_1.SetValueWithoutNotify.html)
