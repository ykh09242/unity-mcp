using System;
using System.Collections.Generic;
using System.IO;
using System.Text;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Runtime.Helpers;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEngine;

namespace MCPForUnity.Editor.Tools
{
    /// <summary>Owned disk snapshots retain value evidence, never live Unity objects or full issue trees.</summary>
    internal static class ReferenceSnapshotStore
    {
        private const string Prefix = "mcp-ref-snapshot-v1_";
        private const double IdleSeconds = 600;
        private const long MaximumBytes = 8L * 1024 * 1024 * 1024;
        private static readonly Dictionary<string, LinkedListNode<Snapshot>> Entries = new(StringComparer.Ordinal);
        private static readonly LinkedList<Snapshot> Recent = new();
        private static long retainedBytes;
        private static double sweepAt;

        private sealed class Snapshot : IDisposable
        {
            internal string Cursor;
            internal string Scope;
            internal JObject Metadata;
            internal FileStream File;
            internal long[] Offsets;
            internal double Touched;

            public void Dispose() => File?.Dispose();
        }

        internal static string Scope(string scope, string path, int objects, int work) => new JArray(scope, path, objects, work).ToString(Formatting.None);

        internal static void Capture(JObject data, List<JObject> issues, string scope)
        {
            var snapshot = new Snapshot();
            try
            {
                string root = Path.GetDirectoryName(Application.dataPath);
                string directory = SafePathUtility.ResolveWithinRoot(root, "Temp/McpReferenceSnapshots");
                Directory.CreateDirectory(directory);
                snapshot.Cursor = Prefix + Guid.NewGuid().ToString("N");
                string path = SafePathUtility.ResolveWithinRoot(root, Path.Combine(directory, snapshot.Cursor + ".snapshot"));
                snapshot.File = new FileStream(path, FileMode.CreateNew, FileAccess.ReadWrite, FileShare.None, 64 * 1024, FileOptions.DeleteOnClose);
                SafePathUtility.ResolveWithinRoot(root, path);
                snapshot.Offsets = new long[issues.Count];
                using (var writer = new BinaryWriter(snapshot.File, Encoding.UTF8, true))
                {
                    for (int i = 0; i < issues.Count; i++)
                    {
                        snapshot.Offsets[i] = snapshot.File.Position;
                        writer.Write(issues[i].ToString(Formatting.None));
                        if (snapshot.File.Position > MaximumBytes)
                            throw new IOException("Snapshot retention budget exceeded; use live pages.");
                    }
                    writer.Flush();
                }
                snapshot.Scope = scope;
                snapshot.Touched = EditorApplication.timeSinceStartup;
                data["referenceCursor"] = snapshot.Cursor;
                data["freshness"] = "captured";
                data["capturedAtUtc"] = DateTimeOffset.UtcNow.ToString("O");
                data["snapshotRetention"] =
                    "10 minutes idle; up to 64 recent snapshots and 8 GiB of temporary evidence. Expired cursors require a fresh capture.";
                snapshot.Metadata = (JObject)data.DeepClone();
                snapshot.Metadata.Remove("issues");
                while (Recent.Count != 0 && (Recent.Count >= 64 || retainedBytes + snapshot.File.Length > MaximumBytes))
                    Remove(Recent.First);
                Entries.Add(snapshot.Cursor, Recent.AddLast(snapshot));
                retainedBytes += snapshot.File.Length;
                EditorApplication.update -= Expire;
                EditorApplication.update += Expire;
                AssemblyReloadEvents.beforeAssemblyReload -= Clear;
                AssemblyReloadEvents.beforeAssemblyReload += Clear;
                EditorApplication.quitting -= Clear;
                EditorApplication.quitting += Clear;
                snapshot = null;
            }
            catch (Exception error) when (error is IOException || error is UnauthorizedAccessException)
            {
                // Retention failure never truncates the scan or changes existing live pagination.
                data["referenceCursor"] = null;
                data["freshness"] = "live";
                data["snapshotUnavailable"] = "Temporary snapshot storage is unavailable; continue with live pages.";
            }
            finally
            {
                snapshot?.Dispose();
            }
        }

        internal static JObject Read(string cursor, string scope, int size, int page)
        {
            Expire();
            if (
                cursor == null
                || cursor.Length != Prefix.Length + 32
                || !cursor.StartsWith(Prefix, StringComparison.Ordinal)
                || !Guid.TryParseExact(cursor.Substring(Prefix.Length), "N", out _)
                || !Entries.TryGetValue(cursor, out var node)
            )
                throw new ArgumentException("Reference snapshot is unavailable or expired; capture a new snapshot.");
            Snapshot snapshot = node.Value;
            if (snapshot.Scope != scope)
                throw new ArgumentException("Reference snapshot scope, path or scan budgets do not match.");
            long offset = PaginationBounds.StartIndex(page, size);
            var rows = new JArray();
            using (var reader = new BinaryReader(snapshot.File, Encoding.UTF8, true))
                for (long i = offset; i < snapshot.Offsets.Length && i - offset < size; i++)
                {
                    snapshot.File.Position = snapshot.Offsets[i];
                    using var json = new JsonTextReader(new StringReader(reader.ReadString())) { DateParseHandling = DateParseHandling.None };
                    rows.Add(JObject.Load(json));
                }
            var result = (JObject)snapshot.Metadata.DeepClone();
            result["issues"] = rows;
            result["pageSize"] = size;
            result["pageNumber"] = page;
            result["hasMore"] = offset + rows.Count < snapshot.Offsets.Length;
            ReferenceDiagnosticUtility.PageBytes(result);
            snapshot.Touched = EditorApplication.timeSinceStartup;
            Recent.Remove(node);
            Recent.AddLast(node);
            return result;
        }

        private static void Expire()
        {
            double now = EditorApplication.timeSinceStartup;
            if (now < sweepAt)
                return;
            while (Recent.First != null && now - Recent.First.Value.Touched >= IdleSeconds)
                Remove(Recent.First);
            sweepAt = Recent.First == null ? 0 : Recent.First.Value.Touched + IdleSeconds;
            if (Recent.Count == 0)
                Detach();
        }

        private static void Remove(LinkedListNode<Snapshot> node)
        {
            retainedBytes -= node.Value.File.Length;
            Entries.Remove(node.Value.Cursor);
            Recent.Remove(node);
            node.Value.Dispose();
        }

        private static void Detach()
        {
            EditorApplication.update -= Expire;
            AssemblyReloadEvents.beforeAssemblyReload -= Clear;
            EditorApplication.quitting -= Clear;
        }

        internal static void Clear()
        {
            while (Recent.First != null)
                Remove(Recent.First);
            sweepAt = 0;
            Detach();
        }
    }
}
