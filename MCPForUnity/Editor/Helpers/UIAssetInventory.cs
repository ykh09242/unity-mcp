using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Globalization;
using System.IO;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEngine.UIElements;

namespace MCPForUnity.Editor.Helpers
{
    /// <summary>Scalar UI inventory observations. Cache admission never limits discovery or page results.</summary>
    [InitializeOnLoad]
    internal static class UIAssetInventory
    {
        private const int MaximumSnapshots = 4;
        private const double LifetimeSeconds = 1800;
        private const string Prefix = "mcp-ui-v1_";
        private static readonly string[] Queries = { "t:VisualTreeAsset", "t:StyleSheet", "t:PanelSettings" };
        private static readonly string[] Types = { "uxml", "uss", "PanelSettings" };
        private static readonly Type[] MainTypes = { typeof(VisualTreeAsset), typeof(StyleSheet), typeof(PanelSettings) };
        private static readonly Dictionary<string, Snapshot> Entries = new Dictionary<string, Snapshot>(StringComparer.Ordinal);
        private static long Epoch,
            AccessOrder,
            RetainedBytes;
        private static bool CleanupScheduled;
        private static double NextCleanup;

        // Private policy/clock seams; native fixtures restore both. These do not limit the requested workload.
        private static long CacheBudgetBytes = 64L * 1024 * 1024;
        private static Func<double> Clock = () => (double)Stopwatch.GetTimestamp() / Stopwatch.Frequency;

        static UIAssetInventory()
        {
            AssemblyReloadEvents.beforeAssemblyReload += Invalidate;
            EditorApplication.quitting += Invalidate;
        }

        internal static void Invalidate()
        {
            Epoch++;
            Entries.Clear();
            RetainedBytes = 0;
            StopCleanupIfEmpty();
        }

        internal static object Read(string scope, string filter, int size, int page, JToken revision)
        {
            string token = null;
            if (revision != null)
            {
                if (revision.Type != JTokenType.String)
                    return new ErrorResponse("invalid_ui_inventory_revision");
                token = revision.Value<string>();
                if (
                    token.Length != Prefix.Length + 32
                    || !token.StartsWith(Prefix, StringComparison.Ordinal)
                    || !Guid.TryParseExact(token.Substring(Prefix.Length), "N", out Guid identity)
                    || identity.ToString("N") != token.Substring(Prefix.Length)
                )
                    return new ErrorResponse("invalid_ui_inventory_revision");
            }
            int mask = FilterMask(filter);
            string key = scope + "\n" + mask.ToString(CultureInfo.InvariantCulture);
            RemoveExpired(Clock());
            long epoch = Epoch;
            Snapshot existing = null;
            if (token != null)
            {
                foreach (var pair in Entries)
                    if (pair.Value.Token == token)
                    {
                        if (pair.Key != key)
                            return new ErrorResponse("ui_inventory_query_mismatch");
                        existing = pair.Value;
                        break;
                    }
                if (existing == null)
                    return new ErrorResponse("stale_ui_inventory");
            }
            else
                Entries.TryGetValue(key, out existing);
            if (existing != null)
            {
                existing.Access = ++AccessOrder;
                return Response(existing.Rows.Count, Page(existing.Rows, size, page), size, page, existing.Token, existing.ExpiresAt);
            }

            long start = PaginationBounds.StartIndex(page, size);
            var selected = new JArray();
            var rows = new List<Row>();
            long bytes = 128L + key.Length * 2L;
            bool retain = bytes <= CacheBudgetBytes;
            int total = 0;
            string[] folders = { scope };
            for (int type = 0; type < Queries.Length; type++)
            {
                if ((mask & (1 << type)) == 0)
                    continue;
                AssetSearchSnapshots.Observe("ui_find_assets");
                string[] guids = AssetDatabase.FindAssets(Queries[type], folders);
                Array.Sort(guids, StringComparer.Ordinal);
                if (epoch != Epoch)
                    return new ErrorResponse("stale_ui_inventory");
                foreach (string guid in guids)
                {
                    string path;
                    try
                    {
                        AssetSearchSnapshots.Observe("ui_resolve_guid");
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
                    // FindAssets also finds embedded subassets, including a UXML's inline StyleSheet.
                    // The list identifies standalone UI assets by their main type, without loading them.
                    Type mainType = AssetDatabase.GetMainAssetTypeAtPath(path);
                    if (mainType == null || !MainTypes[type].IsAssignableFrom(mainType))
                        continue;
                    Row row = null;
                    if (total >= start && selected.Count < size)
                    {
                        row = new Row(path, Types[type]);
                        selected.Add(row.Copy());
                    }
                    total++;
                    if (retain)
                    {
                        // Conservative accounting includes row/list overhead and UTF-16 path storage.
                        bytes += 128L + path.Length * 2L;
                        if (bytes > CacheBudgetBytes)
                        {
                            retain = false;
                            rows = null;
                        }
                        else
                        {
                            MakeRoom(bytes);
                            rows.Add(row ?? new Row(path, Types[type]));
                        }
                    }
                    if (epoch != Epoch)
                        return new ErrorResponse("stale_ui_inventory");
                }
            }
            if (epoch != Epoch)
                return new ErrorResponse("stale_ui_inventory");
            string expiresAt = null;
            if (retain)
            {
                RemoveExpired(Clock());
                MakeRoom(bytes);
                while (Entries.Count >= MaximumSnapshots)
                    Remove(Oldest());
                token = Prefix + Guid.NewGuid().ToString("N");
                expiresAt = DateTime.UtcNow.AddSeconds(LifetimeSeconds).ToString("O", CultureInfo.InvariantCulture);
                Entries.Add(key, new Snapshot(rows, token, expiresAt, bytes, Clock() + LifetimeSeconds, ++AccessOrder));
                RetainedBytes += bytes;
                if (!CleanupScheduled)
                {
                    CleanupScheduled = true;
                    NextCleanup = Clock() + 1;
                    EditorApplication.update += CleanupIdle;
                }
            }
            return Response(total, selected, size, page, token, expiresAt);
        }

