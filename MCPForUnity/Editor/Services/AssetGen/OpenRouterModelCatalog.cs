using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Threading;
using System.Threading.Tasks;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Services.AssetGen.Http;
using MCPForUnity.Editor.Services.AssetGen.Providers;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using UnityEngine;

namespace MCPForUnity.Editor.Services.AssetGen
{
    public static class OpenRouterModelCatalog
    {
        private const string Api = "https://openrouter.ai/api/v1/images/models";
        private static readonly TimeSpan Lifetime = TimeSpan.FromHours(24);

        private sealed class Snapshot
        {
            public int Version = 1;
            public DateTime CheckedAt;
            public List<ModelEntry> Entries;
        }

        private sealed class EndpointCheck
        {
            internal Task<JObject> Task;
            internal DateTime Expires;
        }

        private static readonly object EndpointLock = new();
        private static readonly Dictionary<string, EndpointCheck> EndpointChecks = new();
        private static readonly TimeSpan EndpointLifetime = TimeSpan.FromMinutes(5);
        private static Snapshot snapshot;
        private static bool loaded,
            fromDisk,
            isolated;
        private static Task<bool> refresh;
        private static DateTime attempted;
        public static event Action Changed;
        public static string LastError { get; private set; }
        internal static IHttpTransport TransportOverrideForTests;
        internal static string CachePathOverrideForTests;
        internal static Func<DateTime> UtcNow = () => DateTime.UtcNow;
        private static string CachePath =>
            CachePathOverrideForTests ?? Path.Combine(Path.GetDirectoryName(Application.dataPath), "Library", "MCPForUnity", "openrouter-image-catalog.json");

        public static bool IsRefreshing => refresh != null && !refresh.IsCompleted;
        public static string Source
        {
            get
            {
                Load();
                return snapshot == null ? "bundled"
                    : fromDisk ? "cache"
                    : "live";
            }
        }
        public static string CheckedAt
        {
            get
            {
                Load();
                return snapshot?.CheckedAt.ToString("O");
            }
        }
        public static bool IsStale
        {
            get
            {
                Load();
                return snapshot == null || UtcNow() - snapshot.CheckedAt >= Lifetime;
            }
        }

        internal static bool TryGet(out IReadOnlyList<ModelEntry> entries)
        {
            Load();
            entries = snapshot?.Entries;
            return entries != null;
        }

        public static Task<bool> RefreshAsync(bool force = false)
        {
            Load();
            if (IsRefreshing)
                return refresh;
            if (!force && !IsStale)
                return Task.FromResult(true);
            if (isolated && TransportOverrideForTests == null)
                return Task.FromResult(false);
            if (!force && UtcNow() - attempted < TimeSpan.FromMinutes(2))
                return Task.FromResult(false);
            attempted = UtcNow();
            return refresh = RefreshCore();
        }

        private static async Task<bool> RefreshCore()
        {
            try
            {
                using var ct = new CancellationTokenSource(TimeSpan.FromSeconds(30));
                var entries = await Discover(ct.Token);
                snapshot = new Snapshot { CheckedAt = UtcNow(), Entries = entries };
                lock (EndpointLock)
                    EndpointChecks.Clear();
                fromDisk = false;
                LastError = null;
                Save();
                Changed?.Invoke();
                return true;
            }
            catch (Exception error)
            {
                LastError = error is OperationCanceledException ? "OpenRouter catalog refresh timed out. Previous catalog retained." : error.Message;
                return false;
            }
        }

        private static async Task<List<ModelEntry>> Discover(CancellationToken ct)
        {
            var json = await Get(Api, ct);
            if (!(json["data"] is JArray data))
                throw new InvalidOperationException("Invalid OpenRouter model catalog.");
            var entries = new List<ModelEntry>();
            foreach (JObject model in data)
            {
                string id = (string)model["id"];
                if (
                    !FalModelSchema.SafeId(id)
                    || model["architecture"]?["output_modalities"]?.Values<string>().Contains("image") != true
                    || model["architecture"]?["input_modalities"]?.Values<string>().Contains("text") != true
                )
                    continue;
                var parameters = model["supported_parameters"] as JObject;
                if (parameters == null || !RasterFormat(parameters, out _))
                    continue;
                var modes = Modes(parameters);
                if (modes.Length == 0)
                    continue;
                entries.Add(
                    new ModelEntry
                    {
                        Id = id,
                        Label = (string)model["name"] ?? id,
                        Kind = "image",
                        Provider = "openrouter",
                        FromRefresh = true,
                        Modes = modes,
                        UseCase = "Image generation",
                        RouterParameters = parameters,
                        ModelUrl = "https://openrouter.ai/" + id,
                    }
                );
            }
            var bundled = AssetGenModelCatalog.Bundled("openrouter", "image").Select(e => e.Id).ToArray();
            return entries.GroupBy(e => e.Id).Select(g => g.First()).OrderBy(e => bundled.Contains(e.Id) ? 0 : 1).ToList();
        }

