# Window action lifetime regressions

This dependency-free .NET 11 host compiles exact repository bodies for `OnOpenFileClicked`, `OnBrowseGitUrlClicked`, `ResolveServerPath`, `ResolveLocalServerPath`, `RegisterServerSourceCallbacks` and `CommitServerSource`. It does not maintain copied action algorithms. `--baseline` extracts historical bodies from a read-only Git revision and retains their original automatic-apply scenario branch. LF-normalized source SHA-256 values are printed for provenance. Extraction scaffolding exposes these actions without constructing the rest of either window.

The Process/File/EditorPrefs/UI boundaries in `Boundary.cs` are explicit test facades. They never read client configuration or real preferences. The fake Process retains wrappers so ownership assertions measure prompt disposal rather than finalizer timing. TextField models changed-value notification for an attached panel and suppresses notification for detached fields or `SetValueWithoutNotify`. Null is a synthetic boundary case; native Unity TextField null normalization is outside this test. The actual path helpers run against synthetic `File.Exists` answers. Current scenarios cover inert drafts, Apply/Enter/focus-out commits, repeated and detached Clear, picker cancellation, same selection and parent correction, exactly one notification pair per successful commit, retained drafts after failed persistence, and caught subscriber failures that stop later notifications. Recorded queries and ordered traces check mutation ordering. The source-setting writer, status rendering and preview cancellation are explicit boundaries; real preferences and configuration previews are not exercised.

A separate real `System.Diagnostics.Process` case starts this host as a hidden child, closes the returned wrapper, verifies its SafeHandle is closed while an independent observer sees a live child, then asks it to exit naturally via a task-owned signal file. It never calls Kill/CloseMainWindow. The child has a ten-second self-exit deadline. This proves actual .NET 11 wrapper ownership, not Unity Mono behavior or OS shell file associations.

From the repository root in PowerShell:

```powershell
& 'Server/.venv/Scripts/python.exe' tools/tests/fixtures/window_action_lifetimes/run.py --group process --output .tmp/CS-20261009-memory-query-remaining/services-window-actions/process
& 'Server/.venv/Scripts/python.exe' tools/tests/fixtures/window_action_lifetimes/run.py --group clear --output .tmp/CS-20261009-memory-query-remaining/services-window-actions/clear
& 'Server/.venv/Scripts/python.exe' tools/tests/fixtures/window_action_lifetimes/run.py --group browse --output .tmp/CS-20261009-memory-query-remaining/services-browse/green
& 'Server/.venv/Scripts/python.exe' tools/tests/fixtures/window_action_lifetimes/run.py --output .tmp/CS-20261009-memory-query-remaining/services-browse/all
& 'Server/.venv/Scripts/python.exe' tools/tests/fixtures/window_action_lifetimes/run.py --group browse --baseline c2cdd2b29f6c068170167f591e6db407b1a08d23 --output .tmp/CS-20261009-memory-query-remaining/services-browse/red
```

Current-source execution checks five `process`, eleven `clear` (including draft/commit triggers), and eight `browse` cases: twenty-four in total, with an asserted executed count. The 2026-11-11 .NET 11 run passed all twenty-four; the preserved historical process branch passed five cases.

## Historical automatic-apply evidence

`process` checks five cases, `clear` eight, `browse` eight. Default `all` runs twenty-one and asserts the executed case count. Each group can pass independently of unfixed actions in other groups. Browse baseline above expects four passing / four failing cases; after the fix it expects eight passing / zero failing. Earlier Process/Clear red evidence remains in reports/CS-20261009-memory-query-resources. Generated source/project/binaries/obj stay under validated task .tmp directories (remaining and earlier resources task); no packages or copied assemblies are used. Installed .NET SDK 10.0.401 ran the net9.0 host.

Browse evidence: changed valid selection had File.Exists/prefs writes/Git notifications/HTTP notifications = 2/2/2/2 before, 1/1/1/1 after. Auto-correcting the parent had 3/2/2/2 before, 2/1/1/1 after. Selecting the same value still writes and notifies once; empty/null picker cancellation does no work. Ordered traces check query -> persistence -> Git -> HTTP, with automatic correction logging before persistence and final action logging afterward. Both callbacks observe the final corrected field and preference. Resolver failure precedes mutation; persistence failure happens after field update and before notifications; subscriber failure stops later notification. Fresh controls are created per scenario, preventing an earlier registration from masking duplicate work.

Earlier evidence remains covered: 1,000 returned wrappers had 1,000 undisposed before / zero after; 1,000 Clear clicks emitted each downstream event 1,001 times before / 1,000 after. Empty/repeated-empty and detached Clear, ordinary typed changes, null shell return, missing config and launch failure still have focused cases.

Native Unity panel execution, editor window close/reopen and actual shell file opening were not run. Parent audit separately owns Unity-reference compilation. API references use the authorized official-source fallback after the previously disclosed Context7 quota limit:

- [Microsoft Process.Close/Dispose ownership](https://learn.microsoft.com/en-us/dotnet/api/system.diagnostics.process.close?view=net-9.0)
- [Unity 6 BaseField.SetValueWithoutNotify](https://docs.unity3d.com/6000.0/Documentation/ScriptReference/UIElements.BaseField_1.SetValueWithoutNotify.html)
