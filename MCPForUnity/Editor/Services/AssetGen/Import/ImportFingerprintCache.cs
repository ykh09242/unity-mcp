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
        internal static Action FingerprintReadForTests;
        private static ValidatedSource activeValidation;

        // Only the manager holding the validated source's read handle may lend its digest.
        // The scope covers one synchronous importer and never enters the persistent cache.
        internal static IDisposable UseValidatedSource(string path, string fingerprint, Action validateIdentity)
        {
            var scope = new ValidatedSource(Path.GetFullPath(path), fingerprint, validateIdentity, activeValidation);
            activeValidation = scope;
            return scope;
        }

        private sealed class ValidatedSource : IDisposable
        {
            internal readonly string Path;
            internal readonly string Fingerprint;
            internal readonly Action ValidateIdentity;
            private readonly ValidatedSource previous;

            internal ValidatedSource(string path, string fingerprint, Action validateIdentity, ValidatedSource previous)
            {
                Path = path;
                Fingerprint = fingerprint;
                ValidateIdentity = validateIdentity;
                this.previous = previous;
            }

            public void Dispose()
            {
                if (ReferenceEquals(activeValidation, this))
                    activeValidation = previous;
            }
        }

        internal static bool IsUnchanged(string path, out string fingerprint)
        {
            string fullPath = Path.GetFullPath(path);
            GuardedFileSystem.CheckPath(fullPath);
            if (activeValidation != null && activeValidation.Path == fullPath)
            {
                activeValidation.ValidateIdentity();
                fingerprint = activeValidation.Fingerprint;
            }
            else
            {
                FingerprintReadForTests?.Invoke();
                using var stream = File.OpenRead(fullPath);
                using var hash = SHA256.Create();
                fingerprint = Convert.ToBase64String(hash.ComputeHash(stream));
            }
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
