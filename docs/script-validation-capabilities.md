# Script validation capabilities

The Advanced tab shows the requested validation level and the engine compiled into
the installed Unity MCP package. The Editor and the per-file validation report use
the same Roslyn availability flag. Changing or viewing this setting does not scan
scripts, resolve packages, refresh assets, or request compilation.

| Level | Checks available |
| --- | --- |
| Basic | Lexical structure: balanced delimiters and duplicate method signatures |
| Standard | Structure and Unity lexical rules; compiler syntax only with Roslyn |
| Comprehensive | Standard plus additional quality heuristics |
| Strict | Comprehensive with warnings treated as failures; Roslyn semantic compilation when enabled |

Reference and cross-file partial-type coverage at Strict depends on the owning
assembly context of the actual script. The per-file validation report describes
what was actually checked, its effective level and limitations. An available engine
is not proof that every check ran, or that a script is correct.

Without Roslyn, even Strict uses heuristics. Basic always uses structural checks.
No level runs Unity project compilation. The settings show a compilation snapshot
when attached or updated; idle does not mean the last compilation succeeded.
**Open Compilation Diagnostics** opens the existing Unity Console only. It does
not clear diagnostics, refresh assets, or request compilation. Use the existing
explicit compilation workflow when fresh native compiler results are required.

Roslyn is optional. Its installed assemblies and `USE_ROSLYN` compile configuration
must agree; this view does not install packages or change compiler defines.
