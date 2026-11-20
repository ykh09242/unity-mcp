using System;
using System.Collections.Generic;
using System.IO;
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
            var cache = Caching.AddCache(Directory.CreateDirectory(Path.Combine(root, "bundle-cache")).FullName);
            if (!cache.valid)
                throw new InvalidOperationException("Could not open the owned fixture cache.");
            Caching.currentCacheForWriting = cache;
            bool apply = Environment.GetEnvironmentVariable("MCP_CATALOG_APPLY") == "1";
            string initialization = Environment.GetEnvironmentVariable("MCP_CATALOG_INIT");
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
