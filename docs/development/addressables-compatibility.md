# Addressables compatibility

`manage_addressables` accepts stable Addressables 1.x versions from 1.28 onward
and stable 2.x versions when the requested operation's APIs and stored schema
are compatible. Package sources and public API documentation for
[1.28.2](https://docs.unity3d.com/Packages/com.unity.addressables@1.28/api/UnityEditor.AddressableAssets.Settings.AddressableAssetSettings.html)
and [2.7.6](https://docs.unity3d.com/Packages/com.unity.addressables@2.7/api/UnityEditor.AddressableAssets.Settings.AddressableAssetSettings.html)
establish the compatibility baselines. This is conditional family support,
not a claim that every patch and minor version has been executed in Unity.
Earlier 1.x releases, prereleases, malformed versions and unknown major versions
return `addressables_version_unsupported`.

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

Addressables 1.x edits additionally require its native internal
`AddressableAssetSettings.ClearFindAssetEntryCache()` hook with the verified
assembly, declaring type and signature. After native Undo/Redo restores group
collections, this hook invalidates only the nonserialized settings lookup cache
that otherwise can retain stale entry instances. The adapter keeps weak
references to settings actually edited through the tool, prunes destroyed
owners and releases its event subscription on domain reload or Editor shutdown.
It does not rewrite assets, hashes or unrelated settings. A missing hook refuses
1.x authoring before Undo registration; inspection remains available.

Inspection reads the stored active profile and profile fields directly.
It does not call getters that create default profiles or migrate stored data.
Existing revision guards, bounded scans, read-only checks, native readback,
Undo, label/GUID preservation and the absence of global `SaveAssets` remain
part of the authoring contract. No operation upgrades packages, creates default
settings or starts an Addressables content build.

For compatibility verification, run `AddressablesCompatibilityTests` and
`AddressablesAuthoringTests` in isolated projects using each baseline package.
The first suite exercises version admission, action signature/schema refusal
and the installed layout field schema. The second uses real package-created
temporary settings to verify inspection, preflight, address/group/profile
edits, Undo, refusal without migration and preservation of existing asset bytes.
`AddressablesActualBuildTests` remains a separately authorized explicit content
build fixture; a content build is not required to inspect existing reports.
