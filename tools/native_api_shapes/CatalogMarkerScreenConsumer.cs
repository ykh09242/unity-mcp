using System;
using System.Collections.Generic;
using System.Threading.Tasks;
using MCPForUnity.Runtime;
using UnityEngine;
using UnityEngine.AddressableAssets;
using UnityEngine.Profiling;
using UnityEngine.ResourceManagement.AsyncOperations;
using UnityEngine.SceneManagement;

public sealed class CatalogMarkerScreenConsumer : MonoBehaviour
{
    private const string Key = "marker-text";

    private sealed class Lease : IDisposable
    {
        private AsyncOperationHandle<TextAsset> handle;
        private string token;

        public TextAsset Asset => handle.IsValid() ? handle.Result : null;

        public async Task Load(string owner)
        {
            handle = Addressables.LoadAssetAsync<TextAsset>(Key);
            await handle.Task;
            if (handle.Status != AsyncOperationStatus.Succeeded || Asset == null)
                throw new InvalidOperationException("Screen consumer failed to acquire its marker.");
            token = ReferenceGraphRuntime.Acquire("addressables", Key, Asset, owner);
        }

        public void Dispose()
        {
            if (!handle.IsValid())
                return;
            Addressables.Release(handle);
            handle = default;
            ReferenceGraphRuntime.Release(token);
        }
    }

    private readonly Lease lease = new Lease();
    private bool omitRelease;

    private void OnDestroy()
    {
        if (!omitRelease)
            lease.Dispose();
    }

    private static CatalogMarkerScreenConsumer CreateConsumer(Scene screen, string name)
    {
        var target = new GameObject(name);
        SceneManager.MoveGameObjectToScene(target, screen);
        return target.AddComponent<CatalogMarkerScreenConsumer>();
    }

    public static async Task<object> Run(string session)
    {
        var balanced = await Exercise("balanced", session);
        var omitted = await Exercise("omitted", session);
        return new { balanced, omitted };
    }

    private static async Task<object> Exercise(string arm, string session)
    {
        var events = new List<ReferenceGraphRuntime.HandleObservation>();
        var checkpoints = new List<object>();
        using var capture = ReferenceGraphRuntime.ObserveHandles(events.Add);
        void Sample(int cycle, string phase, bool sceneLoaded, string marker = null)
        {
            long? allocated = null;
            string memoryError = null;
            try
            {
                long sample = Profiler.GetTotalAllocatedMemoryLong();
                if (sample > 0)
                    allocated = sample;
                else
                    memoryError = "counter_unavailable";
            }
            catch (Exception error)
            {
                memoryError = error.GetType().Name;
            }
            checkpoints.Add(
                new
                {
                    cycle,
                    phase,
                    event_count = events.Count,
                    scene_loaded = sceneLoaded,
                    marker,
                    unity_allocated_bytes = allocated,
                    memory_error = memoryError,
                }
            );
        }
        var leases = new List<Lease>();
        Sample(-1, "baseline", false);
        try
        {
            for (int cycle = 0; cycle < 4; cycle++)
            {
                string screenName = arm + "-" + cycle;
                var screen = SceneManager.CreateScene(screenName);
                var first = CreateConsumer(screen, "First");
                var second = CreateConsumer(screen, "Second");
                leases.Add(first.lease);
                leases.Add(second.lease);
                await first.lease.Load(screenName + "/First");
                await second.lease.Load(screenName + "/Second");
                if (first.lease.Asset != second.lease.Asset || second.lease.Asset.text != "old-" + session)
                    throw new InvalidOperationException("The two consumers did not acquire the same original marker.");
                Sample(cycle, "both_open", screen.isLoaded, second.lease.Asset.text);
                Destroy(first.gameObject);
                while (first != null)
                    await Task.Yield();
                // The second native handle still owns the asset after the first consumer closes.
                Sample(cycle, "first_closed", screen.isLoaded, second.lease.Asset.text);
                second.omitRelease = arm == "omitted" && cycle == 1;
                var unload = SceneManager.UnloadSceneAsync(screen);
                while (!unload.isDone)
                    await Task.Yield();
                Sample(cycle, "screen_closed", screen.IsValid() && screen.isLoaded);
            }
        }
        finally
        {
            foreach (var lease in leases)
                lease.Dispose();
        }
        Sample(3, "cleanup", false);
        return new { events, checkpoints };
    }
}
