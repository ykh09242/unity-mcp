using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Threading.Tasks;
using MCPForUnity.Runtime.Content;
using Newtonsoft.Json;
using UnityEngine;
using UnityEngine.AddressableAssets;
using UnityEngine.AddressableAssets.ResourceLocators;
using UnityEngine.Networking;
using UnityEngine.ResourceManagement.AsyncOperations;

public sealed class CatalogMarkerPlayer : MonoBehaviour
{
    private static async Task<long> RemainingBytes(string key)
    {
        var handle = Addressables.GetDownloadSizeAsync(key);
        try
        {
            await handle.Task;
            if (handle.Status != AsyncOperationStatus.Succeeded)
                throw new InvalidOperationException("Fixture key size query failed.");
            return handle.Result;
        }
        finally
        {
            Addressables.Release(handle);
        }
    }

    private static async Task SignalOrigin(string origin, string phase, string session)
    {
        using var signal = UnityWebRequest.Put(origin + "/" + phase + "/" + session, Array.Empty<byte>());
        signal.timeout = 10;
        var request = signal.SendWebRequest();
        while (!request.isDone)
            await Task.Yield();
        if (signal.responseCode != 204)
            throw new InvalidOperationException("Catalog fixture origin rejected phase " + phase + ".");
    }

    private static async Task<object> SelectiveUpdate(string session)
    {
        string origin = Environment.GetEnvironmentVariable("MCP_CATALOG_ORIGIN");
        string path = Environment.GetEnvironmentVariable("MCP_CATALOG_SECONDARY");
        var host = Addressables.InitializeAsync(false);
        try
        {
            await host.Task;
            if (host.Status != AsyncOperationStatus.Succeeded)
                throw new InvalidOperationException("Fixture initialization failed.");
            string primaryId = AddressableCatalogDiagnostics.GetCatalogId(host.Result);
            var load = Addressables.LoadContentCatalogAsync(origin + "/" + path, false);
            string secondaryId;
            string secondaryLocatorId;
            try
            {
                await load.Task;
                if (load.Status != AsyncOperationStatus.Succeeded)
                    throw new InvalidOperationException("Secondary catalog load failed.");
                secondaryId = AddressableCatalogDiagnostics.GetCatalogId(load.Result);
                secondaryLocatorId = load.Result.LocatorId;
            }
            finally
            {
                Addressables.Release(load);
            }
            // A cold additional catalog may start with a provider-generated offline hash.
            var secondaryBefore = await AddressableCatalogDiagnostics.ObserveAsync("secondary-marker");
            if (secondaryBefore.pending_catalog_ids?.Contains(secondaryId) == true)
                secondaryBefore = await AddressableCatalogDiagnostics.ApplySelectedUpdateAsync("secondary-marker", secondaryId);
            var primaryBefore = await AddressableCatalogDiagnostics.ObserveAsync("catalog-marker");
            await SignalOrigin(origin, "switch", session);
            var ambiguous = await AddressableCatalogDiagnostics.ObserveAsync("secondary-marker", true);
            var unknown = await AddressableCatalogDiagnostics.ApplySelectedUpdateAsync("secondary-marker", new string('0', 64));
            var duplicate = new ResourceLocationMap(secondaryLocatorId);
            Addressables.AddResourceLocator(duplicate);
            CatalogObservation duplicateSelection;
            try
            {
                duplicateSelection = await AddressableCatalogDiagnostics.ApplySelectedUpdateAsync("secondary-marker", secondaryId);
            }
            finally
            {
                Addressables.RemoveResourceLocator(duplicate);
            }
            var selected = await AddressableCatalogDiagnostics.ApplySelectedUpdateAsync("secondary-marker", secondaryId);
            var repeated = await AddressableCatalogDiagnostics.ApplySelectedUpdateAsync("secondary-marker", secondaryId);
            var primaryAfter = await AddressableCatalogDiagnostics.ObserveAsync("catalog-marker");
            return new
            {
                primary_id = primaryId,
                secondary_id = secondaryId,
                primary_before = primaryBefore,
                secondary_before = secondaryBefore,
                ambiguous,
                unknown,
                duplicate = duplicateSelection,
                selected,
                repeated,
                primary_after = primaryAfter,
            };
        }
        finally
        {
            Addressables.Release(host);
        }
    }

