using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Security.Cryptography;
using MCPForUnity.Editor.Helpers;
using UnityEditor;

namespace MCPForUnity.Editor.Services.AssetGen.Import
{
    // Main-thread-only cache of successful imports. Source bytes, not just timestamps, identify retries.
    internal static class ImportFingerprintCache
    {
        private static readonly Dictionary<string, (string Hash, string Guid)> Entries = new(StringComparer.Ordinal);

        internal static bool IsUnchanged(string path, out string fingerprint)
        {
            string fullPath = Path.GetFullPath(path);
            GuardedFileSystem.CheckPath(fullPath);
            using (var stream = File.OpenRead(fullPath))
            using (var hash = SHA256.Create())
                fingerprint = Convert.ToBase64String(hash.ComputeHash(stream));
            return Entries.TryGetValue(path, out var entry)
                && entry.Hash == fingerprint
                && entry.Guid == AssetDatabase.AssetPathToGUID(path)
                && AssetDatabase.LoadMainAssetAtPath(path) != null;
        }

        internal static void Remember(string path, string fingerprint)
        {
            if (Entries.Count >= 256 && !Entries.ContainsKey(path))
                Entries.Remove(Entries.Keys.First());
            Entries[path] = (fingerprint, AssetDatabase.AssetPathToGUID(path));
        }
    }
}
