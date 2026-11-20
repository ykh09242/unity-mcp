using System;
using System.Collections.Generic;
using MCPForUnity.Editor.Helpers;
using Newtonsoft.Json.Linq;
using UnityEditor;

namespace MCPForUnity.Editor.Tools
{
    // Main-thread, process-local witnesses. An ordinary import outside the guarded window
    // invalidates the previous witness. Reload and bounded eviction return to unverified.
    internal static class ReferenceGraphImportWitness
    {
        private static readonly Dictionary<string, JObject> Watched = new Dictionary<string, JObject>(StringComparer.Ordinal);
        private static readonly Queue<string> Order = new Queue<string>();
        private static readonly Dictionary<string, bool> InProgress = new Dictionary<string, bool>(StringComparer.Ordinal);

        internal static void Watch(string path)
        {
            if (Watched.ContainsKey(path))
                return;
            if (Watched.Count >= 2048)
                Watched.Remove(Order.Dequeue());
            Order.Enqueue(path);
            Watched[path] = null;
        }

        internal static bool HasWatches => Watched.Count != 0;

        internal static JObject Read(string path) => Watched.TryGetValue(path, out var value) ? (JObject)value?.DeepClone() : null;

        internal static void Changed(string path, bool imported)
        {
            if (Watched.ContainsKey(path))
                Watched[path] = null;
            if (InProgress.ContainsKey(path))
                InProgress[path] = imported;
        }

        internal static void Reimport(string path)
        {
            if (!Watched.ContainsKey(path))
            {
                AssetDatabase.ImportAsset(path, ImportAssetOptions.ForceUpdate);
                return;
            }
            Watched[path] = null;
            JObject opening = TryReadBytes(path);
            InProgress[path] = false;
            try
            {
                AssetDatabase.ImportAsset(
                    path,
                    ImportAssetOptions.ForceUpdate | ImportAssetOptions.ForceSynchronousImport | ImportAssetOptions.DontDownloadFromCacheServer
                );
                if (opening == null || !InProgress[path])
                    return;
                JObject closing = TryReadBytes(path);
                if (closing == null || !JToken.DeepEquals(opening, closing))
                    return;
                closing["guid"] = AssetDatabase.AssetPathToGUID(path);
                closing["import_hash"] = AssetDatabase.GetAssetDependencyHash(path).ToString();
                closing["import_id"] = Guid.NewGuid().ToString("N");
                Watched[path] = closing;
            }
            finally
            {
                InProgress.Remove(path);
            }
        }

        private static JObject TryReadBytes(string path)
        {
            try
            {
                long remaining = 8 * 1024 * 1024;
                string file = AssetPathUtility.GetFullAssetPath(path);
                return new JObject
                {
                    ["asset_sha256"] = ReferenceGraphCommitInputs.Hash(file, ref remaining),
                    ["meta_sha256"] = ReferenceGraphCommitInputs.Hash(file + ".meta", ref remaining),
                };
            }
            catch (Exception)
            {
                // Missing evidence must not prevent an independently authorized import.
                return null;
            }
        }
    }
}
