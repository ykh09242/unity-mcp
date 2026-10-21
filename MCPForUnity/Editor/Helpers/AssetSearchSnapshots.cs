using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Text;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using UnityEditor;

namespace MCPForUnity.Editor.Helpers
{
    // Editor-main-thread only. Limits cover retained rows, not Unity's initial FindAssets array or process heap.
    [InitializeOnLoad]
    internal static class AssetSearchSnapshots
    {
        private const int MaximumSnapshots = 4;
        private const int MaximumRows = 250000;
        private const int MaximumStringCharacters = 4096;
        private const int MaximumRowBytes = 64 * 1024;
        private const int MaximumPayloadBytes = 64 * 1024 * 1024;
        private const double LifetimeSeconds = 1800;
        private const string TokenPrefix = "mcp-as-v1_";
        private static readonly string Session = Guid.NewGuid().ToString("N");
        private static readonly Dictionary<string, Snapshot> Entries = new Dictionary<string, Snapshot>(StringComparer.Ordinal);
        private static long Epoch;
        private static long AccessOrder;
        private static int RetainedBytes;
        private static bool CleanupScheduled;
        private static double NextCleanup;

        // Inert test seams; fixtures restore previous values in finally. Neither is a public command/API.
        private static Action<string> OperationObserved = null;
        private static Func<double> Clock = () => (double)Stopwatch.GetTimestamp() / Stopwatch.Frequency;

        static AssetSearchSnapshots()
        {
            AssemblyReloadEvents.beforeAssemblyReload += Invalidate;
        }

        internal static void Observe(string operation) => OperationObserved?.Invoke(operation);

        internal static void Invalidate()
        {
            Epoch++;
            Entries.Clear();
            RetainedBytes = 0;
            StopCleanupIfEmpty();
        }

        internal static bool IsToken(string token) =>
            token != null
            && token.Length == 75
            && token.StartsWith(TokenPrefix, StringComparison.Ordinal)
            && string.Equals(token, token.ToLowerInvariant(), StringComparison.Ordinal)
            && token[42] == '_'
            && Guid.TryParseExact(token.Substring(10, 32), "N", out _)
            && Guid.TryParseExact(token.Substring(43, 32), "N", out _);

        internal static ErrorResponse ValidateOptions(JObject parameters, bool metadata)
        {
            var token = parameters["searchSnapshot"];
            if (!metadata && token != null)
                return new ErrorResponse("invalid_search_snapshot", new { reason = "searchSnapshot requires search_metadata_v1" });
            if (!metadata)
                return null;
            var preview = parameters["generatePreview"];
            if (preview != null && preview.Type != JTokenType.Null && (preview.Type != JTokenType.Boolean || preview.Value<bool>()))
                return new ErrorResponse("invalid_metadata_search", new { reason = "Metadata search does not generate previews" });
            if (token != null && (token.Type != JTokenType.String || !IsToken(token.Value<string>())))
                return new ErrorResponse("invalid_search_snapshot");
            foreach (string name in new[] { "path", "searchPattern", "filterType", "filterDateAfter" })
            {
                var value = parameters[name];
                if (
                    value != null
                    && value.Type != JTokenType.Null
                    && (value.Type != JTokenType.String || value.Value<string>().Length > MaximumStringCharacters)
                )
                    return LimitError();
            }
            return null;
        }

        internal static object Search(string query, string[] scope, DateTime? cutoff, int pageSize, int pageNumber, string token)
        {
            if (query.Length > MaximumStringCharacters)
                return LimitError();
            long epoch = Epoch;
            double started = Clock();
            RemoveExpired(started);
            string key = new JArray(query, scope == null ? null : scope[0], cutoff?.Ticks.ToString(CultureInfo.InvariantCulture), pageSize).ToString(
                Formatting.None
            );
            if (token != null)
            {
                if (!Entries.TryGetValue(token, out Snapshot existing))
                    return new ErrorResponse("stale_search_snapshot");
                if (!string.Equals(existing.Key, key, StringComparison.Ordinal))
                    return new ErrorResponse("search_snapshot_query_mismatch");
                return Page(existing, pageSize, pageNumber, epoch);
            }