    private static async Task<object> TornDeployment(string session, string cacheCase)
    {
        string origin = Environment.GetEnvironmentVariable("MCP_CATALOG_ORIGIN");
        // The product-specific catalog cache persists across launches. Install the original
        // catalog without fetching its marker bundle, including in the empty cache case.
        var baseline = await AddressableCatalogDiagnostics.ObserveAsync("absent-fixture-marker", true);
        if (baseline.stage != "locate_marker" || baseline.error != "marker_not_found")
            throw new InvalidOperationException("Could not establish the baseline catalog.");
        if (cacheCase == "torn-old-warm")
        {
            var prime = await AddressableCatalogDiagnostics.ObserveAsync("marker-text");
            if (prime.loaded_marker != "old-" + session || prime.stage != "complete")
                throw new InvalidOperationException("Failed to prime the original bundle.");
        }
        long oldBytes = await RemainingBytes("marker-text");
        await SignalOrigin(origin, "switch", session);
        var torn = await AddressableCatalogDiagnostics.ObserveAsync("marker-text", true);
        await SignalOrigin(origin, "rollback", session);
        var recovered = await AddressableCatalogDiagnostics.ObserveAsync("marker-text", true);
        await SignalOrigin(origin, "observed", session);
        var stable = await AddressableCatalogDiagnostics.ObserveAsync("marker-text");
        return new
        {
            baseline,
            old_bytes = oldBytes,
            torn,
            recovered,
            stable,
        };
    }

