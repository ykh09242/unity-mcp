# Window action lifetime regressions

This dependency-free .NET 9 host compiles the exact `OnOpenFileClicked` method and the Git URL value-change/Clear callback statements extracted from repository source. It does not maintain a copied implementation. `--baseline` extracts the same statements from a read-only Git revision; the runner prints LF-normalized source SHA-256 values for provenance. Extraction scaffolding exposes the callbacks without constructing the rest of either window.

The Process/File/EditorPrefs/UI boundaries in `Boundary.cs` are explicit test facades. They never read client configuration or real preferences, and the fake Process retains wrappers so assertions measure prompt disposal rather than finalizer timing. TextField models changed-value notification for an attached panel and suppresses notification for detached fields or `SetValueWithoutNotify`. Null is a synthetic boundary case; this does not prove native Unity TextField null normalization. The resolver facade is identity-only; server-path resolution is outside the exercised change.

A separate real `System.Diagnostics.Process` case starts this host as a hidden child, closes the returned wrapper, verifies its SafeHandle is closed while an independent observer still sees a live child, then asks the child to exit naturally using a task-owned signal file. It never calls Kill/CloseMainWindow. The child has a ten-second self-exit deadline. This proves actual .NET 9 wrapper ownership, not Unity Mono process behavior or OS shell file associations.

From the repository root in PowerShell:

```powershell
& 'Server/.venv/Scripts/python.exe' tools/tests/fixtures/window_action_lifetimes/run.py --group process --output .tmp/CS-20261009-memory-query-resources/services-window-actions/process
& 'Server/.venv/Scripts/python.exe' tools/tests/fixtures/window_action_lifetimes/run.py --group clear --output .tmp/CS-20261009-memory-query-resources/services-window-actions/clear
& 'Server/.venv/Scripts/python.exe' tools/tests/fixtures/window_action_lifetimes/run.py --output .tmp/CS-20261009-memory-query-resources/services-window-actions/all
& 'Server/.venv/Scripts/python.exe' tools/tests/fixtures/window_action_lifetimes/run.py --baseline 43ee728d0691254920e2d8e3c43dcf657b294437 --output .tmp/CS-20261009-memory-query-resources/services-window-actions/red
```

`--group process` checks only the Process fix (five cases), so it can pass independently before the Clear fix is committed. `--group clear` checks only Clear/value-edit notification behavior (eight cases). Default `all` runs all thirteen. Baseline is expected to fail five cases: successful returned wrappers remain undisposed and three synthetic changed values plus the repeated-click count show duplicate notifications. Green expects thirteen passing cases. Generated source, project, binary and obj files stay under the validated task `.tmp` directory; no NuGet packages or copied assemblies are used. Installed .NET SDK 10.0.401 ran the net9.0 host in the recorded audit.

Actual counts: 1,000 returned wrappers had 1,000 undisposed before / zero after. First Clear with a nonempty value emitted each downstream event twice before / once after; 1,000 clicks emitted each downstream event 1,001 times before / 1,000 after. Initially empty and repeated-empty clicks still notify once each; detached fields, ordinary typed changes, null shell return, missing file, launch failure and callback exception propagation remain covered. Each run creates fresh synthetic controls and preferences; no serialized/window-reload state is changed by these two stateless fixes.

Native Unity panel execution, editor window close/reopen and actual shell file opening were not run. The parent audit separately owns Unity-reference compilation. API references were checked through authorized official-source fallback after the previously disclosed Context7 quota limit:

- [Microsoft Process.Close/Dispose ownership](https://learn.microsoft.com/en-us/dotnet/api/system.diagnostics.process.close?view=net-9.0)
- [Unity 6 BaseField.SetValueWithoutNotify](https://docs.unity3d.com/6000.0/Documentation/ScriptReference/UIElements.BaseField_1.SetValueWithoutNotify.html)
