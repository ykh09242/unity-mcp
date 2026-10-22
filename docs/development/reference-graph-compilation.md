# Build-aware reference analysis

The reference collector uses Unity's existing Editor compilation metadata. There
is no additional compiler, driver, service or first-run download. The active build
target and `AssembliesType.Editor` identify the context: this is not a promise
that a Player build has the same references or defines.

## Collection and freshness

`CompilationPipeline.GetAssemblies(AssembliesType.Editor)` supplies assembly names,
source membership, defines and references. A deterministic revision covers that
metadata and the active target. The collector shares one source-to-assembly map,
invalidates it on compilation or target changes, rebuilds after domain reload,
and releases the map and callbacks after ten minutes without use. It does not
rebuild the map for every source page or unrelated asset import.

C# source responses carry context on the first page only; the continuation
fingerprint includes its revision. The server also checks context against the
status used to begin collection. A changed global context checks source bindings
in pages of 256 metadata records and invalidates only affected C# facts, even
when source bytes and the import journal have not changed. Adding a script to an
unchanged assembly does not reread all existing scripts. Signature columns keep
this pass from decoding stored source facts. A final status
check prevents that race from claiming the graph is current. Unchanged idle
passes preserve facts and avoid source reads.

Within the Python analysis index, equivalent assembly metadata and visibility
sets are shared. Entries are removed when their last source owner is removed;
there is no process-global intern pool retaining old project contexts.

Missing native context retains lexical fallback with explicit conditional
coverage diagnostics. An unavailable compiler metadata API reports
`build_context_unavailable`; a source excluded from the current compilation
reports `inactive_build_context` and emits no code edges. Import/compilation
activity and compiler failure remain visible waiting states. Existing authored
edges, rules and external grants are unaffected by derived-context invalidation.

## Supported binding and limits

- Conditional symbols: `#if`, `#elif`, `#else`, `#endif`, file-local `#define` and
  `#undef`, boolean parentheses, `!`, `&&`, `||`, `==` and `!=`. Masked branches keep
  original line numbers. Directives inside literals/comments are not evaluated.
- Assembly visibility for indexed project types, constants and wrapper methods.
  Two unrelated assemblies cannot introduce a false ambiguity merely by using
  the same qualified type name.
- Scoped aliases, relative namespace aliases, explicit `global::`, nearer project
  types and `using static`. Namespace occurrences retain independent imports.
- Named/default wrapper arguments, declaration-scope constant defaults, and
  unambiguous primitive `string`, `int`, `bool` and `char` overloads. Known native
  loader key argument names can be supplied out of order.

Unknown arguments, invalid bindings and multiple viable overloads remain explicit
diagnostics. The analyzer is not a C# compiler: accessibility, generic inference,
inheritance, implicit conversions, extension methods, arbitrary object flow and
cross-file global using directives are incomplete. Global using declarations
produce `global_using_not_propagated`. Possible edges are never safe-delete proof.

## Verification and measurement

Tests cover active/inactive branches, literal directives, malformed expressions,
same-name types in separate assemblies, context-only updates, wrapper defaults,
named arguments, static imports, aliases and continuation races. Real Unity tests
compare compiler metadata, repeated cache reads, deterministic rebuilding, idle
release and domain reload in the existing isolated project with its warm Library.
The server graph CI matrix includes the semantic fixtures on Windows, Linux and
macOS; native execution was performed on Windows only.

Use the existing server environment to measure 50,000 synthetic caller scripts:

```sh
python tools/experiments/reference_graph_compilation.py --scripts 50000
python tools/experiments/reference_graph_compilation.py --scripts 50000 --context
```

Both modes verify every edge and emit a digest. The second mode supplies compiler
context to the same binder; it measures incremental context cost, not a claim
about replacing a full compiler. Single-file edits must affect just that caller.
Python traced bytes cover that edit only, not total process or Unity memory.
The native allocation counter is reported as unavailable when calibration fails;
a zero reading on such a runtime is not evidence of zero allocation.

Three alternating fresh-process samples on Windows/Python 3.14.6 used 50,000
callers plus one wrapper. The context case supplied 100 referenced assemblies
and 25 defines. Median parse/index/resolve times were 4.111s/1.790s/0.767s with
context and 3.186s/1.417s/0.810s without it. All 50,000 edges had the same digest.
The richer cold analysis costs more; a one-file parse/update/query still affected
one caller, at 3.806ms with 8,484 traced peak bytes (3.791ms/7,436 bytes without
context). This benchmark excludes native transport and database publication.

In the warm Unity 6000.0.69f1 isolated project, 1,000 cached compiler status calls
took 7.99ms in a calibrated sample; a deliberate metadata rebuild took 455.68ms.
Those calls include reflection in the test harness. The runtime's allocation
counter failed calibration, so no native allocation reduction is claimed.
Rebuilding occurs at compiler/target invalidation or after idle eviction, not on
every poll. Actual domain reload preserved the deterministic context revision.

## Contracts

The native API is available in the supported Unity 2021.3 baseline:
[GetAssemblies](https://docs.unity3d.com/2021.3/Documentation/ScriptReference/Compilation.CompilationPipeline.GetAssemblies.html)
and [Assembly metadata](https://docs.unity3d.com/2021.3/Documentation/ScriptReference/Compilation.Assembly.html).
The conservative parser follows C# [preprocessor directives](https://learn.microsoft.com/en-us/dotnet/csharp/language-reference/preprocessor-directives)
and [using directives](https://learn.microsoft.com/en-us/dotnet/csharp/language-reference/keywords/using-directive).
