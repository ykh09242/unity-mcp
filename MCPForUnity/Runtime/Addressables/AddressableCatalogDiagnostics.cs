using System;
using System.Collections.Generic;
using System.Linq;
using System.Security.Cryptography;
using System.Text;
using System.Threading.Tasks;
using UnityEngine;
using UnityEngine.AddressableAssets.ResourceLocators;
using UnityEngine.ResourceManagement;
using UnityEngine.ResourceManagement.AsyncOperations;
using UnityEngine.ResourceManagement.ResourceLocations;
using UnityEngine.ResourceManagement.ResourceProviders;
using Provider = UnityEngine.AddressableAssets.Addressables;

namespace MCPForUnity.Runtime.Content
{
    [Serializable]
    public sealed class CatalogObservation
    {
        public string stage;
        public string error;
        public int update_count = -1;
        public int updated_catalog_count;
        public string[] catalog_ids;
        public string[] pending_catalog_ids;
        public string updated_catalog_id;
        public string installed_hash_id;
        public int location_count = -1;
        public string location_id;
        public long download_bytes_before = -1;
        public long download_bytes_after = -1;
        public BundleObservation[] bundles_before;
        public BundleObservation[] bundles_after;
        public string loaded_marker;
    }

    [Serializable]
    public sealed class BundleObservation
    {
        public string hash;
        public bool cached;
    }

    /// <summary>Observe a small, caller-selected TextAsset version marker from the current desktop Player catalog.</summary>
    public static class AddressableCatalogDiagnostics
    {
        // A location or locator identifier can be a signed URL or a local file path.
        private static string Identifier(string value)
        {
            using var hash = SHA256.Create();
            return BitConverter.ToString(hash.ComputeHash(Encoding.UTF8.GetBytes(value))).Replace("-", "").ToLowerInvariant();
        }

        private static async Task<R> Read<T, R>(AsyncOperationHandle<T> handle, Func<T, R> observe, ResourceManager owner = null)
        {
            try
            {
                await handle.Task;
                if (handle.Status != AsyncOperationStatus.Succeeded)
                    throw new InvalidOperationException("Addressables operation failed.", handle.OperationException);
                return observe(handle.Result);
            }
            finally
            {
                if (handle.IsValid())
                    (owner ?? Provider.ResourceManager).Release(handle);
            }
        }

        private static BundleObservation[] Bundles(IResourceLocation root)
        {
            var work = new Queue<IResourceLocation>();
            var seen = new HashSet<IResourceLocation>();
            var result = new List<BundleObservation>();
            work.Enqueue(root);
            int edges = 0;
            while (work.Count > 0)
            {
                var location = work.Dequeue();
                if (!seen.Add(location))
                    continue;
                if (location.Data is AssetBundleRequestOptions bundle && !string.IsNullOrEmpty(bundle.Hash))
                {
                    var version = new CachedAssetBundle(bundle.BundleName, Hash128.Parse(bundle.Hash));
                    result.Add(new BundleObservation { hash = bundle.Hash, cached = Caching.IsVersionCached(version) });
                }
                if (location.HasDependencies)
                    foreach (var child in location.Dependencies)
                    {
                        if (++edges > 256)
                            throw new InvalidOperationException("Marker dependency budget exceeded.");
                        work.Enqueue(child);
                    }
            }
            return result.ToArray();
        }

        /// <summary>Get the opaque catalog ID for an application-owned native locator.</summary>
        public static string GetCatalogId(IResourceLocator locator) => Identifier((locator ?? throw new ArgumentNullException(nameof(locator))).LocatorId);

        /// <summary>Call on Unity's main thread. An explicit update applies the sole pending catalog before loading its marker.</summary>
        public static Task<CatalogObservation> ObserveAsync(string markerAddress, bool applySoleUpdate = false) =>
            Observe(markerAddress, applySoleUpdate, null);

