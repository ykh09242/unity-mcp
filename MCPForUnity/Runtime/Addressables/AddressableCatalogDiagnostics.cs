using System;
using System.Collections.Generic;
using System.Linq;
using System.Security.Cryptography;
using System.Text;
using System.Threading.Tasks;
using UnityEngine;
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

        /// <summary>Call on Unity's main thread. An explicit update applies the sole pending catalog before loading its marker.</summary>
        public static async Task<CatalogObservation> ObserveAsync(string markerAddress, bool applySoleUpdate = false)
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
                var updates = new List<string>();
                string updateHash = null;
                foreach (var locator in current)
                {
                    var info = Provider.GetLocatorInfo(locator);
                    if (info == null || !info.CanUpdateContent)
                        continue;
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
                    {
                        updates.Add(locator.LocatorId);
                        updateHash = remoteHash;
                    }
                }
                observation.update_count = updates.Count;
                if (applySoleUpdate && updates.Count > 0)
                {
                    observation.stage = "update_catalog";
                    if (updates.Count != 1)
                    {
                        observation.error = "multiple_pending_catalogs";
                        return observation;
                    }
                    var update = Provider.UpdateCatalogs(updates, true);
                    // Some providers return an existing in-flight update. Hold only our own reference while reading its result.
                    Provider.ResourceManager.Acquire(update);
                    int matched = await Read(update, locators => locators.Count(locator => locator.LocatorId == updates[0]));
                    if (matched != 1 || Provider.GetLocatorInfo(updates[0])?.LocalHash != updateHash)
                    {
                        observation.error = "catalog_update_unconfirmed";
                        return observation;
                    }
                    observation.updated_catalog_count = matched;
                }
                observation.stage = "locate_marker";
                var catalogs = Provider.ResourceLocators.Take(65).ToArray();
                if (catalogs.Length > 64)
                    throw new InvalidOperationException("Locator count exceeds 64.");
                observation.catalog_ids = catalogs.Select(locator => Identifier(locator.LocatorId)).ToArray();
                var locations = Provider.LoadResourceLocationsAsync(markerAddress, typeof(TextAsset));
                try
                {
                    await locations.Task;
                    if (locations.Status != AsyncOperationStatus.Succeeded)
                        throw new InvalidOperationException("Marker lookup failed.");
                    observation.location_count = locations.Result.Count;
                    if (observation.location_count != 1)
                    {
                        observation.error = observation.location_count == 0 ? "marker_not_found" : "ambiguous_marker";
                        return observation;
                    }
                    var selected = locations.Result[0];
                    observation.location_id = Identifier(selected.InternalId);
                    observation.stage = "cache_before";
                    if (!Caching.ready)
                    {
                        observation.error = "cache_not_ready";
                        return observation;
                    }
                    observation.bundles_before = Bundles(selected);
                    observation.download_bytes_before = await Read(Provider.GetDownloadSizeAsync(markerAddress), bytes => bytes);
                    observation.stage = "load_marker";
                    observation.loaded_marker = await Read(Provider.LoadAssetAsync<TextAsset>(selected), asset =>
                    {
                        if (asset == null || asset.GetData<byte>().Length > 256)
                            throw new InvalidOperationException("Use a UTF-8 TextAsset marker of at most 256 bytes.");
                        return asset.text;
                    });
                    observation.stage = "cache_after";
                    observation.bundles_after = Bundles(selected);
                    observation.download_bytes_after = await Read(Provider.GetDownloadSizeAsync(markerAddress), bytes => bytes);
                }
                finally
                {
                    if (locations.IsValid())
                        Provider.Release(locations);
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
