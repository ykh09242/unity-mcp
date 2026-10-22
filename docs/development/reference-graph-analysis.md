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

The index recognizes `UnityEngine.Resources.Load`, `LoadAsync`, `LoadAll` and
`UnityEngine.AddressableAssets.Addressables.LoadAssetAsync` / `LoadAssetsAsync`.
Visible short names and aliases are resolved conservatively. A project type with
the same name prevents assuming that a call belongs to Unity, and its owner is a
dependency even when no reference can be resolved. Value receivers and reassigned
formal arguments remain unresolved instead of becoming static calls. Single-key lookups
with multiple possible targets remain unresolved. Bulk Resources paths and
Addressables labels can produce multiple targets without truncating their fanout.
Addressables GUID keys must be present in the Addressables catalog; an arbitrary
asset GUID is not automatically an Addressables entry.

String literals, verbatim literals, single-line raw literals, constant references
and string concatenation propagate through nested wrappers, including across
files. Method summaries are built in dependency order without recursive wrapper
execution. A changed wrapper or constant invalidates its transitive dependents.
Overloads with the same visible name/arity, recursive wrappers, dynamic arguments,
unrecognized loaders and absent/ambiguous keys retain explicit diagnostics.
Qualified wrapper rules can specify `call`, zero-based `argument`, `scheme`,
`mode`, `prefix` and `suffix` for APIs whose implementation is unavailable.

This is **partial static evidence**, not Roslyn semantic analysis. It does not
evaluate conditional compilation, runtime reflection, arbitrary expressions,
interpolated strings, multiline raw-string indentation, instance type flow,
inheritance, default/named argument binding or assembly reference resolution.
In particular, it does not certify that a script or asset has no runtime
references. Code edges use `possible` confidence even when their literal key is
unique. Compiler failures and collection errors must remain visible in the
integrating service. Nothing here relaxes the existing live asset deletion guard.

Unity YAML `m_AssetGUID` fields provide separate serialized AssetReference
evidence, including dangling target GUIDs. Binary serialized files need the
collector's explicit unavailable-coverage result; an empty text scan is not a
substitute for that result.

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
