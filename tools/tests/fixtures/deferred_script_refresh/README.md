# Deferred script refresh regression

Run `pwsh -NoProfile -File tools/tests/fixtures/deferred_script_refresh/RunRegression.ps1`
from the repository root. `-Baseline <commit>` checks the same scenarios against an
earlier source revision. The fixture uses the installed .NET 11 SDK and the existing
`.compile-refs/Newtonsoft.Json.dll`; it does not install packages or launch Unity.

The harness extracts the complete refresh helper classes from `ManageScript.cs` and
compiles the actual native admission barriers and compilation reservation. Unity
import, compilation, consent and lifecycle events are controlled boundaries. It
checks pending admission, foreign owners, generation cancellation, Play suspension,
reload cleanup, exact completion ownership and observable uncertain-trigger recovery.
Build output stays under `.tmp/t3-deferred-script-refresh`.

These are managed lifecycle regressions, not evidence of native compilation or an
actual domain reload. Run the corresponding Editor tests for Unity integration.