            var rows = new List<JObject>();
            int bytes = Encoding.UTF8.GetByteCount(key);
            MakeRoom(bytes);
            string expiresAtUtc = DateTime.UtcNow.AddSeconds(LifetimeSeconds).ToString("o", CultureInfo.InvariantCulture);
            Observe("find_assets");
            string[] guids = AssetDatabase.FindAssets(query, scope);
            foreach (string guid in guids)
            {
                string path;
                try
                {
                    path = AssetPathUtility.GetAssetPathFromGuid(guid, allowPackages: true);
                }
                catch (ArgumentException)
                {
                    continue;
                }
                catch (InvalidOperationException)
                {
                    continue;
                }
                if (string.IsNullOrEmpty(path))
                    continue;
                if (path.Length > MaximumStringCharacters || guid.Length > MaximumStringCharacters)
                    return LimitError();
                Observe("read_write_time");
                DateTime? written = AssetPathUtility.GetLastWriteTimeUtc(path);
                if (cutoff.HasValue && (!written.HasValue || written.Value <= cutoff.Value))
                    continue;
                // Refuse before allocating or appending the next metadata row.
                if (rows.Count >= MaximumRows)
                    return LimitError();
                string type = AssetDatabase.GetMainAssetTypeAtPath(path)?.FullName ?? "Unknown";
                string name = Path.GetFileNameWithoutExtension(path);
                string fileName = Path.GetFileName(path);
                if (type.Length > MaximumStringCharacters || name.Length > MaximumStringCharacters || fileName.Length > MaximumStringCharacters)
                    return LimitError();
                var row = new JObject
                {
                    ["path"] = path,
                    ["guid"] = guid,
                    ["assetType"] = type,
                    ["name"] = name,
                    ["fileName"] = fileName,
                    ["isFolder"] = AssetDatabase.IsValidFolder(path),
                    ["instanceID"] = 0,
                    ["lastWriteTimeUtc"] = written?.ToString("o", CultureInfo.InvariantCulture),
                    ["previewBase64"] = null,
                    ["previewWidth"] = 0,
                    ["previewHeight"] = 0,
                    ["metadataOnly"] = true,
                    ["instanceIDAvailable"] = false,
                };
                if (!TryAppendRow(rows, row, ref bytes))
                    return LimitError();
                if (epoch != Epoch || Clock() >= started + LifetimeSeconds)
                    return new ErrorResponse("stale_search_snapshot");
            }
            if (epoch != Epoch || Clock() >= started + LifetimeSeconds)
                return new ErrorResponse("stale_search_snapshot");
            RemoveExpired(Clock());
            while (Entries.Count >= MaximumSnapshots || RetainedBytes + bytes > MaximumPayloadBytes)
                Remove(Entries.OrderBy(pair => pair.Value.LastAccess).First().Key);
            string newToken = TokenPrefix + Session + "_" + Guid.NewGuid().ToString("N");
            var snapshot = new Snapshot(newToken, key, rows, bytes, started + LifetimeSeconds, expiresAtUtc);
            Entries.Add(newToken, snapshot);
            RetainedBytes += bytes;
            if (!CleanupScheduled)
            {
                CleanupScheduled = true;
                NextCleanup = Clock() + 1;
                EditorApplication.update += CleanupIdle;
            }
            return Page(snapshot, pageSize, pageNumber, epoch);
        }

        private static bool TryAppendRow(List<JObject> rows, JObject row, ref int bytes)
        {
            if (rows.Count >= MaximumRows)
                return false;
            int size = Encoding.UTF8.GetByteCount(row.ToString(Formatting.None));
            if (size > MaximumRowBytes || size > MaximumPayloadBytes - bytes)
                return false;
            MakeRoom(bytes + size);
            bytes += size;
            rows.Add(row);
            return true;
        }