        private static int FilterMask(string filter)
        {
            if (string.IsNullOrEmpty(filter))
                return 7;
            if (filter.Equals("uxml", StringComparison.OrdinalIgnoreCase) || filter.Equals("VisualTreeAsset", StringComparison.OrdinalIgnoreCase))
                return 1;
            if (filter.Equals("uss", StringComparison.OrdinalIgnoreCase) || filter.Equals("StyleSheet", StringComparison.OrdinalIgnoreCase))
                return 2;
            return filter.Equals("PanelSettings", StringComparison.OrdinalIgnoreCase) ? 4 : 0;
        }

        private static JArray Page(List<Row> rows, int size, int page)
        {
            var selected = new JArray();
            long start = PaginationBounds.StartIndex(page, size);
            if (start < rows.Count)
                for (int i = (int)start; i < rows.Count && selected.Count < size; i++)
                    selected.Add(rows[i].Copy());
            return selected;
        }

        private static object Response(int total, JArray selected, int size, int page, string token, string expiresAt) =>
            new SuccessResponse(
                $"Found {total} UI asset(s). Returning page {page} ({selected.Count} items).",
                new
                {
                    total,
                    pageSize = size,
                    pageNumber = page,
                    maxPageSize = PaginationBounds.MaxPageSize,
                    assets = selected,
                    inventory_revision = token,
                    inventory_retained = token != null,
                    inventory_expires_at = expiresAt,
                    inventory_order = "type_then_guid",
                    inventory_complete = true,
                    inventory_note = token == null
                        ? "Complete uncached discovery; offset pages are not bound across asset changes."
                        : "Observed inventory; pass inventory_revision to reject pages after invalidation, expiry or eviction.",
                }
            );

        private static string Oldest()
        {
            string key = null;
            long access = long.MaxValue;
            foreach (var pair in Entries)
                if (pair.Value.Access < access)
                {
                    key = pair.Key;
                    access = pair.Value.Access;
                }
            return key;
        }

        private static void MakeRoom(long pending)
        {
            while (Entries.Count > 0 && RetainedBytes + pending > CacheBudgetBytes)
                Remove(Oldest());
        }

        private static void RemoveExpired(double now)
        {
            while (true)
            {
                string expired = null;
                foreach (var pair in Entries)
                    if (now >= pair.Value.Deadline)
                    {
                        expired = pair.Key;
                        break;
                    }
                if (expired == null)
                    return;
                Remove(expired);
            }
        }

        private static void Remove(string key)
        {
            RetainedBytes -= Entries[key].Bytes;
            Entries.Remove(key);
            StopCleanupIfEmpty();
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

        private sealed class Row
        {
            private readonly string path,
                type;

            internal Row(string path, string type)
            {
                this.path = path;
                this.type = type;
            }

            internal JObject Copy() =>
                new JObject
                {
                    ["path"] = path,
                    ["type"] = type,
                    ["name"] = Path.GetFileName(path),
                };
        }

        private sealed class Snapshot
        {
            internal readonly List<Row> Rows;
            internal readonly string Token,
                ExpiresAt;
            internal readonly long Bytes;
            internal readonly double Deadline;
            internal long Access;

            internal Snapshot(List<Row> rows, string token, string expiresAt, long bytes, double deadline, long access)
            {
                Rows = rows;
                Token = token;
                ExpiresAt = expiresAt;
                Bytes = bytes;
                Deadline = deadline;
                Access = access;
            }
        }
    }
}