    private async void Start()
    {
        int exit = 1;
        try
        {
            string session = Environment.GetEnvironmentVariable("MCP_CATALOG_SESSION");
            string directory = Environment.GetEnvironmentVariable("MCP_CATALOG_RUN");
            if (Application.isEditor || !Debug.isDebugBuild || !Guid.TryParseExact(session, "N", out _) || Application.productName != "Catalog-" + session)
                throw new InvalidOperationException("Invalid standalone fixture identity.");
            string root = Directory.GetParent(directory).FullName;
            if (File.ReadAllText(Path.Combine(root, ".catalog-owner")) != session)
                throw new InvalidOperationException("Invalid probe destination.");
            var timeout = System.Diagnostics.Stopwatch.StartNew();
            while (!Caching.ready)
            {
                if (timeout.Elapsed.TotalSeconds >= 10)
                    throw new TimeoutException("Unity's bundle cache did not become ready.");
                await Task.Yield();
            }
            string initialization = Environment.GetEnvironmentVariable("MCP_CATALOG_INIT");
            string cacheName = initialization == "torn-cold" || initialization == "torn-old-warm" ? initialization + "-cache" : "bundle-cache";
            var cache = Caching.AddCache(Directory.CreateDirectory(Path.Combine(root, cacheName)).FullName);
            if (!cache.valid)
                throw new InvalidOperationException("Could not open the owned fixture cache.");
            Caching.currentCacheForWriting = cache;
            bool apply = Environment.GetEnvironmentVariable("MCP_CATALOG_APPLY") == "1";
            if (initialization == "screen-handles")
            {
                var lifecycle = await CatalogMarkerScreenConsumer.Run(session);
                File.WriteAllText(
                    Path.Combine(directory, "observation.json"),
                    JsonConvert.SerializeObject(
                        new
                        {
                            session,
                            build_guid = Application.buildGUID,
                            unity_version = Application.unityVersion,
                            initialization,
                            lifecycle,
                        }
                    )
                );
                Application.Quit(0);
                return;
            }
            if (initialization == "selective" || initialization.StartsWith("torn-", StringComparison.Ordinal))
            {
                object result = initialization == "selective" ? await SelectiveUpdate(session) : await TornDeployment(session, initialization);
                File.WriteAllText(
                    Path.Combine(directory, "observation.json"),
                    JsonConvert.SerializeObject(
                        new
                        {
                            session,
                            build_guid = Application.buildGUID,
                            unity_version = Application.unityVersion,
                            initialization,
                            selective = initialization == "selective" ? result : null,
                            deployment = initialization.StartsWith("torn-", StringComparison.Ordinal) ? result : null,
                        }
                    )
                );
                Application.Quit(0);
                return;
            }
            AsyncOperationHandle<IResourceLocator> host = default;
            AsyncOperationHandle<List<string>> hostCheck = default;
            CatalogObservation observed;
            CatalogObservation retainedObservation = null;
            try
            {
                if (initialization != "cold")
                {
                    bool autoRelease = initialization == "auto-release";
                    host = Addressables.InitializeAsync(autoRelease);
                    if (initialization == "retained")
                    {
                        await host.Task;
                        hostCheck = Addressables.CheckForCatalogUpdates(false);
                        await hostCheck.Task;
                        if (hostCheck.Status != AsyncOperationStatus.Succeeded || hostCheck.Result.Count != 0)
                            throw new InvalidOperationException("Expected the original remote hash before switching the origin.");
                        string origin = Environment.GetEnvironmentVariable("MCP_CATALOG_ORIGIN");
                        await SignalOrigin(origin, "switch", session);
                        retainedObservation = await AddressableCatalogDiagnostics.ObserveAsync("catalog-marker");
                        await SignalOrigin(origin, "observed", session);
                        if (!hostCheck.IsValid() || hostCheck.Status != AsyncOperationStatus.Succeeded || hostCheck.Result.Count != 0)
                            throw new InvalidOperationException("The application's retained old check changed.");
                        if (apply)
                        {
                            // The application finishes its check before asking the native provider to apply the observed update.
                            Addressables.Release(hostCheck);
                            hostCheck = default;
                        }
                    }
                    else if (host.IsDone)
                        throw new InvalidOperationException("The concurrent initialization case requires an in-flight host operation.");
                }
                observed =
                    retainedObservation != null && !apply ? retainedObservation : await AddressableCatalogDiagnostics.ObserveAsync("catalog-marker", apply);
                if (initialization == "retained" || initialization == "in-flight")
                {
                    if (!host.IsValid() || host.Status != AsyncOperationStatus.Succeeded || host.Result == null)
                        throw new InvalidOperationException("The probe consumed the application's initialization handle.");
                    if (initialization == "retained" && !apply && (!hostCheck.IsValid() || hostCheck.Status != AsyncOperationStatus.Succeeded))
                        throw new InvalidOperationException("The probe consumed the application's catalog check.");
                }
            }
            finally
            {
                if (hostCheck.IsValid())
                    Addressables.Release(hostCheck);
                if (initialization != "auto-release" && host.IsValid())
                    Addressables.Release(host);
            }
            long unrelatedBytes = await RemainingBytes("marker-unrelated");
            long sharedLabelBytes = await RemainingBytes("catalog-marker");
            if (unrelatedBytes <= 0 || sharedLabelBytes != unrelatedBytes)
                throw new InvalidOperationException("The unrelated bundle sharing the marker label must remain downloadable.");
            using (var file = new FileStream(Path.Combine(directory, "observation.json"), FileMode.CreateNew, FileAccess.Write))
            using (var writer = new StreamWriter(file))
                writer.WriteLine(
                    JsonConvert.SerializeObject(
                        new
                        {
                            session,
                            build_guid = Application.buildGUID,
                            unity_version = Application.unityVersion,
                            initialization,
                            retained_observation = retainedObservation,
                            unrelated_bytes_after = unrelatedBytes,
                            shared_label_bytes_after = sharedLabelBytes,
                            observed,
                        }
                    )
                );
            exit = observed.stage == "complete" ? 0 : 2;
        }
        catch (Exception error)
        {
            Debug.LogException(error);
        }
        Application.Quit(exit);
    }
}
