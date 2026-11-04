using System;
using System.Security.Cryptography;
using System.Text;
using MCPForUnity.Editor.Constants;
using UnityEditor;

namespace MCPForUnity.Editor.Clients
{
    /// <summary>Receipts for entries written explicitly by this editor, never inferred from their shape.</summary>
    internal static class GeneratedClientConfigOwnership
    {
        internal static string Key(string format, string path) => "MCPForUnity.Generated" + format + "Config." + Fingerprint(path);

        internal static bool CanAutomaticallyRefresh(string format, string path, string entry) =>
            EditorPrefs.GetBool(EditorPrefKeys.AutoRegisterEnabled, true) && Owns(format, path, entry);

        internal static string Receipt(string format, string path) => EditorPrefs.HasKey(Key(format, path)) ? EditorPrefs.GetString(Key(format, path)) : null;

        internal static void RestoreReceipt(string format, string path, string receipt)
        {
            if (receipt == null)
                EditorPrefs.DeleteKey(Key(format, path));
            else
                EditorPrefs.SetString(Key(format, path), receipt);
        }

        internal static bool Owns(string format, string path, string entry)
        {
            if (entry == null)
                return false;
            string stored = EditorPrefs.GetString(Key(format, path), string.Empty);
            return !string.IsNullOrEmpty(stored) && stored == Fingerprint(entry);
        }

        internal static void Record(string format, string path, string entry)
        {
            RestoreReceipt(format, path, EntryReceipt(entry));
        }

        internal static string EntryReceipt(string entry) => entry == null ? null : Fingerprint(entry);

        private static string Fingerprint(string value)
        {
            using var sha = SHA256.Create();
            return BitConverter.ToString(sha.ComputeHash(Encoding.UTF8.GetBytes(value))).Replace("-", string.Empty);
        }
    }
}
