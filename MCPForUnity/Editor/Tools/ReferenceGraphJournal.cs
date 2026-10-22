using System;
using System.Collections.Generic;
using System.Linq;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using UnityEditor;

namespace MCPForUnity.Editor.Tools
{
    /// <summary>Editor-owned import evidence; readers never acknowledge/delete another reader's events.</summary>
    [InitializeOnLoad]
    internal static class ReferenceGraphJournal
    {
        private const string StateKey = "MCP.ReferenceGraph.Journal.v1";
        private const string InstanceKey = "MCP.ReferenceGraph.EditorInstance.v1";
        private const string ActiveKey = "MCP.ReferenceGraph.Active.v1";
        private const int RetainedEvents = 65536;
        private static JArray changes = new JArray();
        private static bool active;
        internal static string Epoch { get; private set; }
        internal static long Revision { get; private set; }
        internal static string InstanceId { get; private set; }

        static ReferenceGraphJournal()
        {
            active = SessionState.GetBool(ActiveKey, false);
            InstanceId = SessionState.GetString(InstanceKey, "");
            if (string.IsNullOrEmpty(InstanceId))
            {
                InstanceId = Guid.NewGuid().ToString("N");
                SessionState.SetString(InstanceKey, InstanceId);
            }
            Restore();
            AssemblyReloadEvents.beforeAssemblyReload += Save;
        }

        internal static void Save()
        {
            if (!active)
                return;
            ReferenceGraphCollector.FlushPendingChanges();
            SessionState.SetString(
                StateKey,
                new JObject
                {
                    ["epoch"] = Epoch,
                    ["revision"] = Revision,
                    ["changes"] = changes.DeepClone(),
                }.ToString(Formatting.None)
            );
        }

        internal static void Activate()
        {
            if (active)
                return;
            active = true;
            SessionState.SetBool(ActiveKey, true);
        }

        internal static void Restore()
        {
            try
            {
                string text = SessionState.GetString(StateKey, "");
                if (text.Length > 0)
                {
                    JObject state = JObject.Parse(text);
                    string epoch = state.Value<string>("epoch");
                    long revision = state.Value<long>("revision");
                    var rows = state["changes"] as JArray;
                    if (
                        Guid.TryParseExact(epoch, "N", out _)
                        && revision >= 0
                        && rows != null
                        && rows.Count <= RetainedEvents
                        && rows.Select((row, index) => row.Value<long>("revision") == index + 1L).All(valid => valid)
                        && revision == rows.Count
                    )
                    {
                        Epoch = epoch;
                        Revision = revision;
                        changes = rows;
                        return;
                    }
                }
            }
            catch (Exception)
            {
                // SessionState is a reload optimization. A fresh epoch requires a full census.
            }
            Reset();
        }

        private static void Reset()
        {
            Epoch = Guid.NewGuid().ToString("N");
            Revision = 0;
            changes.Clear();
        }

        internal static void Record(string[] imported, string[] deleted, string[] moved, string[] movedFrom)
        {
            if (!active)
                return;
            var batch = new Dictionary<string, bool>(StringComparer.Ordinal);
            foreach (string path in deleted.Concat(movedFrom))
                if (ReferenceGraphCollector.Admitted(path))
                    batch[path] = true;
            foreach (string path in imported.Concat(moved))
                if (ReferenceGraphCollector.Admitted(path))
                    batch[path] = false;
            if (batch.Count == 0)
                return;
            // This is a journal retention policy, not an asset/workload limit. Every
            // consumer of an expired epoch must reconcile a complete new census.
            if (batch.Count > RetainedEvents - changes.Count)
            {
                Reset();
                return;
            }
            foreach (var item in batch.OrderBy(item => item.Key, StringComparer.Ordinal))
                changes.Add(
                    new JObject
                    {
                        ["revision"] = ++Revision,
                        ["path"] = item.Key,
                        ["deleted"] = item.Value,
                        ["file_stamp"] = item.Value ? null : ReferenceGraphCollector.ImportedFileStamp(item.Key),
                    }
                );
        }

        internal static JObject Read(string epoch, long after, int count)
        {
            bool expired = epoch != Epoch || after < 0 || after > Revision;
            var rows = expired ? new JArray() : new JArray(changes.Skip((int)after).Take(count).Select(row => row.DeepClone()));
            return new JObject
            {
                ["epoch"] = Epoch,
                ["revision"] = Revision,
                ["requires_census"] = expired,
                ["changes"] = rows,
                ["next_after"] = rows.Count > 0 ? rows.Last.Value<long>("revision") : after,
                ["has_more"] = !expired && after + rows.Count < Revision,
            };
        }

        internal static void RecordCatalogChange()
        {
            if (changes.Count == RetainedEvents)
            {
                Reset();
                return;
            }
            changes.Add(new JObject { ["revision"] = ++Revision, ["kind"] = "addressables_catalog" });
        }
    }

    internal sealed class ReferenceGraphImportObserver : AssetPostprocessor
    {
        private static void OnPostprocessAllAssets(string[] importedAssets, string[] deletedAssets, string[] movedAssets, string[] movedFromAssetPaths) =>
            ReferenceGraphJournal.Record(importedAssets, deletedAssets, movedAssets, movedFromAssetPaths);
    }
}