        /// <summary>Update exactly the pending catalog identified by GetCatalogId and load its own marker.</summary>
        public static Task<CatalogObservation> ApplySelectedUpdateAsync(string markerAddress, string catalogId)
        {
            if (catalogId == null || catalogId.Length != 64 || catalogId.Any(c => !(c >= '0' && c <= '9' || c >= 'a' && c <= 'f')))
                throw new ArgumentException("Supply the lowercase SHA-256 catalog ID.", nameof(catalogId));
            return Observe(markerAddress, true, catalogId);
        }

        private static async Task<CatalogObservation> Observe(string markerAddress, bool apply, string catalogId)
        {
            if (string.IsNullOrWhiteSpace(markerAddress) || markerAddress.Length > 512)
                throw new ArgumentException("Supply a marker address of 1 through 512 characters.", nameof(markerAddress));
            var observation = new CatalogObservation { stage = "initialize" };
            try
            {
                // Resource location requests own their operation and chain onto any initialization already in progress.
                // The marker can first appear in the updated catalog, so an empty pre-update lookup is valid.
                await Read(Provider.LoadResourceLocationsAsync(markerAddress, typeof(TextAsset)), prior => prior.Count);
                observation.stage = "check_catalogs";
                var current = Provider.ResourceLocators.Take(65).ToArray();
                if (current.Length > 64)
                    throw new InvalidOperationException("Locator count exceeds 64.");
                var localHashes = current.Select(locator => Provider.GetLocatorInfo(locator)?.LocalHash).ToArray();
                bool SameInstallation(IResourceLocator original = null, IResourceLocator replacement = null, string replacementHash = null)
                {
                    var installed = Provider.ResourceLocators.Take(65).ToArray();
                    if (installed.Length != current.Length)
                        return false;
                    for (int i = 0; i < current.Length; i++)
                    {
                        bool selected = ReferenceEquals(current[i], original);
                        if (
                            !ReferenceEquals(installed[i], selected ? replacement : current[i])
                            || Provider.GetLocatorInfo(installed[i])?.LocalHash != (selected ? replacementHash : localHashes[i])
                        )
                            return false;
                    }
                    return true;
                }
                var updates = new Dictionary<string, string>(StringComparer.Ordinal);
                foreach (var locator in current)
                {
                    var info = Provider.GetLocatorInfo(locator);
                    if (info == null || !info.CanUpdateContent)
                        continue;
                    if (current.Count(other => other.LocatorId == locator.LocatorId) != 1)
                    {
                        observation.error = "ambiguous_catalog_id";
                        return observation;
                    }
                    var location = info.HashLocation;
                    var hostManager = Provider.ResourceManager;
                    var textProvider = hostManager.GetResourceProvider(typeof(string), location);
                    if (location.HasDependencies || textProvider?.GetType() != typeof(TextDataProvider))
                    {
                        observation.error = "catalog_hash_freshness_unknown";
                        return observation;
                    }
                    // A retained application check can keep a completed hash operation cached under this exact location.
                    // Isolate only the operation cache; preserve the actual text provider, location, transform and request hook.
                    using var fresh = new ResourceManager
                    {
                        InternalIdTransformFunc = hostManager.InternalIdTransformFunc,
                        WebRequestOverride = hostManager.WebRequestOverride,
                    };
                    fresh.ResourceProviders.Add(textProvider);
                    string remoteHash = await Read(fresh.ProvideResource<string>(location), hash => hash, fresh);
                    if (string.IsNullOrEmpty(remoteHash))
                        throw new InvalidOperationException("Missing catalog hash.");
                    if (remoteHash != info.LocalHash)
                        updates.Add(locator.LocatorId, remoteHash);
                }
                if (!SameInstallation())
                {
                    observation.error = "catalog_installation_changed";
                    return observation;
                }
                observation.update_count = updates.Count;
                observation.pending_catalog_ids = updates.Keys.Select(Identifier).ToArray();
                IResourceLocator original = null;
                IResourceLocator updated = null;
                string updateHash = null;
                if (apply && (updates.Count > 0 || catalogId != null))
                {
                    observation.stage = "update_catalog";
                    if (catalogId == null && updates.Count != 1)
                    {
                        observation.error = "multiple_pending_catalogs";
                        return observation;
                    }
                    var candidates = current.Where(c => catalogId == null ? updates.ContainsKey(c.LocatorId) : GetCatalogId(c) == catalogId).ToArray();
                    if (candidates.Length > 1)
                    {
                        observation.error = "ambiguous_catalog_id";
                        return observation;
                    }
                    original = candidates.SingleOrDefault();
                    if (original == null || !updates.TryGetValue(original.LocatorId, out updateHash))
                    {
                        observation.error = original == null ? "catalog_not_found" : "catalog_not_pending";
                        return observation;
                    }
                    var update = Provider.UpdateCatalogs(new[] { original.LocatorId }, true);
                    // Some providers return an existing in-flight update. Hold only our own reference while reading its result.
                    Provider.ResourceManager.Acquire(update);
                    updated = await Read(update, locators => locators?.Count == 1 ? locators[0] : null);
                    if (updated?.LocatorId != original.LocatorId || !SameInstallation(original, updated, updateHash))
                    {
                        observation.error = "catalog_update_unconfirmed";
                        return observation;
                    }
                    observation.updated_catalog_count = 1;
                    observation.updated_catalog_id = GetCatalogId(updated);
                    observation.installed_hash_id = Identifier(updateHash);
                }
                observation.stage = "locate_marker";
                var catalogs = Provider.ResourceLocators.Take(65).ToArray();
                if (catalogs.Length > 64)
                    throw new InvalidOperationException("Locator count exceeds 64.");
                observation.catalog_ids = catalogs.Select(locator => Identifier(locator.LocatorId)).ToArray();
                AsyncOperationHandle<IList<IResourceLocation>> lookup = default;
                try
                {
                    IList<IResourceLocation> locations;
                    if (catalogId != null)
                        updated.Locate(markerAddress, typeof(TextAsset), out locations);
                    else
                    {
                        lookup = Provider.LoadResourceLocationsAsync(markerAddress, typeof(TextAsset));
                        await lookup.Task;
                        if (lookup.Status != AsyncOperationStatus.Succeeded)
                            throw new InvalidOperationException("Marker lookup failed.");
                        locations = lookup.Result;
                    }
                    observation.location_count = locations?.Count ?? 0;
                    if (observation.location_count != 1)
                    {
                        observation.error = observation.location_count == 0 ? "marker_not_found" : "ambiguous_marker";
                        return observation;
                    }
                    var selected = locations[0];
                    observation.location_id = Identifier(selected.InternalId);
                    observation.stage = "cache_before";
                    if (!Caching.ready)
                    {
                        observation.error = "cache_not_ready";
                        return observation;
                    }
                    observation.bundles_before = Bundles(selected);
                    observation.download_bytes_before = await Read(Provider.GetDownloadSizeAsync(selected), bytes => bytes);
                    observation.stage = "load_marker";
                    observation.loaded_marker = await Read(Provider.LoadAssetAsync<TextAsset>(selected), asset =>
                    {
                        if (asset == null || asset.GetData<byte>().Length > 256)
                            throw new InvalidOperationException("Use a UTF-8 TextAsset marker of at most 256 bytes.");
                        return asset.text;
                    });
                    observation.stage = "cache_after";
                    observation.bundles_after = Bundles(selected);
                    observation.download_bytes_after = await Read(Provider.GetDownloadSizeAsync(selected), bytes => bytes);
                }
                finally
                {
                    if (lookup.IsValid())
                        Provider.Release(lookup);
                }
                if (updated != null && !SameInstallation(original, updated, updateHash))
                {
                    observation.error = "catalog_installation_changed";
                    return observation;
                }
                observation.stage = "complete";
            }
            catch (Exception error)
            {
                observation.error = error.GetType().Name;
            }
            return observation;
        }
    }
}
