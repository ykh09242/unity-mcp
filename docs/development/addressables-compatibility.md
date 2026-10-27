# Addressables compatibility

`manage_addressables` conditionally accepts these stable package families:

| Family | Minimum version | Source/API audit baseline |
| --- | --- | --- |
| 1.x | 1.28.0 | [1.28.2](https://docs.unity3d.com/Packages/com.unity.addressables@1.28/api/UnityEditor.AddressableAssets.Settings.AddressableAssetSettings.html) |
| 2.x | 2.0.0 | [2.7.6](https://docs.unity3d.com/Packages/com.unity.addressables@2.7/api/UnityEditor.AddressableAssets.Settings.AddressableAssetSettings.html) |
| 3.x | 3.1.0 | [3.1.0](https://docs.unity3d.com/Packages/com.unity.addressables@3.1/api/UnityEditor.AddressableAssets.Settings.AddressableAssetSettings.html) |
| 4.x | 4.1.1 | [4.1.1](https://docs.unity3d.com/Packages/com.unity.addressables@4.1/changelog/CHANGELOG.html) |

The requested operation must still satisfy its exact native API and stored-schema
checks. Earlier 1.x versions, 3.0.x, 4.0.x, 4.1.0, prereleases, malformed versions
and unknown major versions return `addressables_version_unsupported`.
Addressables 3.1 restored APIs removed in 3.0; admitting 3.1 does not admit 3.0.
The adapter's Unity 2021.3 floor and optional-package behavior are unchanged.
Each Addressables package retains its own Editor requirement; the audited 3.1.0
and 4.1.1 packages require Unity 6000.0. No tool installs or upgrades a package.

| Action | Required capability |
| --- | --- |
| `inspect`, `preflight` | Existing explicit settings asset, required native snapshot members, `GetLabels()` and stored profile schema version 1. |
| `set_address` | Snapshot capabilities and public `void SetAddress(string, bool)`. |
| `move_entry` | Snapshot capabilities and public `AddressableAssetEntry CreateOrMoveEntry(string, AddressableAssetGroup, bool, bool)`. |
| `set_profile_value` | Snapshot capabilities and public `void SetValue(string, string, string)`. |
| `analyze_layout` | Complete consumed native layout field schema, an existing bounded JSON report, and report package identity/version matching the installed package. |

The edit signature is resolved before registering Undo or invoking a mutation.
An unavailable edit API therefore does not prevent inspection of otherwise
compatible settings. Missing or incompatible API members return
`addressables_api_unsupported` with the affected action/member. Unsupported
stored profile versions return `addressables_profile_schema_unsupported`.

Addressables 1.x, 3.x and 4.x edits additionally require the exact native internal
`AddressableAssetSettings.ClearFindAssetEntryCache()` hook, including its verified
assembly, declaring type and signature. After native Undo/Redo restores group
collections, this hook clears only the nonserialized settings lookup cache that
can retain stale entry instances. Native hash resets and group deserialization
alone do not replace this lookup-cache reset. The 2.x path is unchanged.

The adapter keeps weak references to settings actually edited through the tool,
prunes destroyed owners and releases its event subscription on domain reload or
Editor shutdown. It does not rewrite assets, hashes or unrelated settings.
A missing hook refuses affected authoring before Undo registration; inspection
remains available.

## Existing layout reports

Legacy `DuplicatedAssets` analysis remains available. For 3.x and 4.x, the native
schema must additionally contain `List<ContentDirectory> ContentDirectories` and
`Group.ContentDirectoryName`. A modern report must explicitly contain an empty
`ContentDirectories` array. Missing modern declarations, nonempty arrays and
malformed values return `addressables_layout_schema_unsupported`.

Any nonempty or malformed `ContentDirectoryName` also refuses analysis, whether
stored inline or in Unity's managed-reference table. Null and empty strings are
compatible. These checks apply to mixed reports in older families too, so new
coverage cannot be silently omitted while returning `analysis_complete: true`.
Content Directory analysis is outside this adapter's scope. Report bytes remain
unchanged, and no content build or conversion is started. Compatible empty and
legacy reports retain the existing duplication analysis, size/depth/work bounds
and matching-package revision guards.

Inspection reads stored active-profile and profile fields directly. It does not
call getters that create defaults or migrate stored data. Existing bounded scans,
read-only checks, native readback, Undo, label/GUID preservation and the absence
of global `SaveAssets` remain part of the authoring contract.

## Verification evidence

The versions in the table are source/API audit baselines, not an assertion that
this change has executed in each Editor/package combination. Native test coverage
must be recorded separately with the exact package version, Editor version and
source identity. Source compatibility on another OS is not native OS validation.

Run `AddressablesCompatibilityTests` and `AddressablesAuthoringTests` against each
baseline in isolated projects. The first suite covers strict version admission,
action signatures, stored/layout schemas and report coverage refusal. The second
uses package-created temporary settings for inspection, preflight, address/group/
profile edits, Undo/Redo, refusal without migration and preservation of existing
asset/report bytes. In particular, run the complete cohort for 3.1.0 and 4.1.1,
then the 2.7.6 regression cohort. No unverified 1.x or 3.x/4.x native result is
implied by the compatibility table. `AddressablesActualBuildTests` remains a
separately authorized explicit content-build fixture; a build is unnecessary to
inspect existing reports.
