# Reference graph exclusions

The reference graph applies `.gitignore` patterns by default. Its **Collection
policy** also accepts additional graph-only exclusion patterns. These settings
belong to the authenticated user, Unity instance and project, just like the rest
of the graph configuration.

## Configure the policy

In **Unity MCP > References > Rules & source configuration**, select
**Collection policy**. Keep **Respect .gitignore** enabled to apply the project's
rules, and enter additional exclusions one per line. Read the current policy
before saving; the existing version check prevents overwriting another edit.
The advanced configuration remains available for configuration transfer.

The equivalent MCP actions first read the current policy and then update it with
that returned version:

```json
{"action":"policy"}
{"action":"set_policy","options":{"expected_version":0,"config":{"respect_gitignore":true,"exclude":["Assets/Generated/*","Assets/Temporary/*"]}}}
```

The example version `0` is only for a policy that has not been saved before. Use
the actual returned version for an existing policy. Omitting `respect_gitignore`
keeps its default of `true`; an explicit `false` disables Gitignore matching for
that scope. Additional `exclude` patterns retain their existing glob semantics
and are additive: they do not re-include paths excluded by `.gitignore`.

## Rule semantics and boundaries

Root and nested `.gitignore` files use Gitignore pattern syntax, including
comments, escaped special characters, directory patterns, `**`, and `!`
negation. Nested rules are relative to their containing directory. A child
cannot be re-included while its parent directory remains excluded. See the
[official Gitignore documentation](https://git-scm.com/docs/gitignore).

The graph applies these patterns to discovered paths regardless of whether Git
tracks them. It does not inspect the Git index, `.git/info/exclude` or a user's
global Git configuration. Logical paths use `/` separators and case-sensitive
pattern matching consistently across platforms. Git and optional native pattern-matching libraries
are not required on the developer's machine.

The Unity collector supplies rules for its admitted project/package paths as
revision-bound evidence; it does not grant the server access to an arbitrary
local project directory. An authorized external source uses rules within its
own granted root. It never reads a parent outside that grant to discover more
rules, and exclusion settings cannot expand filesystem access.

## Existing graph data and updates

Rule changes invalidate the effective collection policy. Reconciliation removes
excluded generated content and prevents excluded endpoints from reappearing
through serialized GUID dependencies, Resources/Addressables keys, wrapper
analysis or runtime observations. Queries and cached or historical results must
respect the current exclusion policy, including when a caller supplies a GUID
directly. Removing an exclusion makes the path eligible for collection again;
it does not require deleting the graph database.

Authored manual declarations are preserved, but declarations involving an
excluded endpoint are not exposed as active graph references. A private path and
identity admission map may remain so that a GUID-only reference can be rejected
without loading the excluded asset. Exclusions govern graph collection and
delivery; they do not delete source files or independently retained native
journal/runtime-capture files.

Missing, changed or unreadable rule evidence cannot silently become an allow-all
policy. The provider reports incomplete or unavailable evidence and retries
through the existing collection flow. Snapshot revision checks prevent a worker
from publishing against rules that changed during collection.

## Collection cost

Native rule discovery reuses the asset inventory's ancestor directories. It does
not recursively scan the whole project filesystem on each status request.
Watcher events invalidate cached evidence, while explicit snapshot checks cover
present and missing candidates. The server compiles and reuses matching rules
by effective policy revision. Large rule files use bounded transport chunks and
the existing operator-controlled collection budgets rather than a new total
project-size limit.

Each individual pattern search has a two-second complexity guard, using the
server's existing `regex` dependency. This is not a deadline for a graph job or
the number of files it may collect. Operators can provision a different positive,
finite value with `UNITY_MCP_REFERENCE_GRAPH_PATTERN_SECONDS`. If matching exceeds
that budget, collection reports incomplete evidence instead of treating the path
as included. Ordinary patterns continue to use compiled, cached matchers.
