# Code and external reference analysis

The reference graph analyzer runs in the existing Python server. It needs no C#
compiler, parser binary, database driver, optional Unity package or runtime
download. These adapters are internal building blocks; the public graph tool and
its background reconciliation integrate them separately.

## Evidence and coverage

`parse_csharp` tokenizes source without executing it. Comments and literal
contents cannot introduce calls. It records type and method declarations,
namespace imports and aliases, class string constants, calls, and formal argument
expressions. File facts can be serialized independently of the original text.
Imports and aliases belong to their lexical namespace occurrence, including nested,
reopened and file-scoped namespaces. Constant expressions retain that context too.
Analysis version changes schedule a one-time reparse of derived source facts while
preserving authored declarations and the last evidence as stale until replacement.
External analysis identity includes that version, so unchanged JSON/CSV is also
reanalyzed when key-resolution semantics change.

The index recognizes `UnityEngine.Resources.Load`, `LoadAsync`, `LoadAll` and
`UnityEngine.AddressableAssets.Addressables.LoadAssetAsync`, `LoadAssetsAsync`,
`InstantiateAsync` and `LoadSceneAsync`. A versioned signature registry follows the
string-key overloads and wrapper propagation. Nonliteral location/AssetReference
arguments remain diagnostic evidence, not guessed catalog keys.
Visible short names and aliases are resolved conservatively. A project type with
the same name prevents assuming that a call belongs to Unity, and its owner is a
dependency even when no reference can be resolved. Value receivers and reassigned
formal arguments remain unresolved instead of becoming static calls. Single-key lookups
with multiple possible targets remain unresolved. Bulk Resources paths and
Addressables labels can produce multiple targets without truncating their fanout.
Addressables GUID keys must be present in the Addressables catalog; an arbitrary
asset GUID is not automatically an Addressables entry.
Resources keys and folder-prefix lookups are case insensitive; returned evidence
retains the original key spelling. Addressables keys and filesystem paths remain
case sensitive. Colliding Resources names stay ambiguous for a single load.

String literals, verbatim literals, single-line raw literals, constant references
and string concatenation propagate through nested wrappers, including across
files. Method summaries are built in dependency order without recursive wrapper
execution. A changed wrapper or constant invalidates its transitive dependents.
Overloads with the same visible name/arity, recursive wrappers, dynamic arguments,
unrecognized loaders and absent/ambiguous keys retain explicit diagnostics.
Qualified wrapper rules can specify `call`, zero-based `argument`, `scheme`,
`mode`, `prefix` and `suffix` for APIs whose implementation is unavailable.

This is **partial static evidence**, not Roslyn semantic analysis. The
[build-aware binder](reference-graph-compilation.md) uses existing Unity compiler
metadata for active defines and assembly visibility, and supports static imports,
named/default arguments and conservative primitive-type overload binding. It does
not resolve runtime reflection, arbitrary expressions, interpolated strings,
multiline raw-string indentation, instance type flow, inheritance, user-defined
conversions, extension methods or cross-file global using directives.
In particular, it does not certify that a script or asset has no runtime
references. Code edges use `possible` confidence even when their literal key is
unique. Compiler failures and collection errors must remain visible in the
integrating service. Nothing here relaxes the existing live asset deletion guard.

Unity YAML is scanned as tokens using the server's existing PyYAML dependency.
The scan constructs no tagged objects and expands no aliases. Comments, block
scalars and multiline quoted strings cannot introduce fields. A real mapping
field named `m_AssetGUID` produces `serialized_guid_field` evidence, including
dangling targets, with `possible` confidence: a custom class may use that field
name too. Without serialized field/type metadata it is not an observed
AssetReference. Coverage is explicitly lexical and untyped. Scanner-detected
errors (such as unterminated strings) remain collection errors with prior evidence
stale; token extraction does not certify complete YAML grammar validity. Unity's
importer remains responsible for document validity, including its special repeated
document tags and stripped-object headers.
Binary serialized files retain the collector's explicit unavailable coverage.

The loader registry follows Unity's [Resources.Load path rules](https://docs.unity3d.com/6000.0/Documentation/ScriptReference/Resources.Load.html)
and Addressables 2.7's [InstantiateAsync](https://docs.unity3d.com/Packages/com.unity.addressables@2.7/api/UnityEngine.AddressableAssets.Addressables.InstantiateAsync.html)
and [LoadSceneAsync](https://docs.unity3d.com/Packages/com.unity.addressables@2.7/api/UnityEngine.AddressableAssets.Addressables.LoadSceneAsync.html)
key overloads. Token handling uses [PyYAML's scanner API](https://pyyaml.org/wiki/PyYAMLDocumentation).

## External sources

Each registered source requires an absolute root and an explicit `include` list.
Patterns use portable forward slashes, `*`, `**` and `?`; matching is case
sensitive on every OS. Optional `exclude` patterns and field mappings are part of
that scope's versioned grant. No application-specific directory is excluded or
special-cased. A finite include prefix prunes unrelated directories before scan.

Only UTF-8 JSON and CSV are read. Hidden paths, parent escapes, absolute relative
paths, alternate data streams, links/reparse points and hard-linked files are
refused. File identity and modification state are checked around the read;
handles close before analysis. Every scan rechecks root access. The integrating
service must check the grant revision before publication and revalidate access
before returning cached external evidence. An adapter instance is not a reusable
authorization token.

JSON mappings use JSON pointer segments, with `*` matching one segment (including
an array index); CSV mappings use column names. Each mapping specifies `scheme`
(`resources`, `addressables`, `guid` or `path`), optional `prefix` / `suffix`, and
`mode` (`single` or `all`). Missing mapped fields and unmatched keys are reported.
Duplicate JSON keys and malformed CSV rows fail the scan. Without mappings, only
exact catalog strings become `possible` candidates; arbitrary numeric IDs and
similar-looking names are not interpreted as resource paths.

CSV field admission grows to the size of an already-read document instead of
retaining Python's small default field limit. The standard library setting is
process-wide and is only raised, under a lock, so concurrent graph readers cannot
lower each other's limit. It allocates no buffer and changes no source data.

## Validation and measurement

Focused tests cover nested cross-file wrappers and constants, alias/shadow
ambiguity, dynamic keys, labels, a 1,200-method wrapper chain, serialized GUIDs,
JSON/CSV mappings, path exclusions, links, access loss, file changes, Unicode,
12,000 CSV references and a 300,000-character CSV field. The focused CI matrix
runs storage and both adapters on Windows, Linux and macOS with Python 3.11/3.15.

An authorized read-only sample from the user's external development repository
parsed three explicitly selected static JSON files. One contained 2,657 string
values, including resource-path field names. No source files were modified and
no client assets were inspected. This establishes adapter compatibility, not a
verified match to an unseen game's asset catalog. Project names and field names
are not built into the adapter.

Reproduce the synthetic large workload from the repository root:

```sh
python tools/experiments/reference_graph_analysis.py --scripts 50000 --assets 250000
```

One local Windows/Python 3.14.6 run used 50,000 short caller scripts, a shared
wrapper, 250,000 resource assets and 50,000 CSV rows. Catalog creation took 0.797s,
source parsing 2.692s, summary indexing 1.007s, resolution 0.511s, wrapper-change
fanout 0.041s and CSV resolution 0.243s. Every generated edge and all 50,001
affected owners were checked. These are one-run synthetic component timings,
not end-to-end Unity import or arbitrary real-project throughput guarantees.
Source mutation, transport and durable publication are measured by integration
checks separately. No total project size or task deadline derives from this
benchmark.
