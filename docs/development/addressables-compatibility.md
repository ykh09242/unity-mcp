# Addressables compatibility

`manage_addressables` resolves the installed `com.unity.addressables` provider and
checks the members needed by each action. Package versions identify observations.
A new release or prerelease can use the same API and serialized report format.
Each package retains its own Unity Editor requirement.

| Family | Official source audit representatives | Latest stable in the audited registry |
| --- | --- | --- |
| 1.x | 1.1.5, 1.28.2, 1.29.0 | [1.29.0](https://download.packages.unity.com/com.unity.addressables/-/com.unity.addressables-1.29.0.tgz), Unity 2021.3 minimum |
| 2.x | 2.0.3, 2.7.6, 2.11.2 | [2.11.2](https://docs.unity3d.com/Packages/com.unity.addressables@2.11/changelog/CHANGELOG.html), Unity 6000.0 minimum |
| 3.x | 3.0.0, 3.1.0 | [3.1.0](https://docs.unity3d.com/Packages/com.unity.addressables@3.1/changelog/CHANGELOG.html), Unity 6000.0 minimum |
| 4.x | 4.0.0, 4.1.1 | [4.1.1](https://docs.unity3d.com/Packages/com.unity.addressables@4.1/changelog/CHANGELOG.html), Unity 6000.0 minimum |

The registry/source audit was performed on 2026-10-27. Package release notes can
cover unrelated API changes; the adapter checks the exact members it consumes.
The early 1.1.5 provider exposes the stored label table and explicit-entry lookup.
It lacks the native JSON BuildLayout type required by report analysis.

| Action | Required capability |
| --- | --- |
| `inspect`, `preflight` | Existing explicit settings asset, required native snapshot members, public `List<string> GetLabels()` or the stored `LabelTable.m_LabelNames` list and stored profile schema version 1. |
| `set_address` | Snapshot capabilities and public `void SetAddress(string, bool)`. |
| `move_entry` | Snapshot capabilities and public `AddressableAssetEntry CreateOrMoveEntry(string, AddressableAssetGroup, bool, bool)`. |
| `set_profile_value` | Snapshot capabilities and public `void SetValue(string, string, string)`. |
| `analyze_layout` | Required native duplication fields, an existing bounded JSON report declaring its Addressables producer, and compatible serialized fields and managed references. |

The edit signature, fresh snapshot and applicable Undo capability are checked
before registering Undo or invoking a mutation. An unavailable edit API therefore
does not prevent inspection of otherwise compatible settings. Missing or
incompatible API members return `addressables_api_unsupported` with the affected
action/member. Unsupported stored profile versions return
`addressables_profile_schema_unsupported` with the requested action.

For cached entry lookups, the adapter binds the internal
`AddressableAssetSettings.ClearFindAssetEntryCache()` hook by assembly, declaring
type, visibility, parameters and return type. Native Undo/Redo can replace
in-memory entries in group collections. Older cached providers expose only the
private, nonserialized `Cache<string, AddressableAssetEntry> m_FindAssetEntryCache`.
The adapter clears that specific field when the provider has no reset hook.
Earlier uncached providers search the group's live entry map directly.

The adapter keeps weak references to edited settings and releases the Undo event
on assembly reload or Editor shutdown. Unknown lookup APIs refuse authoring
before Undo registration. Inspection remains available when its own APIs exist.

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
and content-hash revision guards.

The producer declaration identifies the report's package. The reader checks the
installed native serializer's fields, value types and managed-reference graph
before decoding the report, including the source assembly, namespace, nested
type, reference ID and target type. Reports with missing, added or incompatible
fields refuse analysis. The response includes the installed `package_version`
and the declared `report_package_version`.

Inspection reads stored active-profile and profile fields directly. It does not
call getters that create defaults or migrate stored data. Existing bounded scans,
read-only checks, native readback, Undo, label/GUID preservation and the absence
of global `SaveAssets` remain part of the authoring contract.

## Verification evidence

Windows native tests ran on Unity 6000.0.69f1 with official Addressables packages.
On 1.1.5, 1.19.19 and 1.21.17 the focused fixtures exercised settings inspection,
group ownership, guarded edits, label preservation and Undo/Redo with Scriptable
Build Pipeline 1.23.4. The original 1.1.5 dependency on Build Pipeline 1.5.1
uses a Sprite Packer API removed from that Editor.

On 1.29.0, 2.7.6 and 4.1.1 the `AddressablesCompatibilityTests` and
`AddressablesAuthoringTests` suites also exercised stored profiles, bounded
preflight, native layout serialization, compatible producer declarations and
malformed reference graphs. The tests use package-created temporary settings and
check preservation of existing asset and report bytes.

The 1.21.20 build report shipped in Unity's 2.7.6 package fixtures decoded with
duplication evidence under 1.21.17. Under 2.7.6 the same file failed the schema
check. Its editor settings omit `EnableJsonCatalog` and its runtime settings
include `ProfilerEvents`.

`AddressablesActualBuildTests` remains a separately authorized explicit
content-build fixture; a build is unnecessary to inspect existing reports.
