using System;
using System.Security.Cryptography;
using System.Text;
using UnityEditor;

namespace MCPForUnity.Editor.Clients
{
    /// <summary>Receipts for entries written explicitly by this editor, never inferred from their shape.</summary>
    internal static class GeneratedClientConfigOwnership
    {
        internal static string Key(string format, string path) => "MCPForUnity.Generated" + format + "Config." + Fingerprint(path);

        internal static bool Owns(string format, string path, string entry)
        {
            if (entry == null)
                return false;
            string stored = EditorPrefs.GetString(Key(format, path), string.Empty);
            return !string.IsNullOrEmpty(stored) && stored == Fingerprint(entry);
        }

        internal static void Record(string format, string path, string entry)
        {
            string key = Key(format, path);
            if (entry == null)
                EditorPrefs.DeleteKey(key);
            else
                EditorPrefs.SetString(key, Fingerprint(entry));
        }

        private static string Fingerprint(string value)
        {
            using var sha = SHA256.Create();
            return BitConverter.ToString(sha.ComputeHash(Encoding.UTF8.GetBytes(value))).Replace("-", string.Empty);
        }
    }
}