        internal static async Task<ModelEntry> VerifyForGeneration(string id, string mode, CancellationToken ct)
        {
            if (!FalModelSchema.SafeId(id))
                throw new InvalidOperationException("Invalid OpenRouter model ID.");
            ct.ThrowIfCancellationRequested();
            if (!await WaitForCaller(RefreshAsync(), ct))
                throw new InvalidOperationException(LastError ?? "OpenRouter catalog is unavailable. Retry after refreshing models.");
            var observed = snapshot;
            var model = observed?.Entries.FirstOrDefault(e => e.Id == id);
            if (model == null)
                throw new InvalidOperationException($"OpenRouter model '{id}' is unavailable or incompatible. Refresh models and choose another model.");
            mode = string.IsNullOrEmpty(mode) ? "text" : mode;
            var verification = GetSharedEndpoints(id);
            var json = await WaitForCaller(verification.Task, ct);
            if (!ReferenceEquals(observed, snapshot))
                throw new InvalidOperationException("OpenRouter catalog changed during verification. Retry with the current model selection.");
            var endpoints = (JArray)json["endpoints"];
            // Each job owns its capabilities; neither mode selection nor caller mutation may alter cached data.
            var entry = JsonConvert.DeserializeObject<ModelEntry>(JsonConvert.SerializeObject(model));
            foreach (JObject endpoint in endpoints)
            {
                var parameters = endpoint["supported_parameters"] as JObject;
                string tag = (string)endpoint["provider_tag"];
                // Pin the chosen capabilities; a fallback provider may accept different fields.
                if (parameters == null || string.IsNullOrEmpty(tag) || !Modes(parameters).Contains(mode) || !RasterFormat(parameters, out string format))
                    continue;
                entry.RouterProviderTag = tag;
                entry.RouterParameters = (JObject)parameters.DeepClone();
                entry.OutputFormat = format;
                entry.Modes = Modes(parameters);
                entry.VerifiedAt = (verification.Expires - EndpointLifetime).ToString("O");
                Load();
                int index = snapshot?.Entries.FindIndex(e => e.Id == id) ?? -1;
                if (index >= 0 && JsonConvert.SerializeObject(snapshot.Entries[index]) != JsonConvert.SerializeObject(entry))
                {
                    snapshot.Entries[index] = JsonConvert.DeserializeObject<ModelEntry>(JsonConvert.SerializeObject(entry));
                    Save();
                    Changed?.Invoke();
                }
                return entry;
            }
            throw new InvalidOperationException($"OpenRouter model '{id}' has no compatible '{mode}' endpoint.");
        }

        private static EndpointCheck GetSharedEndpoints(string id)
        {
            lock (EndpointLock)
            {
                if (
                    EndpointChecks.TryGetValue(id, out var check)
                    && !check.Task.IsFaulted
                    && !check.Task.IsCanceled
                    && (!check.Task.IsCompleted || UtcNow() < check.Expires)
                )
                    return check;
                foreach (
                    string expired in EndpointChecks
                        .Where(pair =>
                            pair.Value.Task.IsCompleted && (pair.Value.Task.IsFaulted || pair.Value.Task.IsCanceled || UtcNow() >= pair.Value.Expires)
                        )
                        .Select(pair => pair.Key)
                        .ToArray()
                )
                    EndpointChecks.Remove(expired);
                if (EndpointChecks.Count >= 128)
                {
                    string victim = EndpointChecks
                        .Where(pair => pair.Value.Task.IsCompleted)
                        .OrderBy(pair => pair.Value.Expires)
                        .Select(pair => pair.Key)
                        .FirstOrDefault();
                    if (victim == null)
                        throw new InvalidOperationException("Too many model verifications are in progress. Retry shortly.");
                    EndpointChecks.Remove(victim);
                }
                check = new EndpointCheck { Task = FetchEndpoints(id), Expires = UtcNow() + EndpointLifetime };
                EndpointChecks[id] = check;
                return check;
            }
        }