        private static object Page(Snapshot snapshot, int size, int page, long epoch)
        {
            long start = PaginationBounds.StartIndex(page, size);
            var rows = new JArray();
            if (start < snapshot.Rows.Count)
            {
                for (int index = (int)start; index < snapshot.Rows.Count && rows.Count < size; index++)
                    rows.Add(snapshot.Rows[index].DeepClone());
            }
            if (epoch != Epoch || Clock() >= snapshot.Deadline)
            {
                Remove(snapshot.Token);
                return new ErrorResponse("stale_search_snapshot");
            }
            snapshot.LastAccess = ++AccessOrder;
            return new SuccessResponse(
                $"Found {snapshot.Rows.Count} asset(s). Returning page {page} ({rows.Count} assets).",
                new
                {
                    totalAssets = snapshot.Rows.Count,
                    pageSize = size,
                    pageNumber = page,
                    maxPageSize = PaginationBounds.MaxPageSize,
                    previewBase64Bytes = 0,
                    assets = rows,
                    metadataOnly = true,
                    searchSnapshot = snapshot.Token,
                    snapshotContractVersion = 1,
                    expiresAtUtc = snapshot.ExpiresAtUtc,
                    hasMore = start + rows.Count < snapshot.Rows.Count,
                }
            );
        }

        private static void RemoveExpired(double now)
        {
            foreach (string token in Entries.Where(pair => now >= pair.Value.Deadline).Select(pair => pair.Key).ToArray())
                Remove(token);
        }

        private static void CleanupIdle()
        {
            double now = Clock();
            if (now < NextCleanup)
                return;
            NextCleanup = now + 1;
            RemoveExpired(now);
        }

        private static void StopCleanupIfEmpty()
        {
            if (Entries.Count != 0 || !CleanupScheduled)
                return;
            EditorApplication.update -= CleanupIdle;
            CleanupScheduled = false;
        }

        // Include the in-progress row payload when bounding aggregate accounted bytes.
        private static void MakeRoom(int pendingBytes)
        {
            while (Entries.Count > 0 && RetainedBytes + pendingBytes > MaximumPayloadBytes)
                Remove(Entries.OrderBy(pair => pair.Value.LastAccess).First().Key);
        }

        private static void Remove(string token)
        {
            if (Entries.TryGetValue(token, out Snapshot snapshot))
            {
                RetainedBytes -= snapshot.Bytes;
                Entries.Remove(token);
                StopCleanupIfEmpty();
            }
        }

        private static ErrorResponse LimitError() =>
            new ErrorResponse("search_snapshot_limit_exceeded", new { reason = "Narrow the search query or folder scope; snapshots are never truncated." });

        private sealed class Snapshot
        {
            internal readonly string Token;
            internal readonly string Key;
            internal readonly List<JObject> Rows;
            internal readonly int Bytes;
            internal readonly double Deadline;
            internal readonly string ExpiresAtUtc;
            internal long LastAccess;

            internal Snapshot(string token, string key, List<JObject> rows, int bytes, double deadline, string expiresAtUtc)
            {
                Token = token;
                Key = key;
                Rows = rows;
                Bytes = bytes;
                Deadline = deadline;
                ExpiresAtUtc = expiresAtUtc;
            }
        }
    }

    internal sealed class AssetSearchSnapshotInvalidation : AssetPostprocessor
    {
        private static void OnPostprocessAllAssets(
            string[] importedAssets,
            string[] deletedAssets,
            string[] movedAssets,
            string[] movedFromAssetPaths,
            bool didDomainReload
        )
        {
            if (didDomainReload || importedAssets.Length > 0 || deletedAssets.Length > 0 || movedAssets.Length > 0 || movedFromAssetPaths.Length > 0)
            {
                AssetSearchSnapshots.Invalidate();
                UIAssetInventory.Invalidate();
            }
        }
    }
}
