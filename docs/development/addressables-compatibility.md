# Addressables compatibility

`manage_addressables` recognizes stable Addressables **1.x, 2.x, 3.x and 4.x**
package families. Family admission permits capability checks; it does not promise
that every action exists in every minor version. Prereleases, malformed versions
and unknown major versions return `addressables_version_unsupported`.
The adapter's Unity 2021.3 floor and optional-package behavior are unchanged.
Each package retains its own Editor requirement. No tool installs or upgrades it.

| Family | Official source audit representatives | Latest stable in the audited registry |
| --- | --- | --- |
| 1.x | 1.1.5, 1.28.2, 1.29.0 | [1.29.0](https://download.packages.unity.com/com.unity.addressables/-/com.unity.addressables-1.29.0.tgz), Unity 2021.3 minimum |
| 2.x | 2.0.3, 2.7.6, 2.11.2 | [2.11.2](https://docs.unity3d.com/Packages/com.unity.addressables@2.11/changelog/CHANGELOG.html), Unity 6000.0 minimum |
| 3.x | 3.0.0, 3.1.0 | [3.1.0](https://docs.unity3d.com/Packages/com.unity.addressables@3.1/changelog/CHANGELOG.html), Unity 6000.0 minimum |
| 4.x | 4.0.0, 4.1.1 | [4.1.1](https://docs.unity3d.com/Packages/com.unity.addressables@4.1/changelog/CHANGELOG.html), Unity 6000.0 minimum |

The registry/source audit was performed on 2026-10-27. Package release notes can
cover unrelated API changes; the adapter checks the exact members it consumes.
For example, the audited 3.0.0 and 4.0.0 sources retain the three authoring
signatures below. Version 1.1.5 lacks `GetLabels()` and the consumed `BuildLayout`
type, so affected actions refuse their missing capabilities rather than rejecting
the entire 1.x family. Later versions must satisfy the same runtime checks.

| Action | Required capability |
| --- | --- |
| `inspect`, `preflight` | Existing explicit settings asset, required native snapshot members, public `List<string> GetLabels()` and stored profile schema version 1. |
| `set_address` | Snapshot capabilities and public `void SetAddress(string, bool)`. |
| `move_entry` | Snapshot capabilities and public `AddressableAssetEntry CreateOrMoveEntry(string, AddressableAssetGroup, bool, bool)`. |
| `set_profile_value` | Snapshot capabilities and public `void SetValue(string, string, string)`. |
| `analyze_layout` | Complete consumed native layout field schema, an existing bounded JSON report, and report package identity/version matching the installed package. |

The edit signature, fresh snapshot and applicable Undo capability are checked
before registering Undo or invoking a mutation. An unavailable edit API therefore
does not prevent inspection of otherwise compatible settings. Missing or
incompatible API members return `addressables_api_unsupported` with the affected
action/member. Unsupported stored profile versions return
`addressables_profile_schema_unsupported` with the requested action.

Addressables 1.x, 3.x and 4.x edits additionally require the exact native internal
`AddressableAssetSettings.ClearFindAssetEntryCache()` hook, including its verified
assembly, declaring type and signature. After native Undo/Redo restores group
collections, this hook clears only the nonserialized settings lookup cache that
can retain stale entry instances. Native hash resets and group deserialization
alone do not replace this lookup-cache reset. The existing 2.x path uses native
hash-aware cache invalidation and is unchanged.

The adapter keeps weak references to settings actually edited through the tool,
prunes destroyed owners and releases its event subscription on domain reload or
Editor shutdown. It does not rewrite assets, hashes or unrelated settings.
A missing hook refuses affected authoring before Undo registration; inspection
remains available when its own capabilities are present.

## Existing layout reports

Legacy `DuplicatedAssets` analysis remains available when the consumed native
schema is compatible. Content Directory support is detected from the actual
loaded native type, independently of the version family. If the extension is
entirely absent, legacy reports do not need a `ContentDirectories` declaration.
If any extension member exists, the complete native schema must contain
`List<ContentDirectory> ContentDirectories` and `Group.ContentDirectoryName`.
Partial or incompatible native extensions refuse analysis.

A report whose native schema declares Content Directories must explicitly contain
an empty `ContentDirectories` array. Missing required declarations, nonempty
arrays and malformed values return `addressables_layout_schema_unsupported`.
Any nonempty or malformed `ContentDirectoryName` also refuses analysis, whether
stored inline or in Unity's managed-reference table. Null and empty strings are
compatible. These report checks also apply to mixed reports in older families,
so new coverage cannot be silently omitted while returning
`analysis_complete: true`.

Content Directory analysis is outside this adapter's scope. Report bytes remain
unchanged, and no content build or conversion is started. Compatible empty and
legacy reports retain the existing duplication analysis, size/depth/work bounds
and matching-package revision guards.

Inspection reads stored active-profile and profile fields directly. It does not
call getters that create defaults or migrate stored data. Existing bounded scans,
read-only checks, native readback, Undo, label/GUID preservation and the absence
of global `SaveAssets` remain part of the authoring contract.

## Verification evidence

The versions above are source audit representatives. They are not a claim of
native execution in every Editor/package combination. Record native test results
separately with the exact package version, Editor version and source identity.
Source compatibility on another OS is not native OS validation.

Run the complete `AddressablesCompatibilityTests` and `AddressablesAuthoringTests`
cohorts for 1.29.0, 2.11.2, 3.0.0, 3.1.0, 4.0.0 and 4.1.1, then restore and run
the warm project's 2.7.6 baseline. Retain each executed row's original result,
exact package/Editor versions and source identity in the corresponding release
verification records; source audit and compilation are not native execution.
The first suite covers strict family admission, exact action signatures,
stored/layout schemas and report coverage refusal. The second uses package-created
temporary settings for inspection, preflight, address/group/profile edits,
Undo/Redo, refusal without migration and preservation of existing asset/report
bytes. Early 1.1.5 missing capabilities are covered by source inspection and
controlled refusal tests; no blanket native support is implied.
`AddressablesActualBuildTests` remains a separately authorized explicit
content-build fixture; a build is unnecessary to inspect existing reports.
