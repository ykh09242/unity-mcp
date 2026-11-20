# Player catalog and content-update diagnostics

On desktop, call `MCPForUnity.Runtime.Content.AddressableCatalogDiagnostics.ObserveAsync(markerAddress)` on Unity's main thread. Supply an address for a diagnostic UTF-8 `TextAsset` of at most 256 bytes. The returned observation tracks initialization, checking for catalog updates, resolving exactly one marker location, observing bundle hashes in Unity's cache, loading the marker, and checking remaining download bytes. A missing or ambiguous marker returns its lookup count and error. A failed stage leaves subsequent observations unset. Catalog and location identifiers are SHA-256 digests because providers can put paths or credentials in them.

The optional assembly targets desktop Players and the Editor with Addressables versions from 1.21.17 through 2.x. Its absence leaves the base runtime and Editor adapter available. Applications with explicit assembly references can reference `MCPForUnity.Addressables.Diagnostics` from a matching optional assembly. The first resource-location query starts or joins the normal initialization sequence. For dependency-free hash locations served by Unity's `TextDataProvider`, the probe uses a separate operation cache with the application's provider, original location, ID transform and web request override. A host can keep its completed catalog check while the probe fetches the current hash. Other hash-provider shapes return `catalog_hash_freshness_unknown`. An explicit catalog update must confirm the freshly observed hash in the installed locator or the probe returns `catalog_update_unconfirmed`. Set `DisableCatalogUpdateOnStartup` when the application needs to observe its previous catalog first.

The default call checks for pending updates and loads the current marker. Pass `applySoleUpdate: true` to apply a single pending catalog before loading the marker. Multiple pending catalogs return `multiple_pending_catalogs` in this mode. Bundle acquisition during marker loading can populate the Unity cache. `bundles_before` and `bundles_after` identify the requested bundle versions by native hash and report whether Unity cached each version. `loaded_marker` comes from the loaded asset. Use an application-owned diagnostic marker, since its text becomes part of the result. [Unity catalog checks](https://docs.unity3d.com/Packages/com.unity.addressables@1.21/api/UnityEngine.AddressableAssets.Addressables.CheckForCatalogUpdates.html) · [Unity catalog updates](https://docs.unity3d.com/Packages/com.unity.addressables@1.21/api/UnityEngine.AddressableAssets.Addressables.UpdateCatalogs.html)

For an explicit selection, obtain the ID from the locator returned by the application's catalog load. Release that load handle when finished with it so Addressables can fetch the next generation. Each observation lists opaque `pending_catalog_ids`. Use the application's catalog identity when selecting from that list. [Unity runtime catalog management](https://docs.unity3d.com/Packages/com.unity.addressables@1.21/manual/LoadContentCatalogAsync.html)

```csharp
string catalogId = AddressableCatalogDiagnostics.GetCatalogId(applicationLocator);
var result = await AddressableCatalogDiagnostics.ApplySelectedUpdateAsync("secondary-marker", catalogId);
```

The call checks fresh hashes, passes one native locator ID to `UpdateCatalogs` and resolves the marker through the updated locator. Success records one `updated_catalog_id` and the SHA-256 digest of its installed native hash in `installed_hash_id`. It checks the returned locator, installed hash, and the identities and installed hashes of the other locators. Unknown, nonpending and ambiguous selections return `catalog_not_found`, `catalog_not_pending` and `ambiguous_catalog_id` respectively. A changed installation observed before dispatch returns `catalog_installation_changed`. A mismatched native result returns `catalog_update_unconfirmed`. Native failure can leave installation state uncertain. Inspect a fresh observation before deciding on a retry. A confirmed update followed by a marker error retains its update fields.

## Controlled Windows Player fixture

The primary marker text and an unrelated texture share one label but occupy separate bundles. The probe resolves exactly one text location and measures its dependencies before and after loading. The five single-catalog cases also measure the entire label and the texture's own address. Both must still report the same positive download size after the selected marker's remaining size reaches zero.

Run with a licensed Unity 6000.0.69f1 Windows Editor and Windows standalone build support. Use a fresh output directory. Package preparation uses the reviewed 1.21.17 Addressables archive and Scriptable Build Pipeline 1.23.4. An optional `--registry-cache` points to an existing package cache.

```powershell
python tools/run_addressables_content_update.py --editor 'C:/Program Files/Unity/Hub/Editor/6000.0.69f1/Editor/Unity.exe' --unity-version 6000.0.69f1 --output .work/catalog-player-run
```

The fixture creates a disposable project, builds an original remote catalog, and builds one development Player. It edits the marker bytes at the same address and uses the original `addressables_content_state.bin` for a native content-update build. It builds an additional remote catalog with its own content state, marker, bundles and update at a separate URL. Each catalog must have different file hashes between generations at its original URL. A loopback server serves each generation to fresh processes of the original Player. All six processes share a dedicated bundle cache and a per-run product name for the Player's catalog cache.

In the stale and update cases, the Player first retains a completed native check against the old origin. It asks the loopback server to switch to the new catalog, runs the probe in that same process, and reads the original check again. The server records the hash GETs before and after switching. In the update case, the application then releases its old check, applies the update and loads the new marker in the same process. Other warm cases exercise concurrent initialization and automatic handle release. The 6000.0 no-license CI row compiles the provider and fixture sources for Windows Editor and Player symbols. Native cache and marker observations require a completed Player run.

| Process | Served catalog | Required loaded marker | Required evidence |
| --- | --- | --- | --- |
| Old cold | Original | Old | Bundle version initially absent, positive download size, observed bundle GET and cached after loading |
| Old cached | Original | Old | Existing bundle version, zero download size, no bundle GET |
| Old stale | Updated | Old | One pending update observed with the old catalog still in use and old bundle cached |
| Two pending | Both updated | Secondary new, primary old | Explicit secondary update, changed secondary bundle, original primary marker and cached bundle hash, only the primary still pending |
| New update | Updated | New | One catalog updated, different bundle hash, positive download size and observed bundle GET |
| New cached | Updated | New | No pending updates, updated bundle cached, zero download size and no bundle GET |

The two-pending process loads the additional catalog with `LoadContentCatalogAsync`, observes both old markers and switches both catalog generations in-process. It checks legacy ambiguity, an unknown ID and a duplicate locator ID, selects the secondary, checks a repeat selection, and reads the primary again. The receipt verifier requires a secondary catalog and bundle GET after the switch and fails on a primary catalog or bundle GET during that interval.

The fixture denies bundle requests during cached cases. A passing `result.json` binds the session and Player build GUID, catalog file hashes, both original content-state digests, HTTP requests and each process's observation. Missing receipts or a marker mismatch fail the run even when the address resolves. The runner retains build and Player artifacts in the output directory. Unity stores the run's product-specific catalog cache under the operating system's Player data directory. A no-license test of the receipt checks supplies no native Player result. [Unity content-update builds](https://docs.unity3d.com/Packages/com.unity.addressables@1.21/api/UnityEditor.AddressableAssets.Build.ContentUpdateScript.BuildContentUpdate.html)
