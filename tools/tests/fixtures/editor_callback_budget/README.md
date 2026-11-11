# Editor callback budget regression

Run `pwsh -NoProfile -File tools/tests/fixtures/editor_callback_budget/RunRegression.ps1`
from the repository root. `-Baseline <commit>` runs the same scenarios against an
earlier scheduler. The installed .NET 11 SDK builds the actual
`EditorMainThreadWakeup.cs` with controlled Editor update and native tick boundaries.
No package installation or Editor launch is required. Output stays under
`.tmp/t3-editor-callback-budget`.

The cases check count and elapsed-time budgets, FIFO across slices, native wake-up
of remaining work, cancellation, throwing callbacks, shutdown and reentrant drains.
They provide managed scheduler evidence; native tick delivery is covered by
`EditorMainThreadWakeupTests` in the Unity test project.