        private static async Task<JObject> FetchEndpoints(string id)
        {
            using var timeout = new CancellationTokenSource(TimeSpan.FromSeconds(30));
            var json = await Get(Api + "/" + id + "/endpoints", timeout.Token);
            if ((string)json["id"] != id || !(json["endpoints"] is JArray))
                throw new InvalidOperationException("Invalid OpenRouter endpoint catalog.");
            return json;
        }

        private static async Task<T> WaitForCaller<T>(Task<T> work, CancellationToken ct)
        {
            ct.ThrowIfCancellationRequested();
            if (!ct.CanBeCanceled)
                return await work;
            var canceled = new TaskCompletionSource<bool>();
            using (ct.Register(() => canceled.TrySetResult(true)))
            {
                await Task.WhenAny(work, canceled.Task);
                ct.ThrowIfCancellationRequested();
                return await work;
            }
        }

        private static string[] Modes(JObject parameters)
        {
            var reference = parameters["input_references"];
            int minimum = (int?)reference?["min"] ?? 0,
                maximum = (int?)reference?["max"] ?? 0;
            var modes = new List<string>();
            if (minimum == 0)
                modes.Add("text");
            if (reference != null && minimum <= 1 && maximum >= 1)
                modes.Add("image");
            return modes.ToArray();
        }

        private static bool RasterFormat(JObject parameters, out string format)
        {
            format = null;
            if (parameters["output_format"] == null)
                return true;
            var values = parameters["output_format"]?["values"]?.Values<string>().ToArray();
            format = new[] { "png", "jpeg", "jpg" }.FirstOrDefault(f => values?.Contains(f) == true);
            return format != null;
        }

        private static async Task<JObject> Get(string url, CancellationToken ct)
        {
            var result = await (TransportOverrideForTests ?? new UnityWebRequestTransport()).SendAsync(new HttpRequestSpec { Method = "GET", Url = url }, ct);
            if (result == null || result.Status < 200 || result.Status >= 300)
                throw new InvalidOperationException($"OpenRouter catalog request failed (HTTP {result?.Status}). Previous catalog retained.");
            return BoundedJson.ParseObject(ProviderHttp.BodyText(result));
        }

        private static void Load()
        {
            if (loaded)
                return;
            loaded = true;
            try
            {
                GuardedFileSystem.CheckPath(CachePath);
                if (!File.Exists(CachePath))
                    return;
                if (new FileInfo(CachePath).Length > FalModelCatalog.MaxCacheBytes)
                {
                    McpLog.Warn(
                        $"OpenRouter model cache exceeds {FalModelCatalog.MaxCacheBytes / (1024 * 1024)} MB and was ignored; models are re-fetched after each reload."
                    );
                    return;
                }
                var candidate = JsonConvert.DeserializeObject<Snapshot>(File.ReadAllText(CachePath));
                if (
                    candidate?.Version != 1
                    || candidate.Entries == null
                    || candidate.CheckedAt.Kind != DateTimeKind.Utc
                    || candidate.CheckedAt > UtcNow().AddMinutes(5)
                    || candidate.Entries.Any(e => e == null || e.Kind != "image" || e.Provider != "openrouter" || !FalModelSchema.SafeId(e.Id))
                )
                    return;
                snapshot = candidate;
                fromDisk = true;
            }
            catch { /* Ignore corrupt caches. */ }
        }

        private static void Save()
        {
            try
            {
                GuardedFileSystem.WriteAllTextAtomic(CachePath, JsonConvert.SerializeObject(snapshot));
            }
            catch (Exception ex)
            {
                // Persistence failure must not discard a successful in-memory refresh.
                McpLog.Warn("Could not persist the model catalog: " + ex.Message);
            }
        }

        internal static void ReloadCacheForTests()
        {
            snapshot = null;
            loaded = false;
        }

        internal static void ResetForTests(bool isolate = false)
        {
            snapshot = null;
            loaded = false;
            fromDisk = false;
            isolated = isolate;
            refresh = null;
            lock (EndpointLock)
                EndpointChecks.Clear();
            LastError = null;
            attempted = DateTime.MinValue;
            TransportOverrideForTests = null;
            Changed = null;
            UtcNow = () => DateTime.UtcNow;
            CachePathOverrideForTests = isolate ? Path.Combine(Path.GetTempPath(), "unused_or_catalog_" + Guid.NewGuid().ToString("N"), "catalog.json") : null;
        }
    }
}
